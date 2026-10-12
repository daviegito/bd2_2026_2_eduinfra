import logging
from dataclasses import dataclass
from pathlib import Path

import duckdb
import psycopg

from eduinfra.configuracao import Configuracao
from eduinfra.microdados import garantir_microdados
from eduinfra.transformacao import CamadaSilver, preparar_camada_silver

log = logging.getLogger(__name__)

TAMANHO_LOTE = 50_000

MATRICULA_DA_CARGA = "SISTEMA-CENSO"

TABELAS_DE_APOIO = """
create temp table stg_municipio (
    co_municipio integer, no_municipio text, sg_uf char(2), co_uf smallint
);
create temp table stg_escola (
    co_entidade integer, no_entidade text, co_municipio integer,
    tp_dependencia smallint, tp_localizacao smallint, ano_censo_referencia smallint
);
create temp table stg_item (
    co_entidade integer, item_codigo text, situacao text
);
"""


@dataclass(frozen=True)
class ResumoCarga:
    municipios: int
    escolas: int
    vistorias: int
    itens: int


def executar_carga(configuracao: Configuracao) -> ResumoCarga:
    with psycopg.connect(configuracao.dsn) as conexao:
        colunas_por_item = _catalogo_de_itens(conexao)

        csv_bruto = garantir_microdados(configuracao)
        camada = preparar_camada_silver(configuracao, csv_bruto, colunas_por_item)

        with conexao.transaction():
            conexao.execute(TABELAS_DE_APOIO)
            _copiar_camada_silver(conexao, camada)
            resumo = _consolidar(conexao, configuracao)

        return resumo


def _catalogo_de_itens(conexao: psycopg.Connection) -> dict[str, str]:
    resultado = conexao.execute("select codigo, coluna_censo from item_infraestrutura")
    catalogo = dict(resultado.fetchall())

    if not catalogo:
        raise RuntimeError(
            "catálogo de infraestrutura vazio: rode as migrações antes da carga"
        )

    return catalogo


def _copiar_camada_silver(conexao: psycopg.Connection, camada: CamadaSilver) -> None:
    _copiar_parquet(conexao, camada.municipios, "stg_municipio")
    _copiar_parquet(conexao, camada.escolas, "stg_escola")
    _copiar_parquet(conexao, camada.itens, "stg_item")


def _copiar_parquet(conexao: psycopg.Connection, parquet: Path, tabela: str) -> None:
    leitor = duckdb.connect()
    try:
        leitor.execute(f"select * from read_parquet('{parquet.as_posix()}')")
        with conexao.cursor().copy(f"copy {tabela} from stdin") as copia:
            while lote := leitor.fetchmany(TAMANHO_LOTE):
                for linha in lote:
                    copia.write_row(linha)
    finally:
        leitor.close()

    log.info("staging carregado: %s", tabela)


def _consolidar(conexao: psycopg.Connection, configuracao: Configuracao) -> ResumoCarga:
    municipios = conexao.execute(
        """
        insert into municipio (co_municipio, no_municipio, sg_uf, co_uf)
        select co_municipio, no_municipio, sg_uf, co_uf from stg_municipio
        on conflict (co_municipio) do nothing
        """
    ).rowcount

    escolas = conexao.execute(
        """
        insert into escola (co_entidade, no_entidade, co_municipio, tp_dependencia,
                            tp_localizacao, ano_censo_referencia)
        select co_entidade, no_entidade, co_municipio, tp_dependencia,
               tp_localizacao, ano_censo_referencia
        from stg_escola
        on conflict (co_entidade) do nothing
        """
    ).rowcount

    tecnico_id = _tecnico_da_carga(conexao)

    vistorias = conexao.execute(
        """
        insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em, observacao)
        select e.co_entidade, %(tecnico)s, 'censo_escolar', %(data_evento)s,
               'Linha de base derivada do Censo Escolar ' || %(ano)s
        from stg_escola e
        on conflict (co_entidade, ocorrido_em) where origem = 'censo_escolar' do nothing
        """,
        {
            "tecnico": tecnico_id,
            "data_evento": configuracao.data_referencia,
            "ano": str(configuracao.ano_censo),
        },
    ).rowcount

    itens = conexao.execute(
        """
        insert into vistoria_item (vistoria_id, item_codigo, situacao)
        select v.id, s.item_codigo, s.situacao::situacao_item
        from stg_item s
        join vistoria v
          on v.co_entidade = s.co_entidade
         and v.ocorrido_em = %(data_evento)s
         and v.origem = 'censo_escolar'
        on conflict (vistoria_id, item_codigo) do nothing
        """,
        {"data_evento": configuracao.data_referencia},
    ).rowcount

    return ResumoCarga(municipios=municipios, escolas=escolas, vistorias=vistorias, itens=itens)


def _tecnico_da_carga(conexao: psycopg.Connection) -> int:
    conexao.execute(
        """
        insert into tecnico (matricula, nome, lotacao_uf)
        values (%s, 'Carga automatizada do Censo Escolar', 'DF')
        on conflict (matricula) do nothing
        """,
        (MATRICULA_DA_CARGA,),
    )

    resultado = conexao.execute(
        "select id from tecnico where matricula = %s", (MATRICULA_DA_CARGA,)
    ).fetchone()

    return resultado[0]
