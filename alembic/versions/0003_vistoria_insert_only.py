"""Núcleo transacional insert-only de vistorias

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-22
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# ocorrido_em é quando a realidade mudou na escola; registrado_em é quando o
# dado bateu no banco (ADR 0001).
CRIACAO = [
    """
    create table tecnico (
        id bigint generated always as identity primary key,
        matricula text not null unique,
        nome text not null,
        lotacao_uf char(2) not null,
        ativo boolean not null default true,
        registrado_em timestamptz not null default now()
    )
    """,
    "comment on table tecnico is 'Operador do aplicativo de vistorias. Contém dado pessoal: não exportar para a camada analítica sem pseudonimização.'",
    """
    create table vistoria (
        id bigint generated always as identity primary key,
        co_entidade integer not null references escola (co_entidade),
        tecnico_id bigint not null references tecnico (id),
        origem text not null,
        ocorrido_em timestamptz not null,
        registrado_em timestamptz not null default now(),
        observacao text,
        constraint vistoria_origem_valida check (
            origem in ('censo_escolar', 'vistoria_presencial', 'autodeclaracao_escola')
        ),
        constraint vistoria_evento_antes_do_registro check (ocorrido_em <= registrado_em)
    )
    """,
    "create index vistoria_por_escola_no_tempo on vistoria (co_entidade, ocorrido_em desc, id desc)",
    "create index vistoria_por_data_do_evento on vistoria (ocorrido_em desc)",
    # Torna a carga anual idempotente.
    """
    create unique index vistoria_censo_uma_por_escola_e_data
        on vistoria (co_entidade, ocorrido_em)
        where origem = 'censo_escolar'
    """,
    """
    create table vistoria_item (
        vistoria_id bigint not null references vistoria (id) on delete restrict,
        item_codigo text not null references item_infraestrutura (codigo),
        situacao situacao_item not null,
        quantidade integer,
        primary key (vistoria_id, item_codigo),
        constraint vistoria_item_quantidade_nao_negativa check (quantidade is null or quantidade >= 0)
    )
    """,
    "create index vistoria_item_por_item on vistoria_item (item_codigo, situacao)",
    """
    create or replace function impedir_alteracao_de_evento() returns trigger
    language plpgsql as $$
    begin
        raise exception
            'a tabela % é insert-only e o comando % foi bloqueado; registre uma nova vistoria em vez de alterar o passado',
            tg_table_name, tg_op
            using errcode = 'restrict_violation';
    end;
    $$
    """,
]

GATILHOS = [
    (tabela, operacao)
    for tabela in ("vistoria", "vistoria_item")
    for operacao in ("update", "delete", "truncate")
]

REMOCAO = [
    "drop table vistoria_item",
    "drop table vistoria",
    "drop table tecnico",
    "drop function impedir_alteracao_de_evento",
]


def upgrade() -> None:
    for comando in CRIACAO:
        op.execute(comando)

    for tabela, operacao in GATILHOS:
        op.execute(
            f"""
            create trigger {tabela}_sem_{operacao}
                before {operacao} on {tabela}
                for each statement execute function impedir_alteracao_de_evento()
            """
        )


def downgrade() -> None:
    for comando in REMOCAO:
        op.execute(comando)
