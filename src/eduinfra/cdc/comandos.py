import argparse
import logging
from dataclasses import replace
from pathlib import Path

from eduinfra.cdc import auditoria_cdc, fluxo_cdc, janelas, recuperacao_cdc
from eduinfra.nucleo.configuracao import Configuracao

log = logging.getLogger(__name__)

ARQUIVO_DE_VIDA = Path("/tmp/cdc_vivo")
COMANDOS_DE_BANCADA = ("demonstrar-cdc", "comparar-ingestao", "medir-cdc", "medir-recarga", "auditar-cdc")


def _como_bancada(configuracao: Configuracao) -> Configuracao:
    return replace(configuracao, dsn=configuracao.dsn_cdc_bancada or configuracao.dsn)


def preparar_slot_cdc(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    if not configuracao.dsn_cdc:
        raise RuntimeError(
            "DATABASE_URL_CDC não definida; o slot lógico exige o papel eduinfra_cdc."
        )
    fluxo_cdc.preparar_slot(configuracao)
    log.info("cdc: slot lógico pronto")
    return 0


def publicar_linha_de_base(configuracao: Configuracao, forcar: bool) -> None:
    resultado = fluxo_cdc.publicar_linha_de_base(configuracao, forcar)
    log.info("analítico: %d recebidos, %d inseridos", resultado.recebidas, resultado.inseridas)


def auditar(configuracao: Configuracao, sla_de_frescor_s: float) -> int:
    auditoria = auditoria_cdc.auditar(configuracao, sla_de_frescor_s=sla_de_frescor_s)
    print(auditoria_cdc.formatar(auditoria))
    return 1 if auditoria_cdc.violacoes(auditoria) else 0


def publicar_analitico(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    publicar_linha_de_base(configuracao, opcoes.forcar)
    return 0


def acompanhar_slot(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    configuracao = replace(configuracao, dsn=configuracao.dsn_cdc or configuracao.dsn)
    if opcoes.continuo:
        fluxo_cdc.acompanhar(configuracao, opcoes.intervalo, ARQUIVO_DE_VIDA)
    else:
        print(fluxo_cdc.passar_uma_vez(configuracao))
    return 0


def auditar_cdc(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    return auditar(_como_bancada(configuracao), opcoes.sla_frescor)


def reprocessar_fila(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    try:
        resultado = recuperacao_cdc.reprocessar(configuracao, opcoes.responsavel, opcoes.motivo, opcoes.lsn, opcoes.chave)
    except ValueError as erro:
        log.error("%s", erro)
        return 1
    print(resultado)
    return 1 if resultado.pendentes else 0


def tratar_desvio(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    try:
        novo = recuperacao_cdc.tratar_desvio(configuracao, opcoes.responsavel, opcoes.motivo, opcoes.lsn, opcoes.chave)
    except ValueError as erro:
        log.error("%s", erro)
        return 1
    print("Tratamento registrado." if novo else "A ocorrência já possui um recibo de tratamento.")
    return 0


def processar_janelas(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    print(janelas.resumo_texto(janelas.processar(configuracao)))
    return 0


def reprocessar_janela(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    janela = janelas.reprocessar_janela(configuracao, opcoes.mes)
    print(f"janela {janela.mes}: {janela.linhas} linhas, impressão {janela.impressao}")
    return 0


COMANDOS = {
    "preparar-slot-cdc": preparar_slot_cdc,
    "publicar-analitico": publicar_analitico,
    "cdc": acompanhar_slot,
    "auditar-cdc": auditar_cdc,
    "reprocessar-fila-cdc": reprocessar_fila,
    "tratar-desvio-cdc": tratar_desvio,
    "processar-janelas": processar_janelas,
    "reprocessar-janela": reprocessar_janela,
}
