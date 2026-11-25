import argparse

from alembic import command
from alembic.config import Config

from eduinfra.nucleo.configuracao import Configuracao


def migrar(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    command.upgrade(_alembic(configuracao), opcoes.revisao)
    return 0


def reverter(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    command.downgrade(_alembic(configuracao), opcoes.revisao)
    return 0




def gerar_amostra(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    from eduinfra.origem.amostra import gerar_amostra as gerar

    gerar(configuracao, opcoes.escolas)
    return 0


def _alembic(configuracao: Configuracao) -> Config:
    return Config(str(configuracao.arquivo_alembic))


COMANDOS = {
    "migrar": migrar,
    "reverter": reverter,
    "gerar_amostra": gerar_amostra,
}
