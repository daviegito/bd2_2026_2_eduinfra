"""Frescor da Gold e SLA (issue #52), sem banco."""

import unittest
from datetime import datetime, timedelta, timezone

import pytest

from eduinfra import cli, frescor, modelo_analitico
from eduinfra.frescor import Medicao, avaliar, duracao, resumo

AGORA = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
SLA = timedelta(hours=25)


def test_gold_em_dia_tem_frescor_zero_mesmo_sem_vistoria_nova_ha_dias():
    medicao = avaliar(AGORA, gold_construida=True, pendentes=0, pendente_desde=None, sla=SLA)

    assert medicao.frescor == timedelta(0)
    assert medicao.dentro_do_sla


def test_frescor_e_a_espera_da_vistoria_pendente_mais_antiga():
    medicao = avaliar(AGORA, True, 3, AGORA - timedelta(hours=2), SLA)

    assert medicao.frescor == timedelta(hours=2)
    assert medicao.vistorias_pendentes == 3
    assert medicao.dentro_do_sla


@pytest.mark.parametrize(
    ("espera", "dentro"),
    [
        (timedelta(hours=25), True),  # no limite
        (timedelta(hours=25, seconds=1), False),
    ],
)
def test_limite_do_sla(espera, dentro):
    assert avaliar(AGORA, True, 1, AGORA - espera, SLA).dentro_do_sla is dentro


def test_gold_nao_construida_viola_o_sla():
    medicao = avaliar(AGORA, gold_construida=False, pendentes=None, pendente_desde=None, sla=SLA)

    assert medicao.frescor is None
    assert not medicao.dentro_do_sla
    assert "construir-gold" in resumo(medicao)


def test_relogio_adiantado_na_origem_nao_gera_frescor_negativo():
    medicao = avaliar(AGORA, True, 1, AGORA + timedelta(seconds=3), SLA)

    assert medicao.frescor == timedelta(0)


def test_duracao_em_segundos_minutos_e_horas():
    assert duracao(timedelta(seconds=4.25)) == "4,2 s"
    assert duracao(timedelta(minutes=12, seconds=30)) == "12,5 min"
    assert duracao(timedelta(hours=25)) == "25,0 h"


def test_resumo_aponta_a_violacao():
    medicao = avaliar(AGORA, True, 1, AGORA - timedelta(seconds=7), timedelta(seconds=2))

    assert resumo(medicao) == "1 vistoria(s) fora da Gold, a mais antiga esperando há 7,0 s: FORA DO SLA de 2,0 s"


def _medicao(dentro: bool) -> Medicao:
    return Medicao(AGORA, True, 1, AGORA, timedelta(0), SLA, dentro)


@pytest.fixture
def banco_falso(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://falso")
    chamadas = []

    def medir(dsn, sla=frescor.SLA_PADRAO, comando="medir-frescor"):
        chamadas.append((comando, sla))
        return _medicao(dentro=sla >= SLA)

    monkeypatch.setattr(frescor, "medir", medir)
    return chamadas


def test_medir_frescor_sai_com_codigo_1_fora_do_sla(banco_falso):
    assert cli.main(["medir-frescor"]) == 0
    assert cli.main(["medir-frescor", "--sla-horas", "1"]) == 1
    assert banco_falso == [("medir-frescor", SLA), ("medir-frescor", timedelta(hours=1))]


def test_construir_gold_registra_o_frescor(banco_falso, monkeypatch):
    fonte = modelo_analitico.ConferenciaDaFonte(versao_delta=1, na_fonte=0, na_origem=0, so_na_origem=0, so_na_fonte=0)
    monkeypatch.setattr(modelo_analitico, "construir", lambda configuracao: modelo_analitico.ResumoGold({}, 0.0, fonte))
    monkeypatch.setattr(cli, "_auditar_gold", lambda: True)

    assert cli.main(["construir-gold"]) == 0
    assert banco_falso == [("construir-gold", frescor.SLA_PADRAO)]


def test_construir_gold_sai_com_codigo_1_quando_a_auditoria_falha(banco_falso, monkeypatch):
    fonte = modelo_analitico.ConferenciaDaFonte(versao_delta=1, na_fonte=0, na_origem=0, so_na_origem=0, so_na_fonte=0)
    monkeypatch.setattr(modelo_analitico, "construir", lambda configuracao: modelo_analitico.ResumoGold({}, 0.0, fonte))
    monkeypatch.setattr(cli, "_auditar_gold", lambda: False)

    assert cli.main(["construir-gold"]) == 1
