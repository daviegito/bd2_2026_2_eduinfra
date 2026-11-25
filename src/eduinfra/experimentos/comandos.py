"""Comandos que medem, comparam ou demonstram; nenhum deles faz parte do pipeline agendado."""

import argparse
import logging
from dataclasses import replace

from eduinfra.cdc.comandos import auditar
from eduinfra.experimentos import (
    caracterizacao,
    cobertura_enem,
    comparacao_compressao,
    comparacao_ingestao,
    comparacao_latencia,
    comparacao_modelagem,
    comparacao_particionamento,
    demonstracao_cdc,
    modelo_analitico,
    otimizacao_consulta,
)
from eduinfra.gold import comandos as comandos_da_gold
from eduinfra.gold import frescor
from eduinfra.nucleo.configuracao import Configuracao
from eduinfra.nucleo.saida import imprimir

log = logging.getLogger(__name__)


def _como_bancada(configuracao: Configuracao) -> Configuracao:
    return replace(configuracao, dsn=configuracao.dsn_cdc_bancada or configuracao.dsn)


def caracterizar(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    medidas = caracterizacao.medir(configuracao.dsn)
    tamanhos = caracterizacao.tamanhos_em_disco(configuracao.dsn)
    print(caracterizacao.formatar_markdown(medidas, tamanhos))
    return 0


def medir_cobertura_enem(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    imprimir(cobertura_enem.formatar_markdown(cobertura_enem.medir(configuracao)), opcoes.saida)
    return 0


def comparar_modelagem(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    print(comparacao_modelagem.formatar_markdown(comparacao_modelagem.comparar(configuracao.dsn)))
    return 0


def comparar_compressao(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    silver = configuracao.diretorio_dados / "silver"
    print(comparacao_compressao.formatar_markdown(comparacao_compressao.comparar(silver, opcoes.repeticoes)))
    return 0


def comparar_particionamento(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    silver = configuracao.diretorio_dados / "silver"
    comparacao = comparacao_particionamento.comparar(silver, opcoes.repeticoes, opcoes.lotes)
    print(comparacao_particionamento.formatar_markdown(comparacao))
    return 0


def comparar_ingestao(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    capturas, ociosa = comparacao_ingestao.medir(_como_bancada(configuracao))
    print(comparacao_ingestao.formatar_markdown(capturas, ociosa))
    return 0


def medir_cdc(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    configuracao = _como_bancada(configuracao)
    print(comparacao_latencia.formatar_cdc(comparacao_latencia.medir_cdc(configuracao, opcoes.repeticoes)))
    print(comparacao_latencia.formatar_ordem(comparacao_latencia.medir_ordem(configuracao, opcoes.repeticoes)))
    return auditar(configuracao, opcoes.sla_frescor)


def medir_recarga(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    print(comparacao_latencia.formatar_recarga(comparacao_latencia.medir_recarga(_como_bancada(configuracao), opcoes.repeticoes)))
    return 0


def demonstrar_cdc(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    print(demonstracao_cdc.demonstrar(_como_bancada(configuracao)))
    return 0


def demonstrar_atraso(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    from eduinfra.experimentos.demonstracao_atraso import demonstrar

    print(demonstrar(configuracao))
    return 0


def demonstrar_frescor(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    print(frescor.demonstrar(configuracao.dsn))
    return 0


def construir_gold(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    # Construtor SQL anterior ao dbt, mantido como opção nula do ADR 0005; o pipeline agendado usa o dbt.
    # A opção nula é executada pelo orquestrador: ele prepara a fonte e entra
    # como dbt só na etapa que escreve a Gold.
    alvo = replace(configuracao, dsn=configuracao.dsn_orquestrador or configuracao.dsn)
    resumo = modelo_analitico.construir(alvo)
    fonte = resumo.fonte
    log.info(
        "fonte de eventos: versão %d da tabela Delta, %d eventos; origem com %d; %d só na origem, %d só na fonte",
        fonte.versao_delta, fonte.na_fonte, fonte.na_origem, fonte.so_na_origem, fonte.so_na_fonte,
    )
    for tabela, linhas in resumo.linhas.items():
        log.info("gold.%s: %d linhas", tabela, linhas)
    # Cada construção registra o frescor; a violação só reprova em medir-frescor.
    frescor.medir(configuracao.dsn, comando="construir-gold")
    return 0 if comandos_da_gold.auditar_gold() else 1


def comparar_granularidades(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    texto = modelo_analitico.formatar_markdown(modelo_analitico.comparar(configuracao.dsn, opcoes.repeticoes))
    imprimir(texto, opcoes.saida)
    return 0


def comparar_consulta(opcoes: argparse.Namespace, configuracao: Configuracao) -> int:
    comparacao = otimizacao_consulta.comparar(configuracao.dsn, opcoes.repeticoes)
    otimizacao_consulta.gravar_planos(comparacao, opcoes.pasta_dos_planos)
    print(otimizacao_consulta.formatar_markdown(comparacao))
    return 0 if comparacao.mesmo_resultado else 1


COMANDOS = {
    "caracterizar": caracterizar,
    "medir-cobertura-enem": medir_cobertura_enem,
    "comparar-modelagem": comparar_modelagem,
    "comparar-compressao": comparar_compressao,
    "comparar-particionamento": comparar_particionamento,
    "comparar-ingestao": comparar_ingestao,
    "medir-cdc": medir_cdc,
    "medir-recarga": medir_recarga,
    "demonstrar-cdc": demonstrar_cdc,
    "demonstrar-atraso": demonstrar_atraso,
    "demonstrar-frescor": demonstrar_frescor,
    "construir-gold": construir_gold,
    "comparar-granularidades": comparar_granularidades,
    "comparar-consulta": comparar_consulta,
}
