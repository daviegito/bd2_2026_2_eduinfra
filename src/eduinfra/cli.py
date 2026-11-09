import argparse
import logging
import sys
from datetime import timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config

from eduinfra import (
    auditoria_cdc,
    camada_analitica,
    caracterizacao,
    cobertura_enem,
    comparacao_compressao,
    comparacao_ingestao,
    comparacao_latencia,
    comparacao_modelagem,
    comparacao_particionamento,
    demonstracao_cdc,
    fluxo_cdc,
    frescor,
    janelas,
    modelo_analitico,
)
from eduinfra.ingestao import ingerir
from eduinfra.ingestao_enem import ingerir_enem
from eduinfra.configuracao import Configuracao, carregar_configuracao

log = logging.getLogger("eduinfra")


def main(argumentos: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eduinfra", description="Origem OLTP do EduInfra AI")

    parser.add_argument("comando", choices=("migrar", "reverter", "carregar", "ingerir", "caracterizar", "comparar-modelagem", "comparar-compressao",
                                           "processar-janelas", "reprocessar-janela", "demonstrar-atraso", "tudo", "gerar_amostra",
                                           "publicar-analitico", "cdc", "demonstrar-cdc", "comparar-ingestao",
                                           "publicar-analitica", "comparar-particionamento", "medir-cdc", "medir-recarga", "auditar-cdc",
                                           "construir-gold", "comparar-granularidades", "medir-frescor", "demonstrar-frescor",
                                           "ingerir-enem", "medir-cobertura-enem", "gerar-painel"))
    parser.add_argument("--repeticoes", type=int, default=5, help="repetições de cada medição em comparar-compressao, comparar-particionamento, medir-cdc e medir-recarga")
    parser.add_argument("--sla-frescor", type=float, default=auditoria_cdc.SLA_DE_FRESCOR_S, help="segundos de frescor tolerados em auditar-cdc e medir-cdc")
    parser.add_argument("--mes", help="janela AAAA-MM para reprocessar-janela")
    parser.add_argument("--lotes", type=int, default=20, help="lotes pequenos gravados antes da compactação em comparar-particionamento")
    parser.add_argument("--escolas", type=int, default=1000, help="número de escolas a gerar quando usar gerar_amostra")
    parser.add_argument("--revisao", default="head", help="revisão alvo do Alembic")
    parser.add_argument("--forcar", action="store_true", help="reexecuta mesmo que o resultado já exista: em ingerir e ingerir-enem, ignora o manifesto; em publicar-analitico, relê a origem")
    parser.add_argument("--continuo", action="store_true", help="no comando cdc, fica acompanhando o slot em vez de passar uma vez")
    parser.add_argument("--intervalo", type=float, default=2.0, help="segundos entre passadas do comando cdc --continuo")
    parser.add_argument("--saida", type=Path, help="em comparar-granularidades e medir-cobertura-enem, grava o resultado neste arquivo Markdown além de imprimir; no Compose, monte a pasta (-v ./docs:/app/docs), senão o arquivo fica dentro do contêiner")
    parser.add_argument("--sla-horas", type=float, default=frescor.SLA_PADRAO.total_seconds() / 3600, help="em medir-frescor, SLA de frescor da Gold em horas (padrão 25)")
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

    if opcoes.comando == "construir-gold":
        resumo = modelo_analitico.construir(configuracao)
        fonte = resumo.fonte
        log.info(
            "fonte de eventos: versão %d da tabela Delta, %d eventos; origem com %d; %d só na origem, %d só na fonte",
            fonte.versao_delta, fonte.na_fonte, fonte.na_origem, fonte.so_na_origem, fonte.so_na_fonte,
        )
        for tabela, linhas in resumo.linhas.items():
            log.info("gold.%s: %d linhas", tabela, linhas)
        # Cada construção registra o frescor; a violação só reprova em medir-frescor.
        frescor.medir(configuracao.dsn, comando="construir-gold")
        return 0

    if opcoes.comando == "medir-frescor":
        medicao = frescor.medir(configuracao.dsn, timedelta(hours=opcoes.sla_horas))
        print(frescor.resumo(medicao))
        return 0 if medicao.dentro_do_sla else 1

    if opcoes.comando == "demonstrar-frescor":
        print(frescor.demonstrar(configuracao.dsn))
        return 0

    if opcoes.comando == "comparar-granularidades":
        texto = modelo_analitico.formatar_markdown(
            modelo_analitico.comparar(configuracao.dsn, opcoes.repeticoes)
        )
        _imprimir(texto, opcoes.saida)
        return 0

    if opcoes.comando == "ingerir-enem":
        resultado = ingerir_enem(configuracao, forcar=opcoes.forcar)
        if resultado.ignorada:
            log.info("ingestão do ENEM ignorada: arquivo já registrado no manifesto (use --forcar para reexecutar)")
        elif resultado.silver_republicada:
            log.info("Silver do ENEM republicada: o manifesto já estava na versão deste arquivo")
        else:
            log.info("ingestão do ENEM concluída: %d escolas agregadas", resultado.escolas)
        return 0

    if opcoes.comando == "medir-cobertura-enem":
        _imprimir(cobertura_enem.formatar_markdown(cobertura_enem.medir(configuracao)), opcoes.saida)
        return 0

    if opcoes.comando == "comparar-modelagem":
        print(comparacao_modelagem.formatar_markdown(comparacao_modelagem.comparar(configuracao.dsn)))
        return 0

    if opcoes.comando == "comparar-compressao":
        silver = configuracao.diretorio_dados / "silver"
        print(comparacao_compressao.formatar_markdown(comparacao_compressao.comparar(silver, opcoes.repeticoes)))
        return 0

    if opcoes.comando == "processar-janelas":
        print(janelas.resumo_texto(janelas.processar(configuracao)))
        return 0

    if opcoes.comando == "reprocessar-janela":
        if not opcoes.mes:
            parser.error("reprocessar-janela exige --mes AAAA-MM")
        janela = janelas.reprocessar_janela(configuracao, opcoes.mes)
        print(f"janela {janela.mes}: {janela.linhas} linhas, impressão {janela.impressao}")
        return 0

    if opcoes.comando == "demonstrar-atraso":
        from eduinfra.demonstracao_atraso import demonstrar

        print(demonstrar(configuracao))
        return 0

    if opcoes.comando == "comparar-particionamento":
        silver = configuracao.diretorio_dados / "silver"
        comparacao = comparacao_particionamento.comparar(silver, opcoes.repeticoes, opcoes.lotes)
        print(comparacao_particionamento.formatar_markdown(comparacao))
        return 0

    if opcoes.comando in ("migrar", "tudo"):
        command.upgrade(_alembic(configuracao), opcoes.revisao)

    if opcoes.comando in ("carregar", "ingerir", "tudo"):
        resultado = ingerir(configuracao, forcar=opcoes.forcar)
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

    if opcoes.comando == "tudo":
        _publicar_linha_de_base(configuracao, opcoes.forcar)

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

    if opcoes.comando == "gerar-painel":
        from eduinfra.painel import gerar_painel
        gerar_painel()
        return 0

    return 0


COMANDOS_ANALITICOS = ("publicar-analitico", "cdc", "demonstrar-cdc", "comparar-ingestao", "medir-cdc", "medir-recarga", "auditar-cdc")

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
    elif opcoes.comando == "medir-cdc":
        print(comparacao_latencia.formatar_cdc(comparacao_latencia.medir_cdc(configuracao, opcoes.repeticoes)))
        print(comparacao_latencia.formatar_ordem(comparacao_latencia.medir_ordem(configuracao, opcoes.repeticoes)))
        return _auditar(configuracao, opcoes.sla_frescor)
    elif opcoes.comando == "auditar-cdc":
        return _auditar(configuracao, opcoes.sla_frescor)
    elif opcoes.comando == "medir-recarga":
        print(comparacao_latencia.formatar_recarga(comparacao_latencia.medir_recarga(configuracao, opcoes.repeticoes)))
    else:
        capturas, ociosa = comparacao_ingestao.medir(configuracao)
        print(comparacao_ingestao.formatar_markdown(capturas, ociosa))

    return 0


def _auditar(configuracao: Configuracao, sla_de_frescor_s: float) -> int:
    auditoria = auditoria_cdc.auditar(configuracao, sla_de_frescor_s=sla_de_frescor_s)
    print(auditoria_cdc.formatar(auditoria))

    return 1 if auditoria_cdc.violacoes(auditoria) else 0


def _publicar_linha_de_base(configuracao: Configuracao, forcar: bool) -> None:
    resultado = fluxo_cdc.publicar_linha_de_base(configuracao, forcar)
    log.info("analítico: %d recebidos, %d inseridos", resultado.recebidas, resultado.inseridas)


def _imprimir(texto: str, saida: Path | None) -> None:
    print(texto)
    if saida:
        saida.parent.mkdir(parents=True, exist_ok=True)
        saida.write_text(texto + "\n", encoding="utf-8")


def _alembic(configuracao: Configuracao) -> Config:
    return Config(str(configuracao.arquivo_alembic))


if __name__ == "__main__":
    sys.exit(main())
