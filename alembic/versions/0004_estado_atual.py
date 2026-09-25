"""View de estado corrente por escola e item

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-22
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Preço do insert-only: o estado corrente é o evento mais recente de cada par
# escola-item. Item não informado não sobrescreve o que a vistoria anterior disse.
VIEW = """
create view escola_estado_atual as
select distinct on (vistoria.co_entidade, item.item_codigo)
    vistoria.co_entidade,
    item.item_codigo,
    item.situacao,
    item.quantidade,
    vistoria.ocorrido_em as vigente_desde,
    vistoria.origem,
    vistoria.id as vistoria_id
from vistoria
join vistoria_item item on item.vistoria_id = vistoria.id
where item.situacao <> 'nao_informado'
order by vistoria.co_entidade, item.item_codigo, vistoria.ocorrido_em desc, vistoria.id desc
"""

COMENTARIO = (
    "comment on view escola_estado_atual is "
    "'Estado corrente por escola e item. Se a leitura passar de 200 ms, promover a view materializada com refresh após cada carga.'"
)


def upgrade() -> None:
    op.execute(VIEW)
    op.execute(COMENTARIO)


def downgrade() -> None:
    op.execute("drop view escola_estado_atual")
