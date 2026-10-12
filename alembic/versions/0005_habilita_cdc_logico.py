"""Habilita a publicação de CDC da origem OLTP.

Revision ID: 0005
Revises: 0004
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PUBLICATION = "eduinfra_cdc"


def upgrade() -> None:
    op.execute("ALTER TABLE public.vistoria REPLICA IDENTITY DEFAULT")
    op.execute("ALTER TABLE public.vistoria_item REPLICA IDENTITY DEFAULT")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_publication
                WHERE pubname = '{PUBLICATION}'
            ) THEN
                CREATE PUBLICATION {PUBLICATION}
                    FOR TABLE public.vistoria, public.vistoria_item;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(f"DROP PUBLICATION IF EXISTS {PUBLICATION}")
