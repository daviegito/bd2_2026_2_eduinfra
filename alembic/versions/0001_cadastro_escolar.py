"""Cadastro geográfico e escolar

Revision ID: 0001
Revises:
Create Date: 2026-09-22
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Único ponto do esquema onde UPDATE é permitido: cadastro corrige digitação,
# não registra fato do mundo.
CRIACAO = [
    """
    create table municipio (
        co_municipio integer primary key,
        no_municipio text not null,
        sg_uf char(2) not null,
        co_uf smallint not null,
        constraint municipio_uf_valida check (co_uf between 11 and 53)
    )
    """,
    """
    create table escola (
        co_entidade integer primary key,
        no_entidade text not null,
        co_municipio integer not null references municipio (co_municipio),
        tp_dependencia smallint not null,
        tp_localizacao smallint not null,
        ano_censo_referencia smallint not null,
        registrado_em timestamptz not null default now(),
        constraint escola_dependencia_valida check (tp_dependencia between 1 and 4),
        constraint escola_localizacao_valida check (tp_localizacao in (1, 2)),
        constraint escola_ano_plausivel check (ano_censo_referencia between 2007 and 2100)
    )
    """,
    "comment on column escola.tp_dependencia is '1 federal, 2 estadual, 3 municipal, 4 privada (dicionário do Censo Escolar)'",
    "comment on column escola.tp_localizacao is '1 urbana, 2 rural (dicionário do Censo Escolar)'",
    "create index escola_por_municipio on escola (co_municipio)",
    "create index escola_por_dependencia on escola (tp_dependencia, tp_localizacao)",
]

REMOCAO = [
    "drop table escola",
    "drop table municipio",
]


def upgrade() -> None:
    for comando in CRIACAO:
        op.execute(comando)


def downgrade() -> None:
    for comando in REMOCAO:
        op.execute(comando)
