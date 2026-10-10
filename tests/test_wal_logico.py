"""Pré-requisitos do CDC da E2: WAL lógico ligado e publicação das tabelas de evento."""

import os

import psycopg
import pytest

pytestmark = pytest.mark.integracao

DSN = os.environ.get("DATABASE_URL")

SLOT_DE_TESTE = "teste_wal_logico"


@pytest.fixture(scope="module")
def conexao():
    if not DSN:
        pytest.skip("DATABASE_URL não definida")

    with psycopg.connect(DSN, autocommit=True) as aberta:
        yield aberta


def test_wal_level_e_logical(conexao):
    nivel = conexao.execute("show wal_level").fetchone()[0]

    assert nivel == "logical", (
        "o banco precisa subir com wal_level=logical (veja o serviço `banco` no docker-compose.yml)"
    )


def test_publicacao_cobre_so_as_tabelas_de_evento(conexao):
    tabelas = {
        linha[0]
        for linha in conexao.execute(
            "select tablename from pg_publication_tables where pubname = 'eduinfra_cdc'"
        ).fetchall()
    }

    assert tabelas == {"vistoria", "vistoria_item"}


def test_publicacao_emite_somente_insert(conexao):
    pub = conexao.execute(
        "select pubinsert, pubupdate, pubdelete, pubtruncate from pg_publication where pubname = 'eduinfra_cdc'"
    ).fetchone()

    assert pub == (True, False, False, False)


def test_slot_logico_pode_ser_criado_e_removido(conexao):
    conexao.execute(
        "select pg_drop_replication_slot(slot_name) from pg_replication_slots where slot_name = %s",
        (SLOT_DE_TESTE,),
    )

    try:
        conexao.execute(
            "select pg_create_logical_replication_slot(%s, 'pgoutput')", (SLOT_DE_TESTE,)
        )
        existe = conexao.execute(
            "select count(*) from pg_replication_slots where slot_name = %s", (SLOT_DE_TESTE,)
        ).fetchone()[0]
        assert existe == 1
    finally:
        conexao.execute(
            "select pg_drop_replication_slot(slot_name) from pg_replication_slots where slot_name = %s",
            (SLOT_DE_TESTE,),
        )
