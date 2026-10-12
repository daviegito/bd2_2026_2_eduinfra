"""Janelas mensais, tolerância a atraso e regravação idempotente da partição, sem banco."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eduinfra.janelas import (
    TOLERANCIA,
    EventoNovo,
    fechamento,
    gravar_particao,
    impressao_da_particao,
    limites,
    mes_de,
)

CABECALHO = "vistoria_id,co_entidade,origem,ocorrido_em,registrado_em,item_codigo,situacao,quantidade"
LINHAS = [
    "10,53002580,vistoria_presencial,2026-10-09 15:00:00+00,2026-10-12 15:00:00+00,biblioteca,existente,",
    "10,53002580,vistoria_presencial,2026-10-09 15:00:00+00,2026-10-12 15:00:00+00,lab_ciencias,existente,",
    "11,53002581,vistoria_presencial,2026-10-01 09:00:00+00,2026-10-01 09:30:00+00,biblioteca,inexistente,",
]


def escrever_csv(pasta: Path, linhas: list[str]) -> Path:
    arquivo = pasta / "eventos.csv"
    arquivo.write_text("\n".join([CABECALHO, *linhas]) + "\n", encoding="utf-8")
    return arquivo


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


def test_regravar_duas_vezes_da_a_mesma_particao(tmp_path):
    destino = tmp_path / "ocorrido_mes=2026-10" / "eventos.parquet"

    primeira = gravar_particao(escrever_csv(tmp_path, LINHAS), destino, "2026-10", tmp_path)
    segunda = gravar_particao(escrever_csv(tmp_path, LINHAS), destino, "2026-10", tmp_path)

    assert primeira == segunda
    assert primeira.linhas == 3
    assert impressao_da_particao(destino) == primeira.impressao


def test_impressao_nao_depende_da_ordem_das_linhas(tmp_path):
    destino = tmp_path / "eventos.parquet"

    em_ordem = gravar_particao(escrever_csv(tmp_path, LINHAS), destino, "2026-10", tmp_path)
    invertida = gravar_particao(escrever_csv(tmp_path, LINHAS[::-1]), destino, "2026-10", tmp_path)

    assert em_ordem.impressao == invertida.impressao


def test_impressao_muda_se_um_valor_muda(tmp_path):
    destino = tmp_path / "eventos.parquet"
    alterada = [LINHAS[0].replace("existente", "em_obra"), *LINHAS[1:]]

    original = gravar_particao(escrever_csv(tmp_path, LINHAS), destino, "2026-10", tmp_path)
    corrigida = gravar_particao(escrever_csv(tmp_path, alterada), destino, "2026-10", tmp_path)

    assert original.impressao != corrigida.impressao


def test_janela_sem_eventos_remove_a_particao(tmp_path):
    destino = tmp_path / "ocorrido_mes=2026-10" / "eventos.parquet"
    gravar_particao(escrever_csv(tmp_path, LINHAS), destino, "2026-10", tmp_path)

    vazia = gravar_particao(escrever_csv(tmp_path, []), destino, "2026-10", tmp_path)

    assert vazia.linhas == 0
    assert not destino.parent.exists()


def test_falha_na_conversao_preserva_a_particao_anterior(tmp_path):
    destino = tmp_path / "eventos.parquet"
    anterior = gravar_particao(escrever_csv(tmp_path, LINHAS), destino, "2026-10", tmp_path)

    with pytest.raises(Exception):
        gravar_particao(escrever_csv(tmp_path, ["isto,não,é,uma,linha,válida"]), destino, "2026-10", tmp_path)

    assert impressao_da_particao(destino) == anterior.impressao
