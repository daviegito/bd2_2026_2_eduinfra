import argparse

from eduinfra.nucleo.configuracao import Configuracao
from eduinfra.seguranca import controle_de_acesso


def demonstrar_acesso(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    return controle_de_acesso.executar()


COMANDOS = {"demonstrar-acesso": demonstrar_acesso}
