"""Publica todas as operações de vistoria e vistoria_item no CDC

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-31
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

PUBLICACAO = "eduinfra_cdc"

# Duas versões da 0005 circularam com a mesma revisão: a do commit 703af55 tinha
# publish = 'insert' e a integrada publica tudo, então bancos na mesma revisão divergem.
# Só com insert, um UPDATE que desvia dos gatilhos nem chega ao slot e a contagem por
# operação daria zero sempre (ADR 0007).


def upgrade() -> None:
    op.execute(f"alter publication {PUBLICACAO} set (publish = 'insert, update, delete, truncate')")


def downgrade() -> None:
    # A revisão 0006 não define um estado único de publicação, então não há para onde voltar.
    pass
