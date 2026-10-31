"""Inserção de vistorias sintéticas na origem, para medições e demonstrações da E2.

A origem é insert-only: o que este módulo grava não sai mais. Use em banco de
desenvolvimento. As vistorias levam a observação informada, que as identifica.
"""

from datetime import datetime

import psycopg


def garantir_tecnico(conexao: psycopg.Connection, matricula: str) -> int:
    conexao.execute(
        """
        insert into tecnico (matricula, nome, lotacao_uf) values (%s, 'Técnico de demonstração', 'DF')
        on conflict (matricula) do nothing
        """,
        (matricula,),
    )
    return conexao.execute("select id from tecnico where matricula = %s", (matricula,)).fetchone()[0]


def inserir_vistorias(conexao: psycopg.Connection, quantidade: int, matricula: str, observacao: str) -> None:
    tecnico = garantir_tecnico(conexao, matricula)
    with conexao.transaction():
        conexao.execute(
            """
            with nova as (
                insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em, observacao)
                select co_entidade, %s, 'vistoria_presencial', now() - interval '1 hour', %s
                from escola order by co_entidade limit %s
                returning id
            )
            insert into vistoria_item (vistoria_id, item_codigo, situacao)
            select nova.id, item.codigo, 'existente' from nova cross join item_infraestrutura item
            """,
            (tecnico, observacao, quantidade),
        )


def inserir_vistoria(conexao: psycopg.Connection, matricula: str, observacao: str, ocorrido_em: datetime) -> int:
    tecnico = garantir_tecnico(conexao, matricula)
    with conexao.transaction():
        return conexao.execute(
            """
            with nova as (
                insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em, observacao)
                select min(co_entidade), %s, 'vistoria_presencial', %s, %s from escola
                returning id
            ),
            itens as (
                insert into vistoria_item (vistoria_id, item_codigo, situacao)
                select nova.id, item.codigo, 'existente' from nova cross join item_infraestrutura item
            )
            select id from nova
            """,
            (tecnico, ocorrido_em, observacao),
        ).fetchone()[0]


def contar_itens_por_vistoria(conexao: psycopg.Connection) -> int:
    return conexao.execute("select count(*) from item_infraestrutura").fetchone()[0]


def contar_itens_da_origem(conexao: psycopg.Connection) -> int:
    return conexao.execute("select count(*) from vistoria_item").fetchone()[0]
