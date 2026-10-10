"""Publicação lógica das tabelas de evento para o CDC da E2

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-10
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# Só vistoria e vistoria_item entram: são as tabelas de evento (ADR 0001).
# publish = 'insert' porque UPDATE, DELETE e TRUNCATE são bloqueados por
# gatilho; publicá-los seria prometer um fluxo que nunca acontece.
#
# REPLICA IDENTITY fica em DEFAULT (chave primária). Como não há UPDATE nem
# DELETE, a identidade da linha antiga nunca é emitida; FULL só aumentaria o WAL.
#
# A publicação existe mesmo com wal_level=replica; o que muda é a criação do
# slot lógico, que falha até o servidor subir com wal_level=logical.
CRIACAO = """
create publication eduinfra_cdc
    for table vistoria, vistoria_item
    with (publish = 'insert')
"""


def upgrade() -> None:
    op.execute(CRIACAO)


def downgrade() -> None:
    op.execute("drop publication eduinfra_cdc")
