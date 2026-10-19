"""Manifesto das execuções de ingestão

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-10
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# Uma linha por ingestão concluída. É gravada na mesma transação da carga:
# se a carga falha, o manifesto também não existe, então "está no manifesto"
# significa "foi carregado por inteiro".
CRIACAO = [
    """
    create table ingestao_execucao (
        id bigint generated always as identity primary key,
        ano_censo smallint not null,
        arquivo_origem text not null,
        hash_sha256 char(64) not null,
        forcada boolean not null default false,
        municipios_inseridos integer not null,
        escolas_inseridas integer not null,
        vistorias_inseridas integer not null,
        itens_inseridos integer not null,
        iniciado_em timestamptz not null,
        concluido_em timestamptz not null default now()
    )
    """,
    "create index ingestao_execucao_por_ano_e_hash on ingestao_execucao (ano_censo, hash_sha256)",
    "comment on table ingestao_execucao is 'Manifesto da ingestão em lote: ano, hash do arquivo de origem e linhas inseridas por execução concluída.'",
]


def upgrade() -> None:
    for comando in CRIACAO:
        op.execute(comando)


def downgrade() -> None:
    op.execute("drop table ingestao_execucao")
