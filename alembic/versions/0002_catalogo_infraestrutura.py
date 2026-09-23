"""Catálogo de itens de infraestrutura vistoriados

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-22
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# coluna_censo é conferida contra o cabeçalho do Censo antes da carga: mudança
# de schema do INEP falha na validação, não no meio do COPY.
ITENS = [
    ("biblioteca", "Biblioteca", "IN_BIBLIOTECA", "pedagogico"),
    ("sala_leitura", "Sala de leitura", "IN_SALA_LEITURA", "pedagogico"),
    ("lab_ciencias", "Laboratório de ciências", "IN_LABORATORIO_CIENCIAS", "pedagogico"),
    ("lab_informatica", "Laboratório de informática", "IN_LABORATORIO_INFORMATICA", "pedagogico"),
    ("quadra_esportes", "Quadra de esportes", "IN_QUADRA_ESPORTES", "esportivo"),
    ("parque_infantil", "Parque infantil", "IN_PARQUE_INFANTIL", "esportivo"),
    ("patio_coberto", "Pátio coberto", "IN_PATIO_COBERTO", "esportivo"),
    ("refeitorio", "Refeitório", "IN_REFEITORIO", "basico"),
    ("cozinha", "Cozinha", "IN_COZINHA", "basico"),
    ("almoxarifado", "Almoxarifado", "IN_ALMOXARIFADO", "basico"),
    ("agua_potavel", "Água potável", "IN_AGUA_POTAVEL", "basico"),
    ("energia_rede_publica", "Energia da rede pública", "IN_ENERGIA_REDE_PUBLICA", "basico"),
    ("esgoto_rede_publica", "Esgoto da rede pública", "IN_ESGOTO_REDE_PUBLICA", "basico"),
    ("internet", "Acesso à internet", "IN_INTERNET", "tecnologia"),
    ("internet_alunos", "Internet disponível para alunos", "IN_INTERNET_ALUNOS", "tecnologia"),
    ("acessibilidade_rampas", "Rampas ou vias de acesso acessíveis", "IN_ACESSIBILIDADE_RAMPAS", "acessibilidade"),
    ("banheiro_pne", "Banheiro adaptado para PNE", "IN_BANHEIRO_PNE", "acessibilidade"),
]

CRIACAO = [
    """
    create type situacao_item as enum (
        'existente', 'inexistente', 'em_obra', 'inutilizavel', 'nao_informado'
    )
    """,
    """
    create table item_infraestrutura (
        codigo text primary key,
        descricao text not null,
        coluna_censo text not null unique,
        categoria text not null,
        constraint item_categoria_valida check (
            categoria in ('pedagogico', 'esportivo', 'basico', 'acessibilidade', 'tecnologia')
        )
    )
    """,
]

REMOCAO = [
    "drop table item_infraestrutura",
    "drop type situacao_item",
]


def upgrade() -> None:
    for comando in CRIACAO:
        op.execute(comando)

    valores = ", ".join(
        "('{}', '{}', '{}', '{}')".format(*item) for item in ITENS
    )
    op.execute(
        f"insert into item_infraestrutura (codigo, descricao, coluna_censo, categoria) values {valores}"
    )


def downgrade() -> None:
    for comando in REMOCAO:
        op.execute(comando)
