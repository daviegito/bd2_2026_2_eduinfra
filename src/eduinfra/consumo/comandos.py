import argparse

from eduinfra.nucleo.configuracao import Configuracao


def gerar_painel(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    from eduinfra.consumo.painel import gerar_painel as gerar

    gerar()
    return 0


COMANDOS = {"gerar-painel": gerar_painel}
