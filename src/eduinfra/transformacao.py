import logging
from dataclasses import dataclass
from pathlib import Path

import duckdb

from eduinfra.configuracao import Configuracao

log = logging.getLogger(__name__)

COLUNAS_CADASTRAIS = (
    "NU_ANO_CENSO",
    "CO_ENTIDADE",
    "NO_ENTIDADE",
    "CO_MUNICIPIO",
    "NO_MUNICIPIO",
    "SG_UF",
    "CO_UF",
    "TP_DEPENDENCIA",
    "TP_LOCALIZACAO",
    "TP_SITUACAO_FUNCIONAMENTO",
)

ESCOLA_EM_ATIVIDADE = "1"


@dataclass(frozen=True)
class CamadaSilver:
    municipios: Path
    escolas: Path
    itens: Path


def preparar_camada_silver(
    configuracao: Configuracao,
    csv_bruto: Path,
    colunas_por_item: dict[str, str],
) -> CamadaSilver:
    destino = configuracao.diretorio_dados / "silver"
    destino.mkdir(parents=True, exist_ok=True)

    conexao = duckdb.connect()
    try:
        # DuckDB não aceita parâmetro preparado em DDL.
        caminho = csv_bruto.as_posix().replace("'", "''")
        conexao.execute(
            f"""
            create or replace view bruto as
            select * from read_csv('{caminho}', delim=';', header=true, encoding='latin-1',
                                   all_varchar=true, sample_size=-1)
            """
        )
        _validar_cabecalho(conexao, colunas_por_item)

        conexao.execute(
            f"""
            create or replace view escolas_ativas as
            select * from bruto
            where TP_SITUACAO_FUNCIONAMENTO = '{ESCOLA_EM_ATIVIDADE}'
              and CO_ENTIDADE is not null
              and CO_MUNICIPIO is not null
            """
        )

        camada = CamadaSilver(
            municipios=destino / "municipio.parquet",
            escolas=destino / "escola.parquet",
            itens=destino / "vistoria_item.parquet",
        )

        _exportar(conexao, _consulta_municipios(), camada.municipios)
        _exportar(conexao, _consulta_escolas(), camada.escolas)
        _exportar(conexao, _consulta_itens(colunas_por_item), camada.itens)

        return camada
    finally:
        conexao.close()


def _validar_cabecalho(conexao: duckdb.DuckDBPyConnection, colunas_por_item: dict[str, str]) -> None:
    presentes = {linha[0] for linha in conexao.execute("describe bruto").fetchall()}
    exigidas = set(COLUNAS_CADASTRAIS) | set(colunas_por_item.values())

    ausentes = sorted(exigidas - presentes)
    if ausentes:
        raise RuntimeError(
            "o arquivo do Censo não tem as colunas esperadas pelo catálogo: "
            + ", ".join(ausentes)
            + ". Atualize a revisão 0002 do Alembic antes de recarregar."
        )


def _consulta_municipios() -> str:
    return """
    select distinct
        cast(CO_MUNICIPIO as integer) as co_municipio,
        NO_MUNICIPIO as no_municipio,
        SG_UF as sg_uf,
        cast(CO_UF as smallint) as co_uf
    from escolas_ativas
    """


def _consulta_escolas() -> str:
    return """
    select
        cast(CO_ENTIDADE as integer) as co_entidade,
        NO_ENTIDADE as no_entidade,
        cast(CO_MUNICIPIO as integer) as co_municipio,
        cast(TP_DEPENDENCIA as smallint) as tp_dependencia,
        cast(TP_LOCALIZACAO as smallint) as tp_localizacao,
        cast(NU_ANO_CENSO as smallint) as ano_censo_referencia
    from escolas_ativas
    where TP_DEPENDENCIA is not null and TP_LOCALIZACAO is not null
    """


def _consulta_itens(colunas_por_item: dict[str, str]) -> str:
    projecao = ",\n            ".join(
        f'coalesce({coluna}, \'\') as "{codigo}"' for codigo, coluna in sorted(colunas_por_item.items())
    )
    lista = ", ".join(f'"{codigo}"' for codigo in sorted(colunas_por_item))

    # Ausência de resposta vira 'nao_informado', nunca 'inexistente'.
    return f"""
    select
        co_entidade,
        item_codigo,
        case valor
            when '1' then 'existente'
            when '0' then 'inexistente'
            else 'nao_informado'
        end as situacao
    from (
        select
            cast(CO_ENTIDADE as integer) as co_entidade,
            {projecao}
        from escolas_ativas
        where TP_DEPENDENCIA is not null and TP_LOCALIZACAO is not null
    ) unpivot (valor for item_codigo in ({lista}))
    """


def _exportar(conexao: duckdb.DuckDBPyConnection, consulta: str, destino: Path) -> None:
    conexao.execute(
        f"copy ({consulta}) to '{destino.as_posix()}' (format parquet, compression zstd)"
    )
    log.info("camada silver gravada em %s", destino.name)
