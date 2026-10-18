import logging
import os
import shutil
import uuid
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

# Escolhido por medição sobre a Silver real: o menor em disco, com leitura
# empatada com snappy e sem compressão (docs/serializacao-compressao.md).
COMPRESSAO_PARQUET = "zstd"


@dataclass(frozen=True)
class CamadaSilver:
    municipios: Path
    escolas: Path
    itens: Path


MARCADOR_ORIGEM = "origem.sha256"


def particao_silver(configuracao: Configuracao) -> Path:
    # Uma partição por ano do Censo: reingerir um ano só troca os arquivos dele.
    return configuracao.diretorio_dados / "silver" / f"ano_censo={configuracao.ano_censo}"


def area_de_publicacao(configuracao: Configuracao) -> Path:
    # Fora de silver/ para que nenhum leitor (rglob) enxergue preparo ou cópia antiga.
    # Fica no mesmo volume de silver/, o que mantém os renames atômicos.
    return configuracao.diretorio_dados / "_publicacao"


def montar_camada_silver(
    configuracao: Configuracao,
    csv_bruto: Path,
    colunas_por_item: dict[str, str],
    hash_origem: str | None = None,
) -> tuple[CamadaSilver, Path]:
    """Gera os três Parquet num diretório de preparo privado. Nada é publicado.

    Quem chama decide quando publicar (publicar_particao) e deve descartar o
    preparo ao terminar (descartar_preparo), com sucesso ou não.
    """
    preparo = area_de_publicacao(configuracao) / f"preparo-ano_censo={configuracao.ano_censo}-{uuid.uuid4().hex[:8]}"
    preparo.mkdir(parents=True)

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
            municipios=preparo / "municipio.parquet",
            escolas=preparo / "escola.parquet",
            itens=preparo / "vistoria_item.parquet",
        )
        _exportar(conexao, _consulta_municipios(), camada.municipios)
        _exportar(conexao, _consulta_escolas(), camada.escolas)
        _exportar(conexao, _consulta_itens(colunas_por_item), camada.itens)

        if hash_origem is not None:
            (preparo / MARCADOR_ORIGEM).write_text(hash_origem + "\n")

        return camada, preparo
    except BaseException:
        descartar_preparo(preparo)
        raise
    finally:
        conexao.close()


def publicar_particao(configuracao: Configuracao, preparo: Path) -> Path:
    """Troca a partição do ano pelo conjunto preparado, como uma unidade.

    Os arquivos nunca são trocados um a um: o diretório inteiro é renomeado.
    Um leitor enxerga a versão antiga completa, a nova completa, ou (por um
    instante, entre os dois renames) nenhuma partição; nunca uma mistura.
    """
    recuperar_particao(configuracao)

    destino = particao_silver(configuracao)
    antigo = _copia_antiga(configuracao)
    destino.parent.mkdir(parents=True, exist_ok=True)

    if destino.exists():
        os.rename(destino, antigo)
    try:
        os.rename(preparo, destino)
    except BaseException:
        if antigo.exists() and not destino.exists():
            os.rename(antigo, destino)
        raise

    shutil.rmtree(antigo, ignore_errors=True)
    log.info("partição silver ano_censo=%d publicada", configuracao.ano_censo)

    return destino


def recuperar_particao(configuracao: Configuracao) -> None:
    """Desfaz o que uma queda entre os dois renames de publicar_particao deixou."""
    destino = particao_silver(configuracao)
    antigo = _copia_antiga(configuracao)

    if antigo.exists() and not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        os.rename(antigo, destino)
        log.warning("partição silver ano_censo=%d restaurada da cópia anterior", configuracao.ano_censo)
    elif antigo.exists():
        shutil.rmtree(antigo, ignore_errors=True)


def hash_da_silver(configuracao: Configuracao) -> str | None:
    """Hash do arquivo de origem que gerou a partição publicada, se houver."""
    recuperar_particao(configuracao)

    marcador = particao_silver(configuracao) / MARCADOR_ORIGEM
    if not marcador.exists():
        return None

    return marcador.read_text().strip() or None


def descartar_preparo(preparo: Path) -> None:
    shutil.rmtree(preparo, ignore_errors=True)


def preparar_camada_silver(
    configuracao: Configuracao,
    csv_bruto: Path,
    colunas_por_item: dict[str, str],
    hash_origem: str | None = None,
) -> CamadaSilver:
    """Monta e publica de uma vez (usado pela carga da E1, sem coordenação com o banco)."""
    _, preparo = montar_camada_silver(configuracao, csv_bruto, colunas_por_item, hash_origem)
    try:
        destino = publicar_particao(configuracao, preparo)
    finally:
        descartar_preparo(preparo)

    return CamadaSilver(
        municipios=destino / "municipio.parquet",
        escolas=destino / "escola.parquet",
        itens=destino / "vistoria_item.parquet",
    )


def _copia_antiga(configuracao: Configuracao) -> Path:
    return area_de_publicacao(configuracao) / f"antigo-ano_censo={configuracao.ano_censo}"


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
        f"copy ({consulta}) to '{destino.as_posix()}' (format parquet, compression {COMPRESSAO_PARQUET})"
    )
    log.info("camada silver preparada: %s", destino.name)
