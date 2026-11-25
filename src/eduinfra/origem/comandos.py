import argparse
import logging
import os

from alembic import command
from alembic.config import Config

from eduinfra.nucleo.configuracao import Configuracao

log = logging.getLogger(__name__)


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


def anonimizar_tecnico(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    from eduinfra.origem import anonimizacao

    if not configuracao.dsn_admin:
        log.error(
            "DATABASE_URL_ADMIN não definida; a anonimização altera dados pessoais "
            "e exige o papel administrativo."
        )
        return 1
    try:
        resultado = anonimizacao.anonimizar_tecnico(configuracao.dsn_admin, opcoes.matricula)
    except ValueError as erro:
        log.error("%s", erro)
        return 2
    if resultado is None:
        log.error("nenhum técnico com essa matrícula; se já foi anonimizado, a matrícula original não existe mais")
        return 1
    print(
        f"Técnico {resultado.tecnico_id} anonimizado: nome e matrícula substituídos e cadastro desativado. "
        f"{resultado.vistorias} vistorias continuam ligadas ao mesmo id."
    )
    return 0


def _alembic(configuracao: Configuracao) -> Config:
    # Só migrar/reverter usam o superusuário; o resto da CLI fica no papel de rotina.
    if not configuracao.dsn_admin:
        raise RuntimeError(
            "DATABASE_URL_ADMIN não definida; execute as migrações via "
            "'docker compose --profile admin run --rm migracao'."
        )
    os.environ["EDUINFRA_ALEMBIC_URL"] = configuracao.dsn_admin
    return Config(str(configuracao.arquivo_alembic))


COMANDOS = {
    "migrar": migrar,
    "reverter": reverter,
    "gerar_amostra": gerar_amostra,
    "anonimizar-tecnico": anonimizar_tecnico,
}
