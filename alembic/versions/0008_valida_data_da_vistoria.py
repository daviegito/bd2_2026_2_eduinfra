"""Limita ocorrido_em ao intervalo finito representável pelo Python, em UTC.

Revision ID: 0008
Revises: 0007
"""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

REGRA = "vistoria_ocorrido_em_representavel"
CONDICAO = """isfinite(ocorrido_em)
    and ocorrido_em >= timestamptz '0001-01-01 00:00:00+00'
    and ocorrido_em < timestamptz '10000-01-01 00:00:00+00'"""


def upgrade() -> None:
    # O diagnóstico e a validação precisam observar o mesmo histórico, sem novas inserções.
    op.execute("lock table vistoria in access exclusive mode")
    invalidas = op.get_bind().execute(sa.text(f"""
        select id, (ocorrido_em at time zone 'UTC')::text, count(*) over ()
        from vistoria where not ({CONDICAO}) order by id limit 10
    """)).fetchall()
    if invalidas:
        exemplos = "; ".join(f"id={id}, ocorrido_em={data} UTC" for id, data, _ in invalidas)
        raise RuntimeError(
            f"Migração 0008 interrompida: {invalidas[0][2]} vistoria(s) com ocorrido_em "
            f"fora dos anos 0001 a 9999 UTC ou não finito. Exemplos: {exemplos}. "
            "Nenhuma linha foi corrigida ou removida; revise o histórico antes de migrar."
        )
    op.create_check_constraint(REGRA, "vistoria", CONDICAO)


def downgrade() -> None:
    op.drop_constraint(REGRA, "vistoria", type_="check")
