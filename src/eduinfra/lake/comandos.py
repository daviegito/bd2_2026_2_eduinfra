import argparse
import logging

from eduinfra.lake import camada_analitica
from eduinfra.nucleo.configuracao import Configuracao

log = logging.getLogger(__name__)


def publicar_analitica(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    resumo = camada_analitica.publicar(configuracao.diretorio_dados)
    no_duckdb = camada_analitica.contar_no_duckdb(resumo.caminho)
    no_oltp = camada_analitica.contar_no_oltp(configuracao.dsn)
    print(camada_analitica.formatar_markdown(resumo, no_duckdb, no_oltp))
    if no_duckdb != no_oltp:
        log.error("a tabela analítica não confere com o OLTP")
        return 1
    return 0


COMANDOS = {"publicar-analitica": publicar_analitica}
