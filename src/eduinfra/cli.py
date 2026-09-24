import argparse
import logging
import sys

from alembic import command
from alembic.config import Config

from eduinfra import caracterizacao
from eduinfra.carga import executar_carga
from eduinfra.configuracao import Configuracao, carregar_configuracao

log = logging.getLogger("eduinfra")


def main(argumentos: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eduinfra", description="Origem OLTP do EduInfra AI")
    parser.add_argument("comando", choices=("migrar", "reverter", "carregar", "caracterizar", "tudo"))
    parser.add_argument("--revisao", default="head", help="revisão alvo do Alembic")
    parser.add_argument("--verboso", action="store_true")
    opcoes = parser.parse_args(argumentos)

    logging.basicConfig(
        level=logging.DEBUG if opcoes.verboso else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    configuracao = carregar_configuracao()

    if opcoes.comando == "reverter":
        command.downgrade(_alembic(configuracao), opcoes.revisao)
        return 0

    if opcoes.comando in ("migrar", "tudo"):
        command.upgrade(_alembic(configuracao), opcoes.revisao)

    if opcoes.comando in ("carregar", "tudo"):
        resumo = executar_carga(configuracao)
        log.info(
            "carga concluída: %d municípios, %d escolas, %d vistorias, %d itens",
            resumo.municipios,
            resumo.escolas,
            resumo.vistorias,
            resumo.itens,
        )

    if opcoes.comando in ("caracterizar", "tudo"):
        medidas = caracterizacao.medir(configuracao.dsn)
        tamanhos = caracterizacao.tamanhos_em_disco(configuracao.dsn)
        print(caracterizacao.formatar_markdown(medidas, tamanhos))

    return 0


def _alembic(configuracao: Configuracao) -> Config:
    return Config(str(configuracao.arquivo_alembic))


if __name__ == "__main__":
    sys.exit(main())
