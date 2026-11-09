"""Consumidor do CDC: do slot lógico da origem à tabela Delta de eventos.

Ordem de uma passada: espiar as transações pendentes, gravar no destino sem
duplicar e só então avançar o slot. A falha entre gravar e avançar não perde
nem duplica nada: as transações voltam na passada seguinte e o merge as ignora.

Uma linha que não converte vai para a fila de erros, e o slot avança mesmo assim:
sem isso, ela barraria todas as seguintes até o WAL retido estourar (ADR 0007).
"""

import logging
import time
from dataclasses import dataclass
from datetime import timezone

import psycopg
import pyarrow as pa

from eduinfra import cdc, eventos, fila_de_erros, telemetria_cdc
from eduinfra.configuracao import Configuracao
from eduinfra.contrato_cdc import TABELA_DE_ITENS, TABELA_DE_VISTORIAS
from eduinfra.pgoutput import OPERACAO_INSERT, Insercao, Transacao

log = logging.getLogger(__name__)

COLUNAS_OBRIGATORIAS = tuple(campo.name for campo in eventos.ESQUEMA if not campo.nullable)
# DataError: o banco devolveu uma data que o Python não representa, como -infinity.
ERROS_DE_CONVERSAO = (KeyError, ValueError, TypeError, OverflowError, psycopg.DataError)
ERROS_DO_ARROW = (pa.ArrowException, OverflowError, TypeError)
COLUNA_DE_ID_POR_TABELA = {TABELA_DE_ITENS: "vistoria_id", TABELA_DE_VISTORIAS: "id"}

CABECALHO_DA_VISTORIA = """
select v.id, v.co_entidade, m.sg_uf, v.origem, v.ocorrido_em, v.registrado_em
from vistoria v
join escola e on e.co_entidade = v.co_entidade
join municipio m on m.co_municipio = e.co_municipio
where v.id = any(%s)
"""


class FalhaSimulada(RuntimeError):
    """Interrompe a passada depois de gravar no destino e antes de avançar o slot."""


@dataclass(frozen=True)
class ResultadoDaPassada:
    transacoes: int
    vistorias_vistas: int
    itens_lidos: int
    itens_inseridos: int
    lsn_confirmado: int | None
    itens_rejeitados: int = 0

    @property
    def itens_repetidos(self) -> int:
        return self.itens_lidos - self.itens_inseridos - self.itens_rejeitados


@dataclass(frozen=True)
class Conversao:
    eventos: pa.Table
    rejeicoes: list[dict]


def publicar_linha_de_base(configuracao: Configuracao, forcar: bool = False) -> eventos.ResultadoDaGravacao:
    # O slot nasce antes da leitura da origem: o que entrar durante a carga chega
    # pelo CDC, e o que chegar pelos dois caminhos é descartado pela chave.
    with psycopg.connect(configuracao.dsn, autocommit=True) as conexao:
        cdc.garantir_slot(conexao)

    if not forcar and eventos.contar(configuracao) > 0:
        log.info("tabela analítica já publicada; o CDC cuida do que mudou. Use --forcar para reler a origem.")
        return eventos.ResultadoDaGravacao(0, 0)

    return eventos.carregar_da_origem(configuracao)


def passar_uma_vez(
    configuracao: Configuracao,
    limite_de_mudancas: int | None = None,
    falhar_antes_de_confirmar: bool = False,
    slot: str = cdc.SLOT_PADRAO,
) -> ResultadoDaPassada:
    with psycopg.connect(configuracao.dsn, autocommit=True) as conexao:
        cdc.garantir_slot(conexao, slot)
        inicio_da_leitura = telemetria_cdc.agora(conexao)
        limite_da_leitura = cdc.posicao_persistida(conexao)
        transacoes = cdc.ler_transacoes(
            conexao, slot, limite_de_mudancas=limite_de_mudancas, ate_lsn=limite_da_leitura
        )
        if not transacoes:
            # WAL de tabelas não publicadas também precisa ser percorrido. A posição
            # é a anterior ao peek: um commit concorrente fica para a próxima passada.
            cdc.confirmar(conexao, limite_da_leitura, slot)
            telemetria_cdc.registrar_frescor(configuracao, inicio_da_leitura)
            return ResultadoDaPassada(0, 0, 0, 0, limite_da_leitura)

        fim_da_leitura = telemetria_cdc.agora(conexao)
        itens = [i for t in transacoes for i in t.insercoes if i.tabela == TABELA_DE_ITENS]
        vistorias = sum(1 for t in transacoes for i in t.insercoes if i.tabela == TABELA_DE_VISTORIAS)
        conversao = converter_eventos(conexao, transacoes)
        fim_da_conversao = telemetria_cdc.agora(conexao)

        gravacao = eventos.gravar_sem_duplicar(configuracao, conversao.eventos)
        violacoes = violacoes_do_insert_only(transacoes)
        fila_de_erros.gravar(configuracao, conversao.rejeicoes + violacoes)
        relogio = telemetria_cdc.RelogioDaPassada(inicio_da_leitura, fim_da_leitura, fim_da_conversao, telemetria_cdc.agora(conexao))
        telemetria_cdc.registrar_passada(configuracao, telemetria_cdc.linhas_da_passada(transacoes, relogio, conversao.eventos, conversao.rejeicoes))
        _avisar_problemas(conversao.rejeicoes, violacoes)

        if falhar_antes_de_confirmar:
            raise FalhaSimulada("processo interrompido antes de avançar o slot")

        lsn = transacoes[-1].lsn_fim
        cdc.confirmar(conexao, lsn, slot)

    # Tudo o que confirmou antes do início da leitura já está no Delta ou na fila.
    if limite_de_mudancas is None:
        telemetria_cdc.registrar_frescor(configuracao, inicio_da_leitura)

    return ResultadoDaPassada(len(transacoes), vistorias, len(itens), gravacao.inseridas, lsn, len(conversao.rejeicoes))


def acompanhar(configuracao: Configuracao, intervalo_em_segundos: float, arquivo_de_vida=None) -> None:
    while True:
        _passar_e_registrar(configuracao)
        if arquivo_de_vida is not None:
            arquivo_de_vida.touch()
        time.sleep(intervalo_em_segundos)


def _passar_e_registrar(configuracao: Configuracao) -> None:
    try:
        resultado = passar_uma_vez(configuracao)
    except psycopg.errors.ObjectInUse:
        # A auditoria espia o mesmo slot: é só esperar a próxima passada, sem derrubar o processo.
        log.info("cdc: slot em uso por outra sessão, a passada volta no próximo ciclo")
        return

    if resultado.transacoes:
        log.info(
            "cdc: %d transações, %d itens lidos, %d inseridos, %d na fila de erros, slot em %s",
            resultado.transacoes,
            resultado.itens_lidos,
            resultado.itens_inseridos,
            resultado.itens_rejeitados,
            resultado.lsn_confirmado,
        )


def _avisar_problemas(rejeicoes: list[dict], violacoes: list[dict]) -> None:
    if rejeicoes:
        log.error("cdc: %d itens não converteram e foram para a fila de erros", len(rejeicoes))
    if violacoes:
        log.error("cdc: %d alterações desviaram do insert-only e foram para a fila de erros", len(violacoes))


def montar_eventos(conexao: psycopg.Connection, transacoes: list[Transacao]) -> pa.Table:
    return converter_eventos(conexao, transacoes).eventos


def converter_eventos(conexao: psycopg.Connection, transacoes: list[Transacao]) -> Conversao:
    itens = [(t, i) for t in transacoes for i in t.insercoes if i.tabela == TABELA_DE_ITENS]
    cabecalhos, falhas = _cabecalhos(conexao, _identificadores(itens))
    aprovados, rejeicoes = separar_itens(itens, cabecalhos, falhas)
    tabela, nao_convertidos = montar_tabela(aprovados)

    return Conversao(tabela, rejeicoes + nao_convertidos)


def _identificadores(itens: list[tuple[Transacao, Insercao]]) -> set[int]:
    return {identificador for _, item in itens if (identificador := id_da_vistoria(item.valores, TABELA_DE_ITENS)) is not None}


def id_da_vistoria(valores: dict, tabela: str) -> int | None:
    try:
        return int(valores[COLUNA_DE_ID_POR_TABELA[tabela]])
    except (KeyError, ValueError, TypeError):
        return None


def _cabecalhos(conexao: psycopg.Connection, identificadores: set[int]) -> tuple[dict[int, tuple], dict[int, Exception]]:
    if not identificadores:
        return {}, {}

    try:
        registros = conexao.execute(CABECALHO_DA_VISTORIA, (sorted(identificadores),)).fetchall()
    except psycopg.DataError:
        # Uma data fora do alcance do Python derruba a leitura em bloco; uma a uma, só a vistoria afetada é rejeitada.
        return _cabecalhos_um_a_um(conexao, identificadores)

    return {registro[0]: registro for registro in registros}, {}


def _cabecalhos_um_a_um(conexao: psycopg.Connection, identificadores: set[int]) -> tuple[dict[int, tuple], dict[int, Exception]]:
    cabecalhos, falhas = {}, {}
    for identificador in identificadores:
        try:
            for registro in conexao.execute(CABECALHO_DA_VISTORIA, ([identificador],)).fetchall():
                cabecalhos[identificador] = registro
        except psycopg.DataError as erro:
            falhas[identificador] = erro

    return cabecalhos, falhas


def separar_itens(
    itens: list[tuple[Transacao, Insercao]],
    cabecalhos: dict[int, tuple],
    falhas: dict[int, Exception],
) -> tuple[list[tuple[Transacao, Insercao, dict]], list[dict]]:
    aprovados, rejeicoes = [], []
    for transacao, item in itens:
        try:
            aprovados.append((transacao, item, _converter_item(transacao, item, cabecalhos, falhas)))
        except ERROS_DE_CONVERSAO as erro:
            rejeicoes.append(_rejeicao(transacao, item, erro))

    return aprovados, rejeicoes


def montar_tabela(aprovados: list[tuple[Transacao, Insercao, dict]]) -> tuple[pa.Table, list[dict]]:
    try:
        return pa.Table.from_pylist([linha for _, _, linha in aprovados], schema=eventos.ESQUEMA), []
    except ERROS_DO_ARROW:
        return _montar_linha_a_linha(aprovados)


def _montar_linha_a_linha(aprovados: list[tuple[Transacao, Insercao, dict]]) -> tuple[pa.Table, list[dict]]:
    tabelas, rejeicoes = [], []
    for transacao, item, linha in aprovados:
        try:
            tabelas.append(pa.Table.from_pylist([linha], schema=eventos.ESQUEMA))
        except ERROS_DO_ARROW as erro:
            rejeicoes.append(_rejeicao(transacao, item, erro))

    return pa.concat_tables(tabelas) if tabelas else eventos.ESQUEMA.empty_table(), rejeicoes


def violacoes_do_insert_only(transacoes: list[Transacao]) -> list[dict]:
    return [
        fila_de_erros.montar_linha(
            transacao.lsn_fim,
            transacao.confirmada_em,
            alteracao.tabela,
            alteracao.operacao,
            id_da_vistoria(alteracao.valores, alteracao.tabela),
            alteracao.valores.get("item_codigo"),
            fila_de_erros.MOTIVO_DE_VIOLACAO,
        )
        for transacao in transacoes
        for alteracao in transacao.alteracoes
    ]


def _rejeicao(transacao: Transacao, item: Insercao, erro: Exception) -> dict:
    return fila_de_erros.montar_linha(
        transacao.lsn_fim,
        transacao.confirmada_em,
        item.tabela,
        OPERACAO_INSERT,
        id_da_vistoria(item.valores, item.tabela),
        item.valores.get("item_codigo"),
        f"{type(erro).__name__}: {erro}",
    )


def _converter_item(transacao: Transacao, item: Insercao, cabecalhos: dict[int, tuple], falhas: dict[int, Exception]) -> dict:
    vistoria_id = int(item.valores["vistoria_id"])
    if vistoria_id in falhas:
        raise falhas[vistoria_id]

    return _evento(transacao, item, cabecalhos[vistoria_id])


def _evento(transacao: Transacao, item: Insercao, cabecalho: tuple) -> dict:
    _, co_entidade, sg_uf, origem, ocorrido_em, registrado_em = cabecalho
    quantidade = item.valores["quantidade"]

    evento = {
        "vistoria_id": int(item.valores["vistoria_id"]),
        "item_codigo": item.valores["item_codigo"],
        "co_entidade": co_entidade,
        "sg_uf": sg_uf,
        "origem": origem,
        "ocorrido_em": ocorrido_em,
        "registrado_em": registrado_em,
        "situacao": item.valores["situacao"],
        "quantidade": None if quantidade is None else int(quantidade),
        "mes_ocorrencia": ocorrido_em.astimezone(timezone.utc).strftime("%Y-%m"),
        "lsn_origem": transacao.lsn_fim,
    }
    # O Arrow aceita o nulo aqui e só o merge recusa, derrubando o lote inteiro.
    nulas = [coluna for coluna in COLUNAS_OBRIGATORIAS if evento[coluna] is None]
    if nulas:
        raise ValueError(f"coluna obrigatória nula: {', '.join(nulas)}")

    return evento

