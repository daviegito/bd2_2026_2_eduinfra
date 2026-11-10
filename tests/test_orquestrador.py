"""Grafo, retry, agenda e partições do orquestrador Dagster, sem banco."""

import importlib.util
import os
import subprocess

import pytest
from dagster import Backoff, DefaultScheduleStatus, Definitions, Failure, materialize

from eduinfra import orquestrador
from eduinfra.orquestrador import (
    ETAPAS_DO_PIPELINE,
    agenda,
    construir_gold,
    defs,
    medir_frescor,
    processar_janelas,
    reprocessar_janela,
    testar_gold,
)


@pytest.fixture
def chamadas(monkeypatch):
    registro = []

    def run_falso(comando, env, cwd):
        assert cwd == orquestrador.RAIZ
        registro.append("dbt" if comando[1] == str(orquestrador.TRANSFORMACAO) else " ".join(comando[3:]))
        return subprocess.CompletedProcess(comando, 0)

    monkeypatch.setattr(orquestrador.subprocess, "run", run_falso)
    return registro


def test_definicoes_carregam():
    Definitions.validate_loadable(defs)


def test_pipeline_roda_cada_etapa_depois_das_suas_dependencias(chamadas):
    resultado = materialize(ETAPAS_DO_PIPELINE)

    assert resultado.success
    assert chamadas[:2] == ["migrar", "ingerir"]
    assert set(chamadas) == {
        "migrar",
        "ingerir",
        "publicar-analitica",
        "publicar-analitico",
        "processar-janelas",
        "dbt",
        "tests.test_e3_gold",
        "medir-frescor",
    }
    ordem = chamadas.index
    assert ordem("publicar-analitico") < ordem("processar-janelas") < ordem("dbt") < ordem("medir-frescor")
    assert ordem("dbt") < ordem("tests.test_e3_gold")


def test_gold_e_construida_pelo_dbt_e_nao_pelo_construir_gold(chamadas):
    assert orquestrador.TRANSFORMACAO.is_file()

    materialize([construir_gold])

    assert chamadas == ["dbt"]


def test_testes_de_dados_rodam_depois_do_dbt_e_sem_retry(chamadas):
    assert importlib.util.find_spec("tests.test_e3_gold") is not None
    assert testar_gold.op.retry_policy is None

    materialize([testar_gold])

    assert chamadas == ["tests.test_e3_gold"]


def test_gold_e_frescor_entram_na_execucao_agendada():
    assert {construir_gold.key, testar_gold.key, medir_frescor.key} <= set(agenda.job.selection.resolve(ETAPAS_DO_PIPELINE))


def test_frescor_fora_do_sla_falha_sem_retry():
    assert medir_frescor.op.retry_policy is None
    assert construir_gold.op.retry_policy is orquestrador.RETRY
    assert processar_janelas.op.retry_policy is orquestrador.RETRY


def test_backfill_passa_o_mes_da_particao_para_a_cli(chamadas):
    resultado = materialize([reprocessar_janela], partition_key="2024-05-01")

    assert resultado.success
    assert chamadas == ["reprocessar-janela --mes 2024-05"]


def test_comando_que_falha_vira_falha_que_o_retry_repete(monkeypatch):
    monkeypatch.setattr(orquestrador.subprocess, "run", lambda comando, env, cwd: subprocess.CompletedProcess(comando, 1))

    with pytest.raises(Failure) as falha:
        orquestrador.rodar("migrar")

    assert falha.value.allow_retries


def test_retry_tem_limite_e_espera_crescente():
    assert orquestrador.RETRY.max_retries == 3
    assert orquestrador.RETRY.backoff is Backoff.EXPONENTIAL
    assert [orquestrador.RETRY.calculate_delay(tentativa) for tentativa in (1, 2, 3)] == [30, 90, 210]


def test_agenda_liga_sozinha_no_horario_de_brasilia():
    assert agenda.default_status is DefaultScheduleStatus.RUNNING
    assert agenda.execution_timezone == "America/Sao_Paulo"
    assert agenda.cron_schedule == os.environ.get("AGENDA_ORQUESTRADOR", "0 3 * * *")
