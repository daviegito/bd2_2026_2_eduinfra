"""Índice que não se repete: o SQLMesh reaproveita a tabela física na reconstrução e roda
as pós-instruções de novo, então um CREATE INDEX sem nome criaria uma cópia a cada execução."""

import hashlib

from sqlmesh import macro


@macro()
def criar_indice(evaluator, *colunas, unico=False):
    lista = ", ".join(coluna.sql(dialect="postgres") for coluna in colunas)
    tabela = evaluator.this_model
    nome = "ix_" + hashlib.md5(f"{tabela}({lista})".encode()).hexdigest()[:20]
    tipo = "unique index" if str(unico).lower() == "true" else "index"

    return f'create {tipo} if not exists "{nome}" on {tabela} ({lista})'
