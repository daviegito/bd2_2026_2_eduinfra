"""Consumidor do CDC: do slot lógico da origem à tabela Delta de eventos.

Ordem de uma passada: espiar as transações pendentes, gravar no destino sem
duplicar e só então avançar o slot. A falha entre gravar e avançar não perde
nem duplica nada: as transações voltam na passada seguinte e o merge as ignora.
"""

import logging
import time
from dataclasses import dataclass
from datetime import timezone

import psycopg
import pyarrow as pa

from eduinfra import cdc, eventos
from eduinfra.configuracao import Configuracao
from eduinfra.pgoutput import Insercao, Transacao

log = logging.getLogger(__name__)

TABELA_DE_ITENS = "vistoria_item"
TABELA_DE_VISTORIAS = "vistoria"

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

    @property
    def itens_repetidos(self) -> int:
        return self.itens_lidos - self.itens_inseridos


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
        transacoes = cdc.ler_transacoes(conexao, slot, limite_de_mudancas=limite_de_mudancas)
        if not transacoes:
            return ResultadoDaPassada(0, 0, 0, 0, None)

        itens = [i for t in transacoes for i in t.insercoes if i.tabela == TABELA_DE_ITENS]
        vistorias = sum(1 for t in transacoes for i in t.insercoes if i.tabela == TABELA_DE_VISTORIAS)
        gravacao = eventos.gravar_sem_duplicar(configuracao, montar_eventos(conexao, transacoes))

        if falhar_antes_de_confirmar:
            raise FalhaSimulada("processo interrompido antes de avançar o slot")

        lsn = transacoes[-1].lsn_fim
        cdc.confirmar(conexao, lsn, slot)

    return ResultadoDaPassada(len(transacoes), vistorias, len(itens), gravacao.inseridas, lsn)


def acompanhar(configuracao: Configuracao, intervalo_em_segundos: float, arquivo_de_vida=None) -> None:
    while True:
        resultado = passar_uma_vez(configuracao)
        if resultado.transacoes:
            log.info(
                "cdc: %d transações, %d itens lidos, %d inseridos, slot em %s",
                resultado.transacoes,
                resultado.itens_lidos,
                resultado.itens_inseridos,
                resultado.lsn_confirmado,
            )
        if arquivo_de_vida is not None:
            arquivo_de_vida.touch()
        time.sleep(intervalo_em_segundos)


def montar_eventos(conexao: psycopg.Connection, transacoes: list[Transacao]) -> pa.Table:
    itens = [(t, i) for t in transacoes for i in t.insercoes if i.tabela == TABELA_DE_ITENS]
    cabecalhos = _cabecalhos(conexao, {int(i.valores["vistoria_id"]) for _, i in itens})
    linhas = [_evento(transacao, item, cabecalhos[int(item.valores["vistoria_id"])]) for transacao, item in itens]

    return pa.Table.from_pylist(linhas, schema=eventos.ESQUEMA)


def _cabecalhos(conexao: psycopg.Connection, identificadores: set[int]) -> dict[int, tuple]:
    if not identificadores:
        return {}

    registros = conexao.execute(CABECALHO_DA_VISTORIA, (sorted(identificadores),)).fetchall()

    return {registro[0]: registro for registro in registros}


def _evento(transacao: Transacao, item: Insercao, cabecalho: tuple) -> dict:
    _, co_entidade, sg_uf, origem, ocorrido_em, registrado_em = cabecalho
    quantidade = item.valores["quantidade"]

    return {
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

