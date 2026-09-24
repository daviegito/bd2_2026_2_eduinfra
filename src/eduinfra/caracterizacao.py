"""Passo 1 do Método de Decisão: carga de trabalho medida no banco populado."""

import psycopg

CONSULTAS = {
    "Municípios": "select count(*) from municipio",
    "Escolas em atividade": "select count(*) from escola",
    "Vistorias registradas": "select count(*) from vistoria",
    "Itens vistoriados": "select count(*) from vistoria_item",
    "Itens existentes": "select count(*) from vistoria_item where situacao = 'existente'",
    "Itens não informados": "select count(*) from vistoria_item where situacao = 'nao_informado'",
    "Escolas sem biblioteca": """
        select count(*) from escola_estado_atual
        where item_codigo = 'biblioteca' and situacao = 'inexistente'
    """,
    "Escolas sem laboratório de ciências": """
        select count(*) from escola_estado_atual
        where item_codigo = 'lab_ciencias' and situacao = 'inexistente'
    """,
}

TAMANHOS = """
select relname, pg_size_pretty(pg_total_relation_size(c.oid))
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'r'
order by pg_total_relation_size(c.oid) desc
"""


def medir(dsn: str) -> dict[str, int]:
    with psycopg.connect(dsn) as conexao:
        return {
            rotulo: conexao.execute(consulta).fetchone()[0]
            for rotulo, consulta in CONSULTAS.items()
        }


def tamanhos_em_disco(dsn: str) -> list[tuple[str, str]]:
    with psycopg.connect(dsn) as conexao:
        return conexao.execute(TAMANHOS).fetchall()


def formatar_markdown(medidas: dict[str, int], tamanhos: list[tuple[str, str]]) -> str:
    linhas = ["| Métrica | Valor |", "| --- | --- |"]
    linhas += [f"| {rotulo} | {valor:,} |".replace(",", ".") for rotulo, valor in medidas.items()]
    linhas += ["", "| Tabela | Tamanho em disco |", "| --- | --- |"]
    linhas += [f"| `{tabela}` | {tamanho} |" for tabela, tamanho in tamanhos]

    return "\n".join(linhas)
