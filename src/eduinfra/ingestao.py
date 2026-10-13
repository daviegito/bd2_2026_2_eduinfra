"""Ingestão em lote idempotente do Censo Escolar (E2).

Reexecutar com o mesmo arquivo de origem não muda nada: o hash do arquivo é
comparado com o manifesto e a execução é ignorada. Com um arquivo diferente
para o mesmo ano, a partição Parquet daquele ano é reescrita e o OLTP recebe só
o que ainda não existe (ADR 0001: o passado não é reescrito).
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from eduinfra.carga import (
    TABELAS_DE_APOIO,
    ResumoCarga,
    _catalogo_de_itens,
    _consolidar,
    _copiar_camada_silver,
)
from eduinfra.configuracao import Configuracao
from eduinfra.microdados import garantir_microdados
from eduinfra.transformacao import preparar_camada_silver

log = logging.getLogger(__name__)

TAMANHO_BLOCO_HASH = 1 << 22


@dataclass(frozen=True)
class ResumoIngestao:
    ignorada: bool
    hash_sha256: str
    carga: ResumoCarga | None


def hash_do_arquivo(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        while bloco := arquivo.read(TAMANHO_BLOCO_HASH):
            resumo.update(bloco)

    return resumo.hexdigest()


def ingerir(configuracao: Configuracao, forcar: bool = False) -> ResumoIngestao:
    iniciado_em = datetime.now(timezone.utc)

    with psycopg.connect(configuracao.dsn) as conexao:
        colunas_por_item = _catalogo_de_itens(conexao)

        csv_bruto = garantir_microdados(configuracao)
        hash_origem = hash_do_arquivo(csv_bruto)

        if not forcar and _ja_ingerido(conexao, configuracao.ano_censo, hash_origem):
            log.info(
                "ano %d já ingerido com este arquivo (sha256 %s…); nada a fazer. Use --forcar para reexecutar.",
                configuracao.ano_censo,
                hash_origem[:12],
            )
            return ResumoIngestao(ignorada=True, hash_sha256=hash_origem, carga=None)

        if _ano_ja_ingerido_com_outro_arquivo(conexao, configuracao.ano_censo, hash_origem):
            log.warning(
                "ano %d já foi ingerido com um arquivo diferente: a partição Parquet será substituída; "
                "o OLTP só recebe chaves novas, porque vistoria é insert-only.",
                configuracao.ano_censo,
            )

        camada = preparar_camada_silver(configuracao, csv_bruto, colunas_por_item)

        with conexao.transaction():
            conexao.execute(TABELAS_DE_APOIO)
            _copiar_camada_silver(conexao, camada)
            carga = _consolidar(conexao, configuracao)
            _registrar(conexao, configuracao, csv_bruto, hash_origem, forcar, carga, iniciado_em)

    return ResumoIngestao(ignorada=False, hash_sha256=hash_origem, carga=carga)


def _ja_ingerido(conexao: psycopg.Connection, ano: int, hash_origem: str) -> bool:
    return (
        conexao.execute(
            "select 1 from ingestao_execucao where ano_censo = %s and hash_sha256 = %s limit 1",
            (ano, hash_origem),
        ).fetchone()
        is not None
    )


def _ano_ja_ingerido_com_outro_arquivo(conexao: psycopg.Connection, ano: int, hash_origem: str) -> bool:
    return (
        conexao.execute(
            "select 1 from ingestao_execucao where ano_censo = %s and hash_sha256 <> %s limit 1",
            (ano, hash_origem),
        ).fetchone()
        is not None
    )


def _registrar(
    conexao: psycopg.Connection,
    configuracao: Configuracao,
    csv_bruto: Path,
    hash_origem: str,
    forcada: bool,
    carga: ResumoCarga,
    iniciado_em: datetime,
) -> None:
    conexao.execute(
        """
        insert into ingestao_execucao (
            ano_censo, arquivo_origem, hash_sha256, forcada,
            municipios_inseridos, escolas_inseridas, vistorias_inseridas, itens_inseridos,
            iniciado_em
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            configuracao.ano_censo,
            csv_bruto.name,
            hash_origem,
            forcada,
            carga.municipios,
            carga.escolas,
            carga.vistorias,
            carga.itens,
            iniciado_em,
        ),
    )
