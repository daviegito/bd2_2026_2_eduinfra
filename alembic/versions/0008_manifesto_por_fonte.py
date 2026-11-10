"""Manifesto da ingestão com mais de uma fonte (Censo e ENEM)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-08
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

FONTES = ("censo", "enem")

# A versão vigente passa a ser a última execução de (fonte, ano). As contagens de
# inserção no OLTP só existem para o Censo; o ENEM não toca o OLTP e registra quantas
# linhas a Silver publicou.
EVOLUCAO = [
    "alter table ingestao_execucao rename column ano_censo to ano",
    "alter table ingestao_execucao add column fonte text not null default 'censo'",
    "alter table ingestao_execucao alter column fonte drop default",
    f"""
    alter table ingestao_execucao add constraint ingestao_execucao_fonte_conhecida
        check (fonte in ({", ".join(f"'{fonte}'" for fonte in FONTES)}))
    """,
    "alter table ingestao_execucao alter column municipios_inseridos drop not null",
    "alter table ingestao_execucao alter column escolas_inseridas drop not null",
    "alter table ingestao_execucao alter column vistorias_inseridas drop not null",
    "alter table ingestao_execucao alter column itens_inseridos drop not null",
    "alter table ingestao_execucao add column linhas_publicadas integer",
    """
    alter table ingestao_execucao add constraint ingestao_execucao_contagens_do_censo
        check (fonte <> 'censo' or (municipios_inseridos is not null and escolas_inseridas is not null
                                    and vistorias_inseridas is not null and itens_inseridos is not null))
    """,
    "drop index ingestao_execucao_por_ano_e_hash",
    "create index ingestao_execucao_por_fonte_e_ano on ingestao_execucao (fonte, ano, id)",
    "comment on table ingestao_execucao is 'Manifesto da ingestão em lote: fonte, ano, hash do arquivo de origem e linhas gravadas por execução concluída.'",
]

# Voltar à 0007 descarta as execuções que não são do Censo: o esquema anterior não
# tem como representá-las. A Silver do ENEM continua em disco e é reingerida depois.
RETORNO = [
    "delete from ingestao_execucao where fonte <> 'censo'",
    "drop index ingestao_execucao_por_fonte_e_ano",
    "alter table ingestao_execucao drop constraint ingestao_execucao_contagens_do_censo",
    "alter table ingestao_execucao drop column linhas_publicadas",
    "alter table ingestao_execucao alter column municipios_inseridos set not null",
    "alter table ingestao_execucao alter column escolas_inseridas set not null",
    "alter table ingestao_execucao alter column vistorias_inseridas set not null",
    "alter table ingestao_execucao alter column itens_inseridos set not null",
    "alter table ingestao_execucao drop constraint ingestao_execucao_fonte_conhecida",
    "alter table ingestao_execucao drop column fonte",
    "alter table ingestao_execucao rename column ano to ano_censo",
    "create index ingestao_execucao_por_ano_e_hash on ingestao_execucao (ano_censo, hash_sha256)",
    "comment on table ingestao_execucao is 'Manifesto da ingestão em lote: ano, hash do arquivo de origem e linhas inseridas por execução concluída.'",
]


def upgrade() -> None:
    for comando in EVOLUCAO:
        op.execute(comando)


def downgrade() -> None:
    for comando in RETORNO:
        op.execute(comando)
