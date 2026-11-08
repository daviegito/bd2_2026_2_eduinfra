"""Fila de erros do CDC: o que o consumidor leu do WAL e não aplicou no Delta (ADR 0007).

Guarda a chave e o motivo, sem os valores da linha. A origem é insert-only e continua
com o dado, e `vistoria.observacao` é texto livre que pode conter dado pessoal.
"""

from datetime import datetime

import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

from eduinfra.configuracao import Configuracao

NOME_DA_TABELA = "fila_de_erros_cdc"
TAMANHO_MAXIMO_DO_MOTIVO = 200
MOTIVO_DE_VIOLACAO = "violacao_insert_only"

ESQUEMA = pa.schema(
    [
        pa.field("lsn_origem", pa.int64(), nullable=False),
        pa.field("chave", pa.string(), nullable=False),
        pa.field("confirmada_em", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("tabela", pa.string(), nullable=False),
        pa.field("operacao", pa.string(), nullable=False),
        pa.field("vistoria_id", pa.int64()),
        pa.field("item_codigo", pa.string()),
        pa.field("motivo", pa.string(), nullable=False),
    ]
)


def caminho_da_tabela(configuracao: Configuracao) -> str:
    return str(configuracao.diretorio_dados / "warehouse" / NOME_DA_TABELA)


def montar_linha(
    lsn_origem: int,
    confirmada_em: datetime,
    tabela: str,
    operacao: str,
    vistoria_id: int | None,
    item_codigo: str | None,
    motivo: str,
) -> dict:
    return {
        "lsn_origem": lsn_origem,
        "chave": f"{tabela}/{operacao}/{vistoria_id}/{item_codigo}",
        "confirmada_em": confirmada_em,
        "tabela": tabela,
        "operacao": operacao,
        "vistoria_id": vistoria_id,
        "item_codigo": item_codigo,
        "motivo": motivo[:TAMANHO_MAXIMO_DO_MOTIVO],
    }


def gravar(configuracao: Configuracao, linhas: list[dict]) -> None:
    if not linhas:
        return

    caminho = caminho_da_tabela(configuracao)
    tabela = pa.Table.from_pylist(linhas, schema=ESQUEMA)
    if not DeltaTable.is_deltatable(caminho):
        (configuracao.diretorio_dados / "warehouse").mkdir(parents=True, exist_ok=True)
        write_deltalake(caminho, ESQUEMA.empty_table())

    # A passada que cai antes de avançar o slot relê as mesmas linhas: a chave evita contar duas vezes.
    (
        DeltaTable(caminho)
        .merge(
            tabela,
            predicate="atual.lsn_origem = novo.lsn_origem and atual.chave = novo.chave",
            source_alias="novo",
            target_alias="atual",
        )
        .when_not_matched_insert_all()
        .execute()
    )


def ler(configuracao: Configuracao) -> pa.Table:
    caminho = caminho_da_tabela(configuracao)
    if not DeltaTable.is_deltatable(caminho):
        return ESQUEMA.empty_table()

    return DeltaTable(caminho).to_pyarrow_table()
