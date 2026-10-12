import argparse
import logging
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from eduinfra import caracterizacao, comparacao_ingestao, comparacao_modelagem, demonstracao_cdc, fluxo_cdc
from eduinfra.ingestao import ingerir
from eduinfra.configuracao import Configuracao, carregar_configuracao

log = logging.getLogger("eduinfra")


def main(argumentos: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eduinfra", description="Origem OLTP do EduInfra AI")

    parser.add_argument("comando", choices=("migrar", "reverter", "carregar", "ingerir", "caracterizar", "comparar-modelagem", "tudo", "gerar_amostra",
                                           "publicar-analitico", "cdc", "demonstrar-cdc", "comparar-ingestao"))
    parser.add_argument("--escolas", type=int, default=1000, help="número de escolas a gerar quando usar gerar_amostra")
    parser.add_argument("--revisao", default="head", help="revisão alvo do Alembic")
    parser.add_argument("--forcar", action="store_true", help="reexecuta a ingestão mesmo que o arquivo já conste no manifesto")
    parser.add_argument("--continuo", action="store_true", help="no comando cdc, fica acompanhando o slot em vez de passar uma vez")
    parser.add_argument("--intervalo", type=float, default=2.0, help="segundos entre passadas do comando cdc --continuo")
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

    if opcoes.comando in COMANDOS_ANALITICOS:
        return _executar_analitico(opcoes, configuracao)

    if opcoes.comando == "comparar-modelagem":
        print(comparacao_modelagem.formatar_markdown(comparacao_modelagem.comparar(configuracao.dsn)))
        return 0

    if opcoes.comando in ("migrar", "tudo"):
        command.upgrade(_alembic(configuracao), opcoes.revisao)

    if opcoes.comando in ("carregar", "ingerir", "tudo"):
        resultado = ingerir(configuracao, forcar=opcoes.forcar)
        if resultado.ignorada:
            log.info("ingestão ignorada: arquivo já registrado no manifesto")
        else:
            resumo = resultado.carga
            log.info(
                "ingestão concluída: %d municípios, %d escolas, %d vistorias, %d itens inseridos",
                resumo.municipios,
                resumo.escolas,
                resumo.vistorias,
                resumo.itens,
            )

    if opcoes.comando == "tudo":
        _publicar_linha_de_base(configuracao, opcoes.forcar)

    if opcoes.comando in ("caracterizar", "tudo"):
        medidas = caracterizacao.medir(configuracao.dsn)
        tamanhos = caracterizacao.tamanhos_em_disco(configuracao.dsn)
        print(caracterizacao.formatar_markdown(medidas, tamanhos))

    if opcoes.comando == "gerar_amostra":
        from eduinfra.sample_generator import gerar_amostra

        gerar_amostra(configuracao, opcoes.escolas)

    return 0


COMANDOS_ANALITICOS = (
    "publicar-analitico",
    "cdc",
    "demonstrar-cdc",
    "comparar-ingestao",
)

ARQUIVO_DE_VIDA = Path("/tmp/cdc_vivo")


def _executar_analitico(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    if opcoes.comando == "publicar-analitico":
        _publicar_linha_de_base(configuracao, opcoes.forcar)
    elif opcoes.comando == "cdc" and opcoes.continuo:
        fluxo_cdc.acompanhar(configuracao, opcoes.intervalo, ARQUIVO_DE_VIDA)
    elif opcoes.comando == "cdc":
        print(fluxo_cdc.passar_uma_vez(configuracao))
    elif opcoes.comando == "demonstrar-cdc":
        print(demonstracao_cdc.demonstrar(configuracao))
    else:
        capturas, ociosa = comparacao_ingestao.medir(configuracao)
        print(comparacao_ingestao.formatar_markdown(capturas, ociosa))

    return 0


def _publicar_linha_de_base(configuracao: Configuracao, forcar: bool) -> None:
    resultado = fluxo_cdc.publicar_linha_de_base(configuracao, forcar)
    log.info("analítico: %d recebidos, %d inseridos", resultado.recebidas, resultado.inseridas)


def _alembic(configuracao: Configuracao) -> Config:
    return Config(str(configuracao.arquivo_alembic))


if __name__ == "__main__":
    sys.exit(main())
