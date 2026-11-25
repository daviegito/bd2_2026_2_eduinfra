"""Colunas que o consumidor do CDC espera das tabelas publicadas pela origem (ADR 0007).

O WAL é decodificado com o catálogo do momento em que a linha foi escrita, então uma
migração desfeita não destrava o que já está no slot. O desvio precisa aparecer quando
acontece. Vale para `vistoria` e `vistoria_item`; as colunas de `vistoria` que não vão
para o Delta (técnico, observação) ficam de fora de propósito e continuam no contrato.
"""

from dataclasses import dataclass

import psycopg

TABELA_DE_VISTORIAS = "vistoria"
TABELA_DE_ITENS = "vistoria_item"

OID_BIGINT = 20
OID_INTEIRO = 23
OID_TEXTO = 25
OID_TIMESTAMPTZ = 1184
# O enum situacao_item tem OID próprio em cada banco: só o nome da coluna é conferido.
TIPO_LIVRE = None

COLUNAS_ESPERADAS: dict[str, dict[str, int | None]] = {
    TABELA_DE_VISTORIAS: {
        "id": OID_BIGINT,
        "co_entidade": OID_INTEIRO,
        "tecnico_id": OID_BIGINT,
        "origem": OID_TEXTO,
        "ocorrido_em": OID_TIMESTAMPTZ,
        "registrado_em": OID_TIMESTAMPTZ,
        "observacao": OID_TEXTO,
    },
    TABELA_DE_ITENS: {
        "vistoria_id": OID_BIGINT,
        "item_codigo": OID_TEXTO,
        "situacao": TIPO_LIVRE,
        "quantidade": OID_INTEIRO,
    },
}

COLUNA_NOVA = "coluna_nova"
COLUNA_AUSENTE = "coluna_ausente"
TIPO_ALTERADO = "tipo_alterado"

COLUNAS_NO_CATALOGO = """
select attname, atttypid::int
from pg_attribute
where attrelid = %s::regclass and attnum > 0 and not attisdropped
order by attnum
"""


@dataclass(frozen=True)
class Desvio:
    tabela: str
    coluna: str
    tipo: str

    def __str__(self) -> str:
        return f"{self.tabela}.{self.coluna}: {self.tipo}"


def comparar(tabela: str, colunas: dict[str, int]) -> list[Desvio]:
    esperadas = COLUNAS_ESPERADAS.get(tabela)
    if esperadas is None:
        return []

    novas = [Desvio(tabela, coluna, COLUNA_NOVA) for coluna in colunas if coluna not in esperadas]
    ausentes = [Desvio(tabela, coluna, COLUNA_AUSENTE) for coluna in esperadas if coluna not in colunas]
    alteradas = [
        Desvio(tabela, coluna, TIPO_ALTERADO)
        for coluna, oid in colunas.items()
        if coluna in esperadas and esperadas[coluna] not in (TIPO_LIVRE, oid)
    ]

    return novas + ausentes + alteradas


def desvios_no_catalogo(conexao: psycopg.Connection) -> list[Desvio]:
    desvios = []
    for tabela in COLUNAS_ESPERADAS:
        colunas = dict(conexao.execute(COLUNAS_NO_CATALOGO, (f"public.{tabela}",)).fetchall())
        desvios += comparar(tabela, colunas)

    return desvios
