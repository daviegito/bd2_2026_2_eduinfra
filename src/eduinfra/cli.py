import argparse
import logging
import sys
from pathlib import Path

from eduinfra.cdc import auditoria_cdc, recuperacao_cdc
from eduinfra.cdc import comandos as cdc
from eduinfra.consumo import comandos as consumo
from eduinfra.experimentos import comandos as experimentos
from eduinfra.gold import comandos as gold
from eduinfra.gold import frescor
from eduinfra.ingestao import comandos as ingestao
from eduinfra.lake import comandos as lake
from eduinfra.nucleo.configuracao import Configuracao, carregar_configuracao
from eduinfra.origem import comandos as origem
from eduinfra.seguranca import comandos as seguranca

COMANDOS = {
    **origem.COMANDOS,
    **ingestao.COMANDOS,
    **cdc.COMANDOS,
    **lake.COMANDOS,
    **gold.COMANDOS,
    **experimentos.COMANDOS,
    **consumo.COMANDOS,
    **seguranca.COMANDOS,
}


def main(argumentos: list[str] | None = None) -> int:
    parser = montar_parser()
    opcoes = parser.parse_args(argumentos)

    logging.basicConfig(
        level=logging.DEBUG if opcoes.verboso else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    configuracao = carregar_configuracao()

    if opcoes.comando == "reprocessar-janela" and not opcoes.mes:
        parser.error("reprocessar-janela exige --mes AAAA-MM")

    if opcoes.comando == "tudo":
        return _tudo(opcoes, configuracao)

    return COMANDOS[opcoes.comando](opcoes, configuracao)


def _tudo(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    # A migração é um passo administrativo separado (serviço migracao).
    ingestao.ingerir(opcoes, configuracao)
    cdc.publicar_linha_de_base(configuracao, opcoes.forcar)
    experimentos.caracterizar(opcoes, configuracao)
    return lake.publicar_analitica(opcoes, configuracao)


def montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eduinfra", description="Origem OLTP do EduInfra AI")

    parser.add_argument("comando", choices=("migrar", "reverter", "carregar", "ingerir", "caracterizar", "comparar-modelagem", "comparar-compressao",
                                           "processar-janelas", "reprocessar-janela", "demonstrar-atraso", "tudo", "gerar_amostra",
                                           "preparar-slot-cdc", "publicar-analitico", "cdc", "demonstrar-cdc", "comparar-ingestao",
                                           "publicar-analitica", "comparar-particionamento", "medir-cdc", "medir-recarga", "auditar-cdc",
                                           "reprocessar-fila-cdc", "tratar-desvio-cdc", "comparar-consulta", "construir-gold", "comparar-granularidades", "medir-frescor", "demonstrar-frescor",
                                           "ingerir-enem", "medir-cobertura-enem", "gerar-painel",
                                           "demonstrar-acesso", "anonimizar-tecnico"))
    parser.add_argument("--repeticoes", type=int, default=5, help="repetições de cada medição em comparar-compressao, comparar-particionamento, medir-cdc, medir-recarga e comparar-consulta")
    parser.add_argument("--pasta-dos-planos", type=Path, default=Path("docs/explain"), help="onde comparar-consulta grava os planos de execução")
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
    parser.add_argument("--responsavel", help="identificador operacional sem nome/e-mail: operador-01")
    parser.add_argument("--motivo", choices=recuperacao_cdc.MOTIVOS, help="motivo padronizado do tratamento")
    parser.add_argument("--lsn", type=int, help="LSN decimal da ocorrência na fila; exige --chave")
    parser.add_argument("--chave", help="chave exata da ocorrência na fila; exige --lsn")
    parser.add_argument("--matricula", help="em anonimizar-tecnico, matrícula original do técnico que pediu a eliminação")
    parser.add_argument("--verboso", action="store_true")
    return parser


if __name__ == "__main__":
    sys.exit(main())
