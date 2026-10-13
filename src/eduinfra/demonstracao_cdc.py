"""Demonstração do CDC e da semântica de entrega (issues #25 e #26).

Insere vistorias na origem e mostra o efeito atravessando o fluxo até a tabela
Delta: entrega normal, retomada depois de o consumidor ficar parado, falha entre
gravar e confirmar, e reprocessamento do mesmo intervalo. A origem é
insert-only: as vistorias da demonstração ficam no banco, marcadas na
observação. Rode num ambiente de desenvolvimento.
"""

import psycopg

from eduinfra import cdc, eventos, fluxo_cdc
from eduinfra.configuracao import Configuracao
from eduinfra.vistorias_de_teste import contar_itens_da_origem, inserir_vistorias

MATRICULA = "DEMO-E2-25"
OBSERVACAO = "Demonstração da E2 (issues #25 e #26)"
ITENS_POR_VISTORIA = 17


def demonstrar(configuracao: Configuracao) -> str:
    saida = ["# CDC e semântica de entrega", ""]

    with psycopg.connect(configuracao.dsn, autocommit=True) as conexao:
        cdc.garantir_slot(conexao)
        saida += _etapa_inicial(configuracao, conexao)
        saida += _etapa_entrega_normal(configuracao, conexao)
        saida += _etapa_parar_e_retomar(configuracao, conexao)
        saida += _etapa_falha_antes_de_confirmar(configuracao, conexao)
        saida += _etapa_reprocessar_intervalo(configuracao, conexao)
        saida += _etapa_final(configuracao, conexao)

    return "\n".join(saida)


def _passada(configuracao: Configuracao, **opcoes) -> fluxo_cdc.ResultadoDaPassada:
    return fluxo_cdc.passar_uma_vez(configuracao, **opcoes)


def _linha_do_estado(configuracao: Configuracao, conexao: psycopg.Connection) -> str:
    slot = cdc.estado_do_slot(conexao)
    return (
        f"origem {contar_itens_da_origem(conexao)} itens, "
        f"Delta {eventos.contar(configuracao)} itens, "
        f"slot {slot.situacao_do_wal} com {slot.atraso_em_bytes} bytes por entregar"
    )


def _etapa_inicial(configuracao: Configuracao, conexao: psycopg.Connection) -> list[str]:
    resultado = _passada(configuracao)

    return [
        "## 0. Estado de partida",
        "",
        f"Pendências antigas entregues antes de começar: {resultado.itens_lidos} itens lidos, "
        f"{resultado.itens_inseridos} inseridos.",
        _linha_do_estado(configuracao, conexao),
        "",
    ]


def _etapa_entrega_normal(configuracao: Configuracao, conexao: psycopg.Connection) -> list[str]:
    antes = eventos.contar(configuracao)
    inserir_vistorias(conexao, 3, MATRICULA, OBSERVACAO)
    resultado = _passada(configuracao)

    return [
        "## 1. Entrega normal: 3 vistorias novas na origem",
        "",
        f"Delta antes: {antes}. Lidos pelo CDC: {resultado.itens_lidos}. Inseridos: {resultado.itens_inseridos}.",
        f"Delta depois: {eventos.contar(configuracao)} (esperado {antes + 3 * ITENS_POR_VISTORIA}).",
        "",
    ]


def _etapa_parar_e_retomar(configuracao: Configuracao, conexao: psycopg.Connection) -> list[str]:
    antes = eventos.contar(configuracao)
    inserir_vistorias(conexao, 2, MATRICULA, OBSERVACAO)
    inserir_vistorias(conexao, 2, MATRICULA, OBSERVACAO)
    parado = _linha_do_estado(configuracao, conexao)
    resultado = _passada(configuracao)

    return [
        "## 2. Consumidor parado, 4 vistorias inseridas, consumidor religado",
        "",
        f"Com o consumidor parado: {parado}.",
        f"Ao religar: {resultado.transacoes} transações, {resultado.itens_inseridos} itens inseridos.",
        f"Delta: {antes} -> {eventos.contar(configuracao)} (esperado {antes + 4 * ITENS_POR_VISTORIA}).",
        "",
    ]


def _etapa_falha_antes_de_confirmar(configuracao: Configuracao, conexao: psycopg.Connection) -> list[str]:
    antes = eventos.contar(configuracao)
    inserir_vistorias(conexao, 2, MATRICULA, OBSERVACAO)

    try:
        _passada(configuracao, falhar_antes_de_confirmar=True)
    except fluxo_cdc.FalhaSimulada:
        pass

    apos_falha = _linha_do_estado(configuracao, conexao)
    retomada = _passada(configuracao)

    return [
        "## 3. Falha depois de gravar e antes de confirmar o slot",
        "",
        f"Depois da falha: {apos_falha}.",
        "O destino já tem as linhas, mas o slot ainda não avançou: o fluxo é no mínimo uma vez.",
        f"Na retomada: {retomada.itens_lidos} itens reentregues, {retomada.itens_inseridos} inseridos, "
        f"{retomada.itens_repetidos} descartados pela chave (vistoria_id, item_codigo).",
        f"Delta: {antes} -> {eventos.contar(configuracao)} (esperado {antes + 2 * ITENS_POR_VISTORIA}).",
        "",
    ]


def _etapa_reprocessar_intervalo(configuracao: Configuracao, conexao: psycopg.Connection) -> list[str]:
    inserir_vistorias(conexao, 1, MATRICULA, OBSERVACAO)
    transacoes = cdc.ler_transacoes(conexao)
    lote = fluxo_cdc.montar_eventos(conexao, transacoes)
    primeira = eventos.gravar_sem_duplicar(configuracao, lote)
    segunda = eventos.gravar_sem_duplicar(configuracao, lote)
    cdc.confirmar(conexao, transacoes[-1].lsn_fim)

    return [
        "## 4. Reprocessar o mesmo intervalo de eventos duas vezes",
        "",
        f"1ª gravação: {primeira.recebidas} recebidos, {primeira.inseridas} inseridos.",
        f"2ª gravação: {segunda.recebidas} recebidos, {segunda.inseridas} inseridos.",
        "",
    ]


def _etapa_final(configuracao: Configuracao, conexao: psycopg.Connection) -> list[str]:
    return ["## 5. Estado final", "", _linha_do_estado(configuracao, conexao), ""]
