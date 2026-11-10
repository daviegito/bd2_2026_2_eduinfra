"""Ingestão em lote idempotente do ENEM, com o mesmo protocolo do Censo.

A versão vigente é o hash da última execução de ('enem', ano) no manifesto,
e a Silver publicada só vale se o marcador dela tiver esse mesmo hash. O ENEM
não grava no OLTP: a transação só registra o manifesto, e a Silver é publicada
depois do commit. Falha antes do commit não deixa rastro; falha depois deixa a
Silver antiga, que a próxima execução republica sem nova linha no manifesto.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from eduinfra.configuracao import Configuracao
from eduinfra.enem import (
    escolas_agregadas,
    hash_da_silver_enem,
    montar_silver_enem,
    publicar_silver_enem,
)
from eduinfra.ingestao import _hash_vigente, hash_do_arquivo
from eduinfra.microdados import garantir_resultados_enem
from eduinfra.transformacao import descartar_preparo

log = logging.getLogger(__name__)

FONTE = "enem"


@dataclass(frozen=True)
class ResumoIngestaoEnem:
    ignorada: bool
    hash_sha256: str
    escolas: int | None
    silver_republicada: bool = False


def ingerir_enem(configuracao: Configuracao, forcar: bool = False) -> ResumoIngestaoEnem:
    iniciado_em = datetime.now(timezone.utc)
    ano = configuracao.ano_enem

    with psycopg.connect(configuracao.dsn) as conexao:
        csv_resultados = garantir_resultados_enem(configuracao)
        hash_origem = hash_do_arquivo(csv_resultados)

        vigente_no_banco = _hash_vigente(conexao, FONTE, ano)

        if not forcar and vigente_no_banco == hash_origem:
            if hash_da_silver_enem(configuracao) == hash_origem:
                log.info(
                    "enem %d já ingerido com este arquivo (sha256 %s…); nada a fazer. Use --forcar para reexecutar.",
                    ano,
                    hash_origem[:12],
                )
                return ResumoIngestaoEnem(ignorada=True, hash_sha256=hash_origem, escolas=None)

            log.warning(
                "enem %d consta no manifesto com este arquivo, mas a Silver publicada é de outra versão; republicando a Silver.",
                ano,
            )
            escolas = _publicar_silver(configuracao, csv_resultados, hash_origem)
            return ResumoIngestaoEnem(ignorada=False, hash_sha256=hash_origem, escolas=escolas, silver_republicada=True)

        if vigente_no_banco is not None and vigente_no_banco != hash_origem:
            log.warning("enem %d já foi ingerido com um arquivo diferente: a partição da Silver será substituída.", ano)

        # Mesmo motivo da ingestão do Censo: sem isto o bloco abaixo vira savepoint
        # e o commit só aconteceria depois da publicação.
        conexao.commit()

        silver, preparo = montar_silver_enem(configuracao, csv_resultados, hash_origem)
        try:
            escolas = escolas_agregadas(silver)
            with conexao.transaction():
                _registrar(conexao, configuracao, csv_resultados, hash_origem, forcar, escolas, iniciado_em)

            publicar_silver_enem(configuracao, preparo)
        finally:
            descartar_preparo(preparo)

    return ResumoIngestaoEnem(ignorada=False, hash_sha256=hash_origem, escolas=escolas)


def _publicar_silver(configuracao: Configuracao, csv_resultados: Path, hash_origem: str) -> int:
    silver, preparo = montar_silver_enem(configuracao, csv_resultados, hash_origem)
    try:
        escolas = escolas_agregadas(silver)
        publicar_silver_enem(configuracao, preparo)
    finally:
        descartar_preparo(preparo)

    return escolas


def _registrar(
    conexao: psycopg.Connection,
    configuracao: Configuracao,
    csv_resultados: Path,
    hash_origem: str,
    forcada: bool,
    escolas: int,
    iniciado_em: datetime,
) -> None:
    conexao.execute(
        """
        insert into ingestao_execucao (fonte, ano, arquivo_origem, hash_sha256, forcada, linhas_publicadas, iniciado_em)
        values (%s, %s, %s, %s, %s, %s, %s)
        """,
        (FONTE, configuracao.ano_enem, csv_resultados.name, hash_origem, forcada, escolas, iniciado_em),
    )
