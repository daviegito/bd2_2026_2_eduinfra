"""Slot com WAL não publicado e commits concorrentes; banco e destino descartáveis."""

from datetime import date
from uuid import uuid4

import psycopg
import pytest

from eduinfra import cdc, eventos, fluxo_cdc
from eduinfra.configuracao import Configuracao
from eduinfra.pgoutput import interpretar_lsn
from tests.test_modelo_analitico import dsn, vistoria

pytestmark = pytest.mark.integracao


@pytest.fixture
def configuracao(dsn, tmp_path):
    return Configuracao(dsn, "https://example.invalid", 2024, date(2024, 5, 29), tmp_path, tmp_path / "alembic.ini")


@pytest.fixture
def conexao(dsn):
    with psycopg.connect(dsn, autocommit=True) as aberta:
        yield aberta


@pytest.fixture
def slot(conexao):
    nome = "teste_ocioso_" + uuid4().hex[:12]
    cdc.garantir_slot(conexao, nome)
    try:
        yield nome
    finally:
        cdc.remover_slot(conexao, nome)


def posicoes(conexao, slot):
    linha = conexao.execute(
        "select confirmed_flush_lsn::text, restart_lsn::text from pg_replication_slots where slot_name = %s", (slot,)
    ).fetchone()
    return tuple(interpretar_lsn(valor) for valor in linha)


def test_wal_de_tabela_nao_publicada_avanca_sem_inventar_eventos(configuracao, conexao, slot):
    confirmado_antes, _ = posicoes(conexao, slot)
    conexao.execute("create table fora_da_publicacao as select generate_series(1, 50000) as id")

    resultado = fluxo_cdc.passar_uma_vez(configuracao, slot=slot)

    confirmado_depois, _ = posicoes(conexao, slot)
    assert resultado.transacoes == resultado.itens_lidos == resultado.itens_inseridos == 0
    assert confirmado_depois > confirmado_antes
    assert confirmado_depois <= resultado.lsn_confirmado


def test_restart_lsn_avanca_apos_checkpoint_e_passadas_sem_eventos(configuracao, conexao, slot):
    _, reinicio_antes = posicoes(conexao, slot)
    conexao.execute("create table outra_tabela as select generate_series(1, 10000) as id")
    conexao.execute("checkpoint")
    for _ in range(3):
        fluxo_cdc.passar_uma_vez(configuracao, slot=slot)
    _, reinicio_depois = posicoes(conexao, slot)
    assert reinicio_depois > reinicio_antes


def test_commit_entre_captura_do_limite_e_peek_fica_para_a_proxima_passada(configuracao, conexao, slot, monkeypatch):
    capturar = cdc.posicao_persistida
    capturados = []

    def capturar_e_confirmar_vistoria(leitor):
        limite = capturar(leitor)
        capturados.append(limite)
        vistoria(configuracao.dsn, 1, "2024-06-01", {"biblioteca": "existente"})
        return limite

    monkeypatch.setattr(cdc, "posicao_persistida", capturar_e_confirmar_vistoria)
    primeira = fluxo_cdc.passar_uma_vez(configuracao, slot=slot)
    monkeypatch.setattr(cdc, "posicao_persistida", capturar)
    segunda = fluxo_cdc.passar_uma_vez(configuracao, slot=slot)
    terceira = fluxo_cdc.passar_uma_vez(configuracao, slot=slot)

    assert primeira.transacoes == 0
    assert primeira.lsn_confirmado == capturados[0]
    assert segunda.itens_inseridos == 1
    assert terceira.itens_inseridos == 0
    assert eventos.contar(configuracao) == 1


def test_transacao_aberta_durante_passada_vazia_chega_apos_o_commit(configuracao, conexao, slot):
    with psycopg.connect(configuracao.dsn) as escritora:
        id_ = escritora.execute(
            "insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em) "
            "values (1, 1, 'vistoria_presencial', '2024-06-01') returning id"
        ).fetchone()[0]
        escritora.execute("insert into vistoria_item values (%s, 'biblioteca', 'existente', null)", (id_,))
        primeira = fluxo_cdc.passar_uma_vez(configuracao, slot=slot)
        assert primeira.transacoes == 0
        escritora.commit()

    segunda = fluxo_cdc.passar_uma_vez(configuracao, slot=slot)
    terceira = fluxo_cdc.passar_uma_vez(configuracao, slot=slot)
    assert segunda.itens_inseridos == 1
    assert terceira.itens_inseridos == 0
    assert eventos.contar(configuracao) == 1


def test_limite_de_mudancas_nao_confirma_transacoes_que_nao_foram_entregues(configuracao, conexao, slot):
    for escola in [1, 2, 3]:
        vistoria(configuracao.dsn, escola, "2024-06-01", {"biblioteca": "existente"})
    primeira = fluxo_cdc.passar_uma_vez(configuracao, limite_de_mudancas=1, slot=slot)
    assert primeira.transacoes == 1
    segunda = fluxo_cdc.passar_uma_vez(configuracao, slot=slot)
    assert primeira.itens_inseridos + segunda.itens_inseridos == 3
    assert eventos.contar(configuracao) == 3


def test_falha_antes_da_confirmacao_ainda_reentrega_sem_duplicar(configuracao, conexao, slot):
    vistoria(configuracao.dsn, 1, "2024-06-01", {"biblioteca": "existente"})
    with pytest.raises(fluxo_cdc.FalhaSimulada):
        fluxo_cdc.passar_uma_vez(configuracao, slot=slot, falhar_antes_de_confirmar=True)
    retomada = fluxo_cdc.passar_uma_vez(configuracao, slot=slot)
    assert retomada.itens_lidos == 1
    assert retomada.itens_inseridos == 0
    assert eventos.contar(configuracao) == 1
