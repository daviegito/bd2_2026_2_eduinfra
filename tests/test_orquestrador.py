"""Grafo, retry, agenda e partições do orquestrador Dagster, sem banco."""

import os
import subprocess

import pytest
from dagster import Backoff, DefaultScheduleStatus, Definitions, Failure, materialize

from eduinfra import orquestrador
from eduinfra.orquestrador import (
    agenda,
    defs,
    ingerir,
    migrar,
    processar_janelas,
    publicar_analitica,
    reprocessar_janela,
)


@pytest.fixture
def chamadas(monkeypatch):
    registro = []

    def run_falso(comando, env):
        registro.append(" ".join(comando[3:]))
        return subprocess.CompletedProcess(comando, 0)

    monkeypatch.setattr(orquestrador.subprocess, "run", run_falso)
    return registro


def test_definicoes_carregam():
    Definitions.validate_loadable(defs)


def test_pipeline_roda_cada_etapa_depois_das_suas_dependencias(chamadas):
    resultado = materialize([migrar, ingerir, publicar_analitica, processar_janelas])

    assert resultado.success
    assert chamadas[:2] == ["migrar", "ingerir"]
    assert set(chamadas[2:]) == {"publicar-analitica", "processar-janelas"}


def test_backfill_passa_o_mes_da_particao_para_a_cli(chamadas):
    resultado = materialize([reprocessar_janela], partition_key="2024-05-01")

    assert resultado.success
    assert chamadas == ["reprocessar-janela --mes 2024-05"]


def test_comando_que_falha_vira_falha_que_o_retry_repete(monkeypatch):
    monkeypatch.setattr(orquestrador.subprocess, "run", lambda comando, env: subprocess.CompletedProcess(comando, 1))

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
