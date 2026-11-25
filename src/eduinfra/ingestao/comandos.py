import argparse
import logging

from eduinfra.ingestao import censo, enem
from eduinfra.nucleo.configuracao import Configuracao

log = logging.getLogger(__name__)


def ingerir(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    resultado = censo.ingerir(configuracao, forcar=opcoes.forcar)
    if resultado.ignorada:
        log.info("ingestão ignorada: arquivo já registrado no manifesto (use --forcar para reexecutar)")
    elif resultado.silver_republicada:
        log.info("Silver republicada: o banco já estava na versão deste arquivo")
    else:
        resumo = resultado.carga
        log.info(
            "ingestão concluída: %d municípios, %d escolas, %d vistorias, %d itens inseridos",
            resumo.municipios,
            resumo.escolas,
            resumo.vistorias,
            resumo.itens,
        )
    return 0


def ingerir_enem(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    resultado = enem.ingerir_enem(configuracao, forcar=opcoes.forcar)
    if resultado.ignorada:
        log.info("ingestão do ENEM ignorada: arquivo já registrado no manifesto (use --forcar para reexecutar)")
    elif resultado.silver_republicada:
        log.info("Silver do ENEM republicada: o manifesto já estava na versão deste arquivo")
    else:
        log.info("ingestão do ENEM concluída: %d escolas agregadas", resultado.escolas)
    return 0




COMANDOS = {
    "carregar": ingerir,
    "ingerir": ingerir,
    "ingerir-enem": ingerir_enem,
}
