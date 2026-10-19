"""Tabela analítica de eventos de vistoria, em Delta Lake (ADR 0003).

A chave natural do evento é (vistoria_id, item_codigo), a chave primária de
vistoria_item na origem. Toda escrita passa por um merge que só insere o que a
tabela ainda não tem, então o lote e o CDC podem se sobrepor e reentregar sem
duplicar linha. A partição é o mês de ocorrido_em, a mesma janela do
reprocessamento de dado atrasado.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

from eduinfra.configuracao import Configuracao

log = logging.getLogger(__name__)

NOME_DA_TABELA = "eventos_vistoria"

ESQUEMA = pa.schema(
    [
        pa.field("vistoria_id", pa.int64(), nullable=False),
        pa.field("item_codigo", pa.string(), nullable=False),
        pa.field("co_entidade", pa.int32(), nullable=False),
        pa.field("sg_uf", pa.string(), nullable=False),
        pa.field("origem", pa.string(), nullable=False),
        pa.field("ocorrido_em", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("registrado_em", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("situacao", pa.string(), nullable=False),
        pa.field("quantidade", pa.int32()),
        pa.field("mes_ocorrencia", pa.string(), nullable=False),
        pa.field("lsn_origem", pa.int64()),
    ]
)

COLUNAS_DA_CHAVE = ("vistoria_id", "item_codigo")
COLUNA_DE_PARTICAO = "mes_ocorrencia"

LINHA_DE_BASE = """
select v.id::bigint as vistoria_id, i.item_codigo, v.co_entidade::int as co_entidade, m.sg_uf,
       v.origem, v.ocorrido_em, v.registrado_em, i.situacao::text as situacao,
       i.quantidade::int as quantidade,
       strftime(v.ocorrido_em at time zone 'UTC', '%Y-%m') as mes_ocorrencia,
       null::bigint as lsn_origem
from origem.public.vistoria v
join origem.public.vistoria_item i on i.vistoria_id = v.id
join origem.public.escola e on e.co_entidade = v.co_entidade
join origem.public.municipio m on m.co_municipio = e.co_municipio
"""


@dataclass(frozen=True)
class ResultadoDaGravacao:
    recebidas: int
    inseridas: int

    @property
    def descartadas_por_duplicidade(self) -> int:
        return self.recebidas - self.inseridas


def caminho_da_tabela(configuracao: Configuracao) -> Path:
    return configuracao.diretorio_dados / "warehouse" / NOME_DA_TABELA


def garantir_tabela(configuracao: Configuracao) -> Path:
    caminho = caminho_da_tabela(configuracao)
    if DeltaTable.is_deltatable(str(caminho)):
        return caminho

    caminho.parent.mkdir(parents=True, exist_ok=True)
    write_deltalake(str(caminho), ESQUEMA.empty_table(), partition_by=[COLUNA_DE_PARTICAO])
    log.info("tabela Delta %s criada em %s", NOME_DA_TABELA, caminho)
    return caminho


def gravar_sem_duplicar(configuracao: Configuracao, eventos: pa.Table) -> ResultadoDaGravacao:
    if eventos.num_rows == 0:
        return ResultadoDaGravacao(0, 0)

    tabela = DeltaTable(str(garantir_tabela(configuracao)))
    meses = sorted(set(eventos.column(COLUNA_DE_PARTICAO).to_pylist()))
    metricas = (
        tabela.merge(
            eventos.cast(ESQUEMA),
            predicate=_predicado(meses),
            source_alias="novo",
            target_alias="atual",
        )
        .when_not_matched_insert_all()
        .execute()
    )

    return ResultadoDaGravacao(eventos.num_rows, int(metricas["num_target_rows_inserted"]))


def _predicado(meses: list[str]) -> str:
    chave = " and ".join(f"atual.{coluna} = novo.{coluna}" for coluna in COLUNAS_DA_CHAVE)
    lista = ", ".join(f"'{mes}'" for mes in meses)

    return f"{chave} and atual.{COLUNA_DE_PARTICAO} in ({lista})"


def ler_da_origem(configuracao: Configuracao) -> pa.Table:
    conexao = duckdb.connect()
    try:
        conexao.execute("install postgres; load postgres;")
        dsn = configuracao.dsn.replace("'", "''")
        conexao.execute(f"attach '{dsn}' as origem (type postgres, read_only)")
        return conexao.execute(LINHA_DE_BASE).arrow().read_all()
    finally:
        conexao.close()


def carregar_da_origem(configuracao: Configuracao) -> ResultadoDaGravacao:
    return gravar_sem_duplicar(configuracao, ler_da_origem(configuracao))


def contar(configuracao: Configuracao) -> int:
    caminho = garantir_tabela(configuracao)
    conexao = duckdb.connect()
    try:
        conexao.execute("install delta; load delta;")
        return conexao.execute(f"select count(*) from delta_scan('{caminho.as_posix()}')").fetchone()[0]
    finally:
        conexao.close()

