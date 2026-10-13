import argparse
import logging
import sys

from alembic import command
from alembic.config import Config

from eduinfra import (
    camada_analitica,
    caracterizacao,
    comparacao_compressao,
    comparacao_modelagem,
    comparacao_particionamento,
)
from eduinfra.carga import executar_carga
from eduinfra.configuracao import Configuracao, carregar_configuracao

log = logging.getLogger("eduinfra")


def main(argumentos: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eduinfra", description="Origem OLTP do EduInfra AI")

    parser.add_argument("comando", choices=("migrar", "reverter", "carregar", "caracterizar", "comparar-modelagem", "comparar-compressao", "publicar-analitica", "comparar-particionamento", "tudo", "gerar_amostra"))
    parser.add_argument("--repeticoes", type=int, default=5, help="repetições de cada medição em comparar-compressao e comparar-particionamento")
    parser.add_argument("--lotes", type=int, default=20, help="lotes pequenos gravados antes da compactação em comparar-particionamento")
    parser.add_argument("--escolas", type=int, default=1000, help="número de escolas a gerar quando usar gerar_amostra")
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

    if opcoes.comando == "comparar-modelagem":
        print(comparacao_modelagem.formatar_markdown(comparacao_modelagem.comparar(configuracao.dsn)))
        return 0

    if opcoes.comando == "comparar-compressao":
        silver = configuracao.diretorio_dados / "silver"
        print(comparacao_compressao.formatar_markdown(comparacao_compressao.comparar(silver, opcoes.repeticoes)))
        return 0

    if opcoes.comando == "comparar-particionamento":
        silver = configuracao.diretorio_dados / "silver"
        comparacao = comparacao_particionamento.comparar(silver, opcoes.repeticoes, opcoes.lotes)
        print(comparacao_particionamento.formatar_markdown(comparacao))
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

    if opcoes.comando in ("publicar-analitica", "tudo"):
        resumo = camada_analitica.publicar(configuracao.diretorio_dados)
        no_duckdb = camada_analitica.contar_no_duckdb(resumo.caminho)
        no_oltp = camada_analitica.contar_no_oltp(configuracao.dsn)
        print(camada_analitica.formatar_markdown(resumo, no_duckdb, no_oltp))
        if no_duckdb != no_oltp:
            log.error("a tabela analítica não confere com o OLTP")
            return 1

    if opcoes.comando == "gerar_amostra":
        from eduinfra.sample_generator import gerar_amostra

        gerar_amostra(configuracao, opcoes.escolas)

    return 0


def _alembic(configuracao: Configuracao) -> Config:
    return Config(str(configuracao.arquivo_alembic))


if __name__ == "__main__":
    sys.exit(main())
