import logging
from dataclasses import dataclass
from pathlib import Path

import duckdb
import psycopg
import pyarrow as pa
from deltalake import DeltaTable, WriterProperties, write_deltalake

log = logging.getLogger(__name__)

TABELA = "vistoria_item"

# Escolhido por medição (comparar-particionamento): a leitura filtra por UF na
# maior parte das consultas e o ano separa as cargas anuais, que são
# substituídas inteiras quando o INEP corrige um arquivo.
PARTICOES = ("ano_censo", "sg_uf")

# Mesmo codec da Silver (docs/serializacao-compressao.md).
COMPRESSAO = "ZSTD"

# Ordenar antes de gravar deixa os valores repetidos juntos, o que ajuda a
# compressão e as estatísticas de mínimo e máximo de cada arquivo.
CONSULTA_TABELA_LARGA = """
select
    cast(e.ano_censo_referencia as smallint) as ano_censo,
    m.sg_uf,
    e.co_municipio,
    i.co_entidade,
    e.tp_dependencia,
    e.tp_localizacao,
    i.item_codigo,
    i.situacao
from read_parquet('{itens}') i
join read_parquet('{escolas}') e using (co_entidade)
join read_parquet('{municipios}') m using (co_municipio)
order by m.sg_uf, e.co_municipio, i.co_entidade, i.item_codigo
"""


@dataclass(frozen=True)
class ResumoPublicacao:
    caminho: Path
    versao: int
    linhas_por_ano: dict[int, int]
    arquivos: int
    bytes_em_disco: int


def caminho_da_tabela(diretorio_dados: Path) -> Path:
    return diretorio_dados / "warehouse" / TABELA


def publicar(diretorio_dados: Path) -> ResumoPublicacao:
    """Grava cada ano da Silver na tabela Delta, substituindo só aquele ano."""
    destino = caminho_da_tabela(diretorio_dados)
    destino.parent.mkdir(parents=True, exist_ok=True)

    linhas_por_ano = {}
    for pasta in pastas_da_silver(diretorio_dados / "silver"):
        dados = tabela_larga(pasta)
        anos = set(dados.column("ano_censo").to_pylist())
        if len(anos) != 1:
            raise RuntimeError(f"a Silver em {pasta} mistura anos do Censo: {sorted(anos)}")
        ano = anos.pop()

        gravar(destino, dados, PARTICOES, substituir_ano=ano)
        linhas_por_ano[ano] = dados.num_rows
        log.info("tabela delta: ano %d gravado com %d linhas", ano, dados.num_rows)

    tabela = DeltaTable(str(destino))
    arquivos = arquivos_da_tabela(tabela)

    return ResumoPublicacao(
        caminho=destino,
        versao=tabela.version(),
        linhas_por_ano=linhas_por_ano,
        arquivos=len(arquivos),
        bytes_em_disco=sum(arquivo.stat().st_size for arquivo in arquivos),
    )


def pastas_da_silver(diretorio_silver: Path) -> list[Path]:
    pastas = sorted({arquivo.parent for arquivo in diretorio_silver.rglob(f"{TABELA}.parquet")})
    if not pastas:
        raise RuntimeError(f"{TABELA}.parquet não encontrado em {diretorio_silver}: rode a carga antes")

    return pastas


def tabela_larga(pasta_silver: Path) -> pa.Table:
    consulta = CONSULTA_TABELA_LARGA.format(
        itens=(pasta_silver / "vistoria_item.parquet").as_posix(),
        escolas=(pasta_silver / "escola.parquet").as_posix(),
        municipios=(pasta_silver / "municipio.parquet").as_posix(),
    )
    conexao = duckdb.connect()
    try:
        return conexao.execute(consulta).to_arrow_table()
    finally:
        conexao.close()


def gravar(
    destino: Path,
    dados: pa.Table,
    particoes: tuple[str, ...],
    substituir_ano: int | None = None,
    modo: str = "overwrite",
) -> None:
    opcoes = {
        "partition_by": list(particoes) or None,
        "writer_properties": WriterProperties(compression=COMPRESSAO),
    }
    # troca só as linhas do ano. recarregar um ano não mexe nos outros, e a troca é um único commit no log.
    if substituir_ano is not None:
        opcoes["predicate"] = f"ano_censo = {substituir_ano}"

    write_deltalake(str(destino), dados, mode=modo, **opcoes)


def arquivos_da_tabela(tabela: DeltaTable) -> list[Path]:
    """Arquivos Parquet que a versão atual da tabela referencia."""
    return [Path(uri.removeprefix("file://")) for uri in tabela.file_uris()]


def abrir_no_duckdb(conexao: duckdb.DuckDBPyConnection, caminho: Path, nome: str = TABELA) -> None:
    """Expõe a tabela Delta como view, lida pela extensão delta do DuckDB."""
    try:
        conexao.execute("load delta")
    except duckdb.Error:
        conexao.execute("install delta")
        conexao.execute("load delta")
    conexao.execute(f"create or replace view {nome} as select * from delta_scan('{caminho.as_posix()}')")


def contar_no_duckdb(caminho: Path) -> dict[int, int]:
    conexao = duckdb.connect()
    try:
        abrir_no_duckdb(conexao, caminho)
        linhas = conexao.execute(
            f"select ano_censo, count(*) from {TABELA} group by ano_censo order by ano_censo"
        ).fetchall()
    finally:
        conexao.close()

    return {int(ano): contagem for ano, contagem in linhas}


def contar_no_oltp(dsn: str) -> dict[int, int]:
    # A tabela analítica guarda as vistorias de linha de base do Censo
    with psycopg.connect(dsn) as conexao:
        linhas = conexao.execute(
            """
            select extract(year from v.ocorrido_em)::int, count(*)
            from vistoria_item i
            join vistoria v on v.id = i.vistoria_id
            where v.origem = 'censo_escolar'
            group by 1
            order by 1
            """
        ).fetchall()

    return dict(linhas)


def formatar_markdown(resumo: ResumoPublicacao, no_duckdb: dict[int, int], no_oltp: dict[int, int] | None) -> str:
    linhas = [
        f"Tabela Delta em {resumo.caminho}, versão {resumo.versao}, particionada por {', '.join(PARTICOES)}.",
        f"{resumo.arquivos} arquivos Parquet, {_mb(resumo.bytes_em_disco)}.",
        "",
        "| Ano | Linhas no DuckDB | Linhas no OLTP | Confere |",
        "| --- | ---: | ---: | --- |",
    ]
    for ano in sorted(set(no_duckdb) | set(no_oltp or {})):
        duckdb_ano = no_duckdb.get(ano, 0)
        if no_oltp is None:
            linhas.append(f"| {ano} | {duckdb_ano:,} | — | não conferido |".replace(",", "."))
            continue
        oltp_ano = no_oltp.get(ano, 0)
        linhas.append(
            f"| {ano} | {duckdb_ano:,} | {oltp_ano:,} | {'sim' if duckdb_ano == oltp_ano else 'NÃO'} |".replace(",", ".")
        )

    return "\n".join(linhas)


def _mb(bytes_em_disco: int) -> str:
    return f"{bytes_em_disco / 1e6:.1f} MB".replace(".", ",")
