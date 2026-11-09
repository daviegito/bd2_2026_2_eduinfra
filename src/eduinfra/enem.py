"""Silver do ENEM: RESULTADOS_AAAA.csv agregado por escola de conclusão.

Desde 2024 o INEP publica PARTICIPANTES e RESULTADOS sem chave em comum; só
RESULTADOS tem a escola (CO_ESCOLA) e as notas, então é ele que entra. Uma linha
do arquivo é um inscrito, presente ou não.

Critérios da agregação (docs/enem.md):

- Prova objetiva: a nota só vale com TP_PRESENCA = 1 e NU_NOTA preenchida.
  Quem faltou (0) ou foi eliminado (2) fica fora da média; ausência nunca vira
  zero. Presente sem nota é dado faltante: também fica fora e é contado no resumo.
- Redação: vale toda nota preenchida com TP_STATUS_REDACAO preenchido, inclusive
  o zero de redação anulada, em branco ou com fuga ao tema, que é o resultado
  oficial. Os zeros são contados à parte; como somam zero, a média sem eles é
  media_redacao * notas_validas_redacao / (notas_validas_redacao - redacoes_nota_zero).
- Nota zero em prova objetiva com presença é mantida como nota.

O INEP troca o CO_ESCOLA por uma máscara quando a escola tem menos de 10
participantes; esses códigos não existem no Censo e ficam marcados.
"""

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

import duckdb

from eduinfra.configuracao import Configuracao
from eduinfra.transformacao import (
    COMPRESSAO_PARQUET,
    MARCADOR_ORIGEM,
    area_de_publicacao,
    descartar_preparo,
    hash_publicado,
    publicar_diretorio,
)

log = logging.getLogger(__name__)

AREAS_OBJETIVAS = ("cn", "ch", "lc", "mt")

COLUNAS_EXIGIDAS = (
    "NU_ANO",
    "CO_ESCOLA",
    "CO_MUNICIPIO_ESC",
    "SG_UF_ESC",
    "TP_DEPENDENCIA_ADM_ESC",
    "TP_LOCALIZACAO_ESC",
    "TP_SIT_FUNC_ESC",
    *(f"TP_PRESENCA_{area.upper()}" for area in AREAS_OBJETIVAS),
    *(f"NU_NOTA_{area.upper()}" for area in AREAS_OBJETIVAS),
    "TP_STATUS_REDACAO",
    "NU_NOTA_REDACAO",
)

PRESENTE = "1"

# Abaixo disso o INEP mascara o CO_ESCOLA (nota 2 do dicionário de RESULTADOS 2024).
MINIMO_DE_PARTICIPANTES = 10

# Os leitores da Silver do Censo procuram escola.parquet e vistoria_item.parquet
# com rglob em silver/: estes nomes não podem coincidir com os dele.
ARQUIVO_DESEMPENHO = "desempenho_escola.parquet"
ARQUIVO_RESUMO = "resumo.parquet"


@dataclass(frozen=True)
class SilverEnem:
    desempenho: Path
    resumo: Path


def particao_enem(configuracao: Configuracao) -> Path:
    return configuracao.diretorio_dados / "silver" / "enem" / f"ano={configuracao.ano_enem}"


def montar_silver_enem(configuracao: Configuracao, csv_resultados: Path, hash_origem: str) -> tuple[SilverEnem, Path]:
    """Gera os Parquet do ano num preparo privado; quem chama publica e descarta."""
    preparo = area_de_publicacao(configuracao) / f"preparo-enem-ano={configuracao.ano_enem}-{uuid.uuid4().hex[:8]}"
    preparo.mkdir(parents=True)

    conexao = duckdb.connect()
    try:
        caminho = csv_resultados.as_posix().replace("'", "''")
        conexao.execute(
            f"""
            create or replace view bruto as
            select * from read_csv('{caminho}', delim=';', header=true, encoding='latin-1', all_varchar=true)
            """
        )
        _validar_cabecalho(conexao)
        conexao.execute(f"create or replace view participante as {_consulta_participantes()}")

        silver = SilverEnem(desempenho=preparo / ARQUIVO_DESEMPENHO, resumo=preparo / ARQUIVO_RESUMO)
        _exportar(conexao, _consulta_desempenho(), silver.desempenho)
        _exportar(conexao, _consulta_resumo(), silver.resumo)
        (preparo / MARCADOR_ORIGEM).write_text(hash_origem + "\n")

        return silver, preparo
    except BaseException:
        descartar_preparo(preparo)
        raise
    finally:
        conexao.close()


def publicar_silver_enem(configuracao: Configuracao, preparo: Path) -> Path:
    destino = publicar_diretorio(preparo, particao_enem(configuracao), _copia_antiga(configuracao))
    log.info("partição silver enem ano=%d publicada", configuracao.ano_enem)

    return destino


def hash_da_silver_enem(configuracao: Configuracao) -> str | None:
    return hash_publicado(particao_enem(configuracao), _copia_antiga(configuracao))


def escolas_agregadas(silver: SilverEnem) -> int:
    return duckdb.connect().execute(
        f"select count(*) from read_parquet('{silver.desempenho.as_posix()}', hive_partitioning=false)"
    ).fetchone()[0]


def _copia_antiga(configuracao: Configuracao) -> Path:
    return area_de_publicacao(configuracao) / f"antigo-enem-ano={configuracao.ano_enem}"


def _validar_cabecalho(conexao: duckdb.DuckDBPyConnection) -> None:
    presentes = {linha[0] for linha in conexao.execute("describe bruto").fetchall()}

    ausentes = [coluna for coluna in COLUNAS_EXIGIDAS if coluna not in presentes]
    if ausentes:
        raise RuntimeError(
            "o arquivo de resultados do ENEM não tem as colunas esperadas: "
            + ", ".join(ausentes)
            + ". O layout suportado é o de RESULTADOS_AAAA.csv, publicado a partir de 2024."
        )


def _consulta_participantes() -> str:
    objetivas = ",\n        ".join(
        f"""case when TP_PRESENCA_{a.upper()} = '{PRESENTE}' then cast(NU_NOTA_{a.upper()} as double) end as nota_{a},
        TP_PRESENCA_{a.upper()} = '{PRESENTE}' and NU_NOTA_{a.upper()} is null as faltante_{a}"""
        for a in AREAS_OBJETIVAS
    )

    return f"""
    select
        cast(NU_ANO as smallint) as ano,
        cast(CO_ESCOLA as integer) as co_escola,
        cast(CO_MUNICIPIO_ESC as integer) as co_municipio_esc,
        SG_UF_ESC as sg_uf_esc,
        cast(TP_DEPENDENCIA_ADM_ESC as smallint) as tp_dependencia_adm_esc,
        cast(TP_LOCALIZACAO_ESC as smallint) as tp_localizacao_esc,
        cast(TP_SIT_FUNC_ESC as smallint) as tp_sit_func_esc,
        {objetivas},
        case when TP_STATUS_REDACAO is not null then cast(NU_NOTA_REDACAO as double) end as nota_redacao
    from bruto
    """


def _todas_as_notas() -> str:
    return " and ".join(f"nota_{a} is not null" for a in (*AREAS_OBJETIVAS, "redacao"))


def _nenhuma_nota() -> str:
    return " and ".join(f"nota_{a} is null" for a in (*AREAS_OBJETIVAS, "redacao"))


def _consulta_desempenho() -> str:
    por_area = ",\n        ".join(
        f"count(nota_{a})::integer as notas_validas_{a}, avg(nota_{a}) as media_{a}" for a in (*AREAS_OBJETIVAS, "redacao")
    )
    soma = " + ".join(f"nota_{a}" for a in (*AREAS_OBJETIVAS, "redacao"))

    # Os atributos da escola vêm do Censo pelo INEP e são iguais em todas as linhas
    # de um mesmo código; any_value não escolhe entre valores diferentes.
    return f"""
    select
        any_value(ano)::smallint as ano,
        co_escola,
        any_value(co_municipio_esc) as co_municipio_esc,
        any_value(sg_uf_esc) as sg_uf_esc,
        any_value(tp_dependencia_adm_esc) as tp_dependencia_adm_esc,
        any_value(tp_localizacao_esc) as tp_localizacao_esc,
        any_value(tp_sit_func_esc) as tp_sit_func_esc,
        count(*)::integer as participantes,
        count(*) filter (where {_todas_as_notas()})::integer as participantes_com_todas_as_notas,
        count(*) filter (where {_nenhuma_nota()})::integer as participantes_sem_nota_valida,
        {por_area},
        count(*) filter (where nota_redacao = 0)::integer as redacoes_nota_zero,
        avg(({soma}) / 5) filter (where {_todas_as_notas()}) as media_geral,
        count(*) < {MINIMO_DE_PARTICIPANTES} as poucos_participantes
    from participante
    where co_escola is not null
    group by co_escola
    order by co_escola
    """


def _consulta_resumo() -> str:
    faltantes = " + ".join(f"count(*) filter (where faltante_{a})" for a in AREAS_OBJETIVAS)
    zeros = " + ".join(f"count(*) filter (where nota_{a} = 0)" for a in AREAS_OBJETIVAS)

    return f"""
    select
        any_value(ano)::smallint as ano,
        count(*) as participantes,
        count(co_escola) as participantes_com_escola,
        count(distinct co_escola) as escolas,
        count(*) filter (where {_todas_as_notas()}) as participantes_com_todas_as_notas,
        count(*) filter (where {_nenhuma_nota()}) as participantes_sem_nota_valida,
        {faltantes} as notas_objetivas_faltantes,
        {zeros} as notas_objetivas_zero,
        count(*) filter (where nota_redacao = 0) as redacoes_nota_zero
    from participante
    """


def _exportar(conexao: duckdb.DuckDBPyConnection, consulta: str, destino: Path) -> None:
    conexao.execute(
        f"copy ({consulta}) to '{destino.as_posix()}' (format parquet, compression {COMPRESSAO_PARQUET})"
    )
    log.info("silver do enem preparada: %s", destino.name)
