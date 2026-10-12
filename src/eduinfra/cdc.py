"""Leitura do slot de replicação lógica da origem (CDC da E2).

O consumo é em duas fases: espiar as transações pendentes e, só depois que o
destino as gravou, avançar o slot. Se o processo cai entre as duas fases, as
mesmas transações voltam na próxima leitura. Isso dá entrega no mínimo uma vez;
a deduplicação no destino (eventos.py) completa o efeito de exatamente uma vez.
"""

import logging
from dataclasses import dataclass

import psycopg

from eduinfra.pgoutput import Transacao, decodificar, formatar_lsn

log = logging.getLogger(__name__)

SLOT_PADRAO = "eduinfra_cdc"
PUBLICACAO_PADRAO = "eduinfra_cdc"
PLUGIN = "pgoutput"


@dataclass(frozen=True)
class EstadoDoSlot:
    existe: bool
    situacao_do_wal: str | None
    atraso_em_bytes: int
    wal_retido_em_bytes: int


def garantir_slot(conexao: psycopg.Connection, slot: str = SLOT_PADRAO) -> bool:
    existente = conexao.execute(
        "select database, database = current_database() from pg_replication_slots where slot_name = %s",
        (slot,),
    ).fetchone()
    if existente and not existente[1]:
        raise RuntimeError(
            f"o slot {slot} pertence ao banco {existente[0]}; slots são do cluster, então outro banco precisa de outro nome"
        )
    if existente:
        return False

    conexao.execute("select pg_create_logical_replication_slot(%s, %s)", (slot, PLUGIN))
    log.info("slot %s criado; mudanças anteriores a este instante não chegam pelo CDC", slot)
    return True


def remover_slot(conexao: psycopg.Connection, slot: str = SLOT_PADRAO) -> None:
    conexao.execute(
        "select pg_drop_replication_slot(slot_name) from pg_replication_slots where slot_name = %s",
        (slot,),
    )


def ler_transacoes(
    conexao: psycopg.Connection,
    slot: str = SLOT_PADRAO,
    publicacao: str = PUBLICACAO_PADRAO,
    limite_de_mudancas: int | None = None,
) -> list[Transacao]:
    linhas = conexao.execute(
        """
        select data from pg_logical_slot_peek_binary_changes(
            %s, null, %s, 'proto_version', '1', 'publication_names', %s)
        """,
        (slot, limite_de_mudancas, publicacao),
    ).fetchall()

    return decodificar(bytes(linha[0]) for linha in linhas)


def confirmar(conexao: psycopg.Connection, lsn_fim: int, slot: str = SLOT_PADRAO) -> None:
    conexao.execute("select pg_replication_slot_advance(%s, %s::pg_lsn)", (slot, formatar_lsn(lsn_fim)))


def estado_do_slot(conexao: psycopg.Connection, slot: str = SLOT_PADRAO) -> EstadoDoSlot:
    linha = conexao.execute(
        """
        select wal_status,
               coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn), 0)::bigint,
               coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn), 0)::bigint
        from pg_replication_slots
        where slot_name = %s
        """,
        (slot,),
    ).fetchone()
    if linha is None:
        return EstadoDoSlot(False, None, 0, 0)

    return EstadoDoSlot(True, linha[0], linha[1], linha[2])
