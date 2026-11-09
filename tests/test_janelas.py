"""Janelas mensais e tolerância a atraso, sem banco. A fonte que elas completam está em test_fonte_de_eventos.py."""

from datetime import datetime, timedelta, timezone

import pytest

from eduinfra.janelas import TOLERANCIA, EventoNovo, _expandir, _faixas, fechamento, limites, mes_de


def utc(*partes: int) -> datetime:
    return datetime(*partes, tzinfo=timezone.utc)


def test_janela_e_o_mes_em_que_a_vistoria_ocorreu():
    assert mes_de(utc(2026, 10, 9, 15)) == "2026-10"
    assert mes_de(datetime(2026, 10, 31, 23, tzinfo=timezone(timedelta(hours=-3)))) == "2026-11"


def test_limites_viram_o_ano_em_dezembro():
    assert limites("2026-12") == (utc(2026, 12, 1), utc(2027, 1, 1))


def test_janela_fecha_tolerancia_depois_do_fim_do_mes():
    assert fechamento("2026-08") == utc(2026, 9, 1) + TOLERANCIA


@pytest.mark.parametrize(
    ("registrado_em", "dentro"),
    [
        (utc(2026, 10, 12), True),  # três dias de atraso, mês ainda aberto
        (utc(2026, 11, 8), True),  # no limite da tolerância
        (utc(2026, 11, 8, 0, 0, 1), False),  # um segundo depois de fechar
    ],
)
def test_tolerancia_a_atraso(registrado_em, dentro):
    evento = EventoNovo(vistoria_id=1, mes="2026-10", ocorrido_em=utc(2026, 10, 9), registrado_em=registrado_em)

    assert evento.dentro_da_tolerancia is dentro


@pytest.mark.parametrize(
    "identificadores",
    [[], [5], list(range(1, 181_066)), [1, 2, 3, 10, 11, 40]],
    ids=["nenhum", "um", "carga-do-censo", "com-buracos"],
)
def test_vistas_na_margem_voltam_iguais_do_controle(identificadores):
    faixas = _faixas(identificadores)

    assert _expandir(faixas) == set(identificadores)
    assert len(faixas) <= 3
