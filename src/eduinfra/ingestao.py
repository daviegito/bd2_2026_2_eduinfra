"""Ingestão em lote idempotente do Censo Escolar (E2).

A versão vigente de um ano é o hash do arquivo da última execução registrada
no manifesto, e a Silver publicada só vale se carregar esse mesmo hash. Se os
dois coincidem com o arquivo de origem, a execução é ignorada. Com um arquivo
diferente, a partição Parquet do ano é substituída e o OLTP recebe só o que
ainda não existe (ADR 0001: o passado não é reescrito).

Ordem de uma ingestão: a Silver é montada num preparo privado, o banco é
carregado e confirmado, e só então a Silver é publicada. Falha antes do commit
deixa banco, manifesto e Silver exatamente como estavam. Falha depois do commit
deixa a Silver antiga, que a próxima execução reconhece como desatualizada
(manifesto vigente, Silver com outro hash) e republica sem recarregar o banco.
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
from eduinfra.transformacao import (
    descartar_preparo,
    hash_da_silver,
    montar_camada_silver,
    publicar_particao,
)

log = logging.getLogger(__name__)

TAMANHO_BLOCO_HASH = 1 << 22


@dataclass(frozen=True)
class ResumoIngestao:
    ignorada: bool
    hash_sha256: str
    carga: ResumoCarga | None
    silver_republicada: bool = False


def hash_do_arquivo(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        while bloco := arquivo.read(TAMANHO_BLOCO_HASH):
            resumo.update(bloco)

    return resumo.hexdigest()


def ingerir(configuracao: Configuracao, forcar: bool = False) -> ResumoIngestao:
    iniciado_em = datetime.now(timezone.utc)
    ano = configuracao.ano_censo

    with psycopg.connect(configuracao.dsn) as conexao:
        colunas_por_item = _catalogo_de_itens(conexao)

        csv_bruto = garantir_microdados(configuracao)
        hash_origem = hash_do_arquivo(csv_bruto)

        vigente_no_banco = _hash_vigente(conexao, ano)

        if not forcar and vigente_no_banco == hash_origem:
            if hash_da_silver(configuracao) == hash_origem:
                log.info(
                    "ano %d já ingerido com este arquivo (sha256 %s…); nada a fazer. Use --forcar para reexecutar.",
                    ano,
                    hash_origem[:12],
                )
                return ResumoIngestao(ignorada=True, hash_sha256=hash_origem, carga=None)

            log.warning(
                "ano %d consta no manifesto com este arquivo, mas a Silver publicada é de outra versão; republicando a Silver.",
                ano,
            )
            _publicar_silver(configuracao, csv_bruto, colunas_por_item, hash_origem)
            return ResumoIngestao(ignorada=False, hash_sha256=hash_origem, carga=None, silver_republicada=True)

        if vigente_no_banco is not None and vigente_no_banco != hash_origem:
            log.warning(
                "ano %d já foi ingerido com um arquivo diferente: a partição Parquet será substituída; "
                "o OLTP só recebe chaves novas, porque vistoria é insert-only.",
                ano,
            )

        # As leituras acima abriram uma transação implícita. Encerrá-la aqui faz do bloco abaixo a
        # transação de fato (e não um savepoint), de modo que o commit acontece ao sair dele,
        # antes de a Silver ser publicada.
        conexao.commit()

        camada, preparo = montar_camada_silver(configuracao, csv_bruto, colunas_por_item, hash_origem)
        try:
            with conexao.transaction():
                conexao.execute(TABELAS_DE_APOIO)
                _copiar_camada_silver(conexao, camada)
                carga = _consolidar(conexao, configuracao)
                _registrar(conexao, configuracao, csv_bruto, hash_origem, forcar, carga, iniciado_em)

            # Só depois do commit: até aqui a Silver publicada não foi tocada.
            publicar_particao(configuracao, preparo)
        finally:
            descartar_preparo(preparo)

    return ResumoIngestao(ignorada=False, hash_sha256=hash_origem, carga=carga)


def _publicar_silver(
    configuracao: Configuracao,
    csv_bruto: Path,
    colunas_por_item: dict[str, str],
    hash_origem: str,
) -> None:
    _, preparo = montar_camada_silver(configuracao, csv_bruto, colunas_por_item, hash_origem)
    try:
        publicar_particao(configuracao, preparo)
    finally:
        descartar_preparo(preparo)


def _hash_vigente(conexao: psycopg.Connection, ano: int) -> str | None:
    """Hash do arquivo da última execução concluída do ano (a versão vigente)."""
    linha = conexao.execute(
        "select hash_sha256 from ingestao_execucao where ano_censo = %s order by id desc limit 1",
        (ano,),
    ).fetchone()

    return linha[0] if linha else None


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
