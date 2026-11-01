"""Auditoria do destino do CDC contra a origem (ADR 0007): checksum, balanço, ordem, esquema e frescor.

Só lê, e pode rodar com o consumidor ligado. O checksum cobre a tabela inteira porque só
uma fração das linhas veio do CDC e uma amostra uniforme quase não testaria esse caminho.
"""

import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

import duckdb
import psycopg
import pyarrow as pa

from eduinfra import cdc, contrato_cdc, eventos, fila_de_erros, fluxo_cdc, telemetria_cdc
from eduinfra.configuracao import Configuracao
from eduinfra.contrato_cdc import TABELA_DE_ITENS, Desvio
from eduinfra.pgoutput import OPERACAO_DELETE, OPERACAO_INSERT, OPERACAO_TRUNCATE, OPERACAO_UPDATE, Transacao
from eduinfra.telemetria_cdc import AMOSTRAS_MINIMAS, MEDIDAS, ResumoDaOrdem, ResumoDeTempos, decimal_br

log = logging.getLogger(__name__)

LIMITE_DE_MEMORIA = "1GB"
SLA_DE_FRESCOR_S = 60.0
TENTATIVAS_DE_ESPIAR = 5
ESPERA_ENTRE_TENTATIVAS_S = 0.5
SLOT_INVALIDADO = "lost"

SO_NA_ORIGEM = "so_na_origem"
SO_NO_DELTA = "so_no_delta"
IGUAIS = "iguais"
DIFERENTES = "diferentes"

# lsn_origem fica de fora: a origem não o conhece, e ele distingue os caminhos em vez de descrever o evento.
IMPRESSAO = """
md5_number(concat_ws('|', vistoria_id, item_codigo, co_entidade, sg_uf, origem, ocorrido_em::varchar,
                     registrado_em::varchar, situacao, coalesce(quantidade::varchar, 'nulo'), mes_ocorrencia))
"""

ESQUEMA_DAS_PENDENCIAS = pa.schema([pa.field("vistoria_id", pa.int64()), pa.field("item_codigo", pa.string())])

OPERACOES_DO_SALDO = (
    (OPERACAO_INSERT, "itens"),
    (OPERACAO_UPDATE, "atualizacoes"),
    (OPERACAO_DELETE, "exclusoes"),
    (OPERACAO_TRUNCATE, "truncamentos"),
)


@dataclass(frozen=True)
class ComparacaoDeLinhas:
    iguais_sem_lsn: int
    iguais_com_lsn: int
    diferentes_sem_lsn: int
    diferentes_com_lsn: int
    pendentes: int
    na_fila: int
    sem_explicacao: int
    so_no_delta: int
    divergencias_por_dia: list[tuple[str, str, int]]


@dataclass(frozen=True)
class SaldoDaOperacao:
    operacao: str
    lidas_do_wal: int
    aplicadas: int
    na_fila: int
    pendentes: int

    @property
    def ja_estavam_no_delta(self) -> int:
        return self.lidas_do_wal - self.aplicadas - self.na_fila


@dataclass(frozen=True)
class Auditoria:
    linhas: ComparacaoDeLinhas
    saldo: list[SaldoDaOperacao]
    ordem: ResumoDaOrdem
    latencias: dict[str, ResumoDeTempos]
    desvios_no_catalogo: list[Desvio]
    desvios_vistos_no_wal: dict[str, int]
    fila_por_motivo: dict[str, int]
    drenado_em: datetime | None
    frescor_s: float | None
    sla_de_frescor_s: float
    slot: cdc.EstadoDoSlot


def auditar(configuracao: Configuracao, slot: str = cdc.SLOT_PADRAO, sla_de_frescor_s: float = SLA_DE_FRESCOR_S) -> Auditoria:
    fila = fila_de_erros.ler(configuracao)
    with psycopg.connect(configuracao.dsn, autocommit=True) as conexao:
        estado_do_slot = cdc.estado_do_slot(conexao, slot)
        analitico = duckdb.connect()
        try:
            _preparar(analitico, configuracao)
            # A origem é lida antes de espiar o slot, e o Delta depois: uma linha que o
            # consumidor entrega no meio da auditoria não aparece como perdida.
            _fotografar_origem(analitico)
            pendencias = _ler_pendencias(conexao, slot, estado_do_slot)
            linhas = _comparar(analitico, conexao, configuracao, pendencias, fila)
            telemetria = telemetria_cdc.ler_telemetria(configuracao)
            aplicadas = _contar_aplicadas(analitico, configuracao, telemetria)
        finally:
            analitico.close()

        drenado_em = telemetria_cdc.ler_frescor(configuracao)
        agora = telemetria_cdc.agora(conexao)
        desvios = contrato_cdc.desvios_no_catalogo(conexao)

    return Auditoria(
        linhas=linhas,
        saldo=calcular_saldo(telemetria, aplicadas, fila, pendencias),
        ordem=telemetria_cdc.resumir_ordem(telemetria),
        latencias=resumir_latencias(telemetria),
        desvios_no_catalogo=desvios,
        desvios_vistos_no_wal=contar_desvios_vistos(telemetria),
        fila_por_motivo=contar_motivos(fila),
        drenado_em=drenado_em,
        frescor_s=None if drenado_em is None else (agora - drenado_em).total_seconds(),
        sla_de_frescor_s=sla_de_frescor_s,
        slot=estado_do_slot,
    )


def _preparar(analitico: duckdb.DuckDBPyConnection, configuracao: Configuracao) -> None:
    analitico.execute("install postgres; load postgres; install delta; load delta;")
    analitico.execute(f"set memory_limit = '{LIMITE_DE_MEMORIA}'")
    dsn = configuracao.dsn.replace("'", "''")
    analitico.execute(f"attach '{dsn}' as origem (type postgres, read_only)")


def _fotografar_origem(analitico: duckdb.DuckDBPyConnection) -> None:
    analitico.execute(
        f"""
        create temp table na_origem as
        select vistoria_id, item_codigo, registrado_em, {IMPRESSAO} as impressao
        from ({eventos.LINHA_DE_BASE})
        """
    )


def _ler_pendencias(conexao: psycopg.Connection, slot: str, estado: cdc.EstadoDoSlot) -> list[Transacao]:
    if not estado.existe or estado.situacao_do_wal == SLOT_INVALIDADO:
        return []

    for _ in range(TENTATIVAS_DE_ESPIAR):
        try:
            return cdc.ler_transacoes(conexao, slot)
        except psycopg.errors.ObjectInUse:
            time.sleep(ESPERA_ENTRE_TENTATIVAS_S)

    raise RuntimeError(f"o slot {slot} ficou em uso durante toda a auditoria")


def _chaves_pendentes(pendencias: list[Transacao]) -> pa.Table:
    linhas = [
        {"vistoria_id": fluxo_cdc.id_da_vistoria(item.valores, TABELA_DE_ITENS), "item_codigo": item.valores.get("item_codigo")}
        for transacao in pendencias
        for item in transacao.insercoes
        if item.tabela == TABELA_DE_ITENS
    ]

    return pa.Table.from_pylist(linhas, schema=ESQUEMA_DAS_PENDENCIAS)


def _comparar(
    analitico: duckdb.DuckDBPyConnection,
    conexao: psycopg.Connection,
    configuracao: Configuracao,
    pendencias: list[Transacao],
    fila: pa.Table,
) -> ComparacaoDeLinhas:
    destino = _delta_scan(configuracao)
    analitico.execute(
        f"""
        create temp table comparacao as
        select coalesce(o.vistoria_id, d.vistoria_id) as vistoria_id,
               coalesce(o.item_codigo, d.item_codigo) as item_codigo,
               strftime(coalesce(o.registrado_em, d.registrado_em) at time zone 'UTC', '%Y-%m-%d') as dia,
               d.lsn_origem is not null as veio_do_cdc,
               case when o.impressao is null then '{SO_NO_DELTA}'
                    when d.impressao is null then '{SO_NA_ORIGEM}'
                    when o.impressao = d.impressao then '{IGUAIS}'
                    else '{DIFERENTES}' end as situacao
        from na_origem o
        full join (select vistoria_id, item_codigo, lsn_origem, registrado_em, {IMPRESSAO} as impressao from {destino}) d
            using (vistoria_id, item_codigo)
        """
    )
    pendentes = _chaves_pendentes(pendencias)
    na_fila = fila.select(["vistoria_id", "item_codigo", "operacao"])
    analitico.register("chaves_pendentes", pendentes)
    analitico.register("chaves_na_fila", na_fila)

    iguais_sem, iguais_com, diferentes_sem, diferentes_com = analitico.execute(
        f"""
        select count(*) filter (where situacao = '{IGUAIS}' and not veio_do_cdc),
               count(*) filter (where situacao = '{IGUAIS}' and veio_do_cdc),
               count(*) filter (where situacao = '{DIFERENTES}' and not veio_do_cdc),
               count(*) filter (where situacao = '{DIFERENTES}' and veio_do_cdc)
        from comparacao
        """
    ).fetchone()
    pendentes_na_origem, em_fila, sem_explicacao = analitico.execute(
        f"""
        select count(*) filter (where p.vistoria_id is not null),
               count(*) filter (where p.vistoria_id is null and f.vistoria_id is not null),
               count(*) filter (where p.vistoria_id is null and f.vistoria_id is null)
        from comparacao c
        left join (select distinct vistoria_id, item_codigo from chaves_pendentes) p using (vistoria_id, item_codigo)
        left join (select distinct vistoria_id, item_codigo from chaves_na_fila where operacao = '{OPERACAO_INSERT}') f
            using (vistoria_id, item_codigo)
        where c.situacao = '{SO_NA_ORIGEM}'
        """
    ).fetchone()
    divergencias = analitico.execute(
        f"select dia, situacao, count(*) from comparacao where situacao <> '{IGUAIS}' group by 1, 2 order by 1, 2"
    ).fetchall()

    return ComparacaoDeLinhas(
        iguais_sem_lsn=iguais_sem,
        iguais_com_lsn=iguais_com,
        diferentes_sem_lsn=diferentes_sem,
        diferentes_com_lsn=diferentes_com,
        pendentes=pendentes_na_origem,
        na_fila=em_fila,
        sem_explicacao=sem_explicacao,
        so_no_delta=_confirmar_so_no_delta(analitico, conexao),
        divergencias_por_dia=divergencias,
    )


def _delta_scan(configuracao: Configuracao) -> str:
    return f"delta_scan('{eventos.garantir_tabela(configuracao).as_posix()}')"


def _confirmar_so_no_delta(analitico: duckdb.DuckDBPyConnection, conexao: psycopg.Connection) -> int:
    # Uma linha nova pode ter sido entregue depois da fotografia da origem: só conta se de fato sumiu de lá.
    chaves = analitico.execute(f"select vistoria_id, item_codigo from comparacao where situacao = '{SO_NO_DELTA}'").fetchall()
    if not chaves:
        return 0

    identificadores = sorted({vistoria_id for vistoria_id, _ in chaves})
    existentes = set(
        conexao.execute(
            "select vistoria_id, item_codigo from vistoria_item where vistoria_id = any(%s)",
            (identificadores,),
        ).fetchall()
    )

    return sum(1 for chave in chaves if chave not in existentes)


def _contar_aplicadas(analitico: duckdb.DuckDBPyConnection, configuracao: Configuracao, telemetria: list[dict]) -> int:
    if not telemetria:
        return 0

    lsn_inicial = min(linha["lsn_fim"] for linha in telemetria)
    return analitico.execute(f"select count(*) from {_delta_scan(configuracao)} where lsn_origem >= {lsn_inicial}").fetchone()[0]


def calcular_saldo(telemetria: list[dict], aplicadas: int, fila: pa.Table, pendencias: list[Transacao]) -> list[SaldoDaOperacao]:
    por_lsn = {linha["lsn_fim"]: linha for linha in telemetria}
    na_fila = Counter(fila.column("operacao").to_pylist())
    pendentes = _contar_pendencias(pendencias)

    return [
        SaldoDaOperacao(
            operacao=operacao,
            lidas_do_wal=sum(linha[coluna] for linha in por_lsn.values()),
            aplicadas=aplicadas if operacao == OPERACAO_INSERT else 0,
            na_fila=na_fila[operacao],
            pendentes=pendentes[operacao],
        )
        for operacao, coluna in OPERACOES_DO_SALDO
    ]


def _contar_pendencias(pendencias: list[Transacao]) -> Counter:
    contagem = Counter(alteracao.operacao for transacao in pendencias for alteracao in transacao.alteracoes)
    contagem[OPERACAO_INSERT] = sum(1 for transacao in pendencias for item in transacao.insercoes if item.tabela == TABELA_DE_ITENS)

    return contagem


def resumir_latencias(telemetria: list[dict]) -> dict[str, ResumoDeTempos]:
    if len(telemetria) < AMOSTRAS_MINIMAS:
        return {}

    return {campo: telemetria_cdc.resumir([linha[campo] for linha in telemetria]) for campo, _ in MEDIDAS}


def contar_desvios_vistos(telemetria: list[dict]) -> dict[str, int]:
    return dict(
        Counter(
            desvio
            for linha in telemetria
            if linha["desvios_de_esquema"]
            for desvio in linha["desvios_de_esquema"].split(telemetria_cdc.SEPARADOR_DE_DESVIOS)
        )
    )


def contar_motivos(fila: pa.Table) -> dict[str, int]:
    return dict(Counter(f"{linha['operacao']}, {linha['motivo'].split(':')[0]}" for linha in fila.to_pylist()))


def violacoes(auditoria: Auditoria) -> list[str]:
    linhas = auditoria.linhas
    diferentes = linhas.diferentes_sem_lsn + linhas.diferentes_com_lsn
    desvios_do_insert_only = sum(saldo.lidas_do_wal + saldo.pendentes for saldo in auditoria.saldo if saldo.operacao != OPERACAO_INSERT)
    candidatas = (
        (diferentes, f"{diferentes} linhas com conteúdo diferente entre a origem e o Delta"),
        (linhas.sem_explicacao, f"{linhas.sem_explicacao} linhas da origem fora do Delta, do slot e da fila de erros"),
        (linhas.so_no_delta, f"{linhas.so_no_delta} linhas no Delta que não existem mais na origem"),
        (linhas.na_fila, f"{linhas.na_fila} itens parados na fila de erros"),
        (desvios_do_insert_only, f"{desvios_do_insert_only} alterações desviaram do insert-only"),
        (auditoria.ordem.inversoes_de_lsn, f"{auditoria.ordem.inversoes_de_lsn} transações chegaram com LSN invertido"),
        (len(auditoria.desvios_no_catalogo), f"esquema fora do contrato: {', '.join(map(str, auditoria.desvios_no_catalogo))}"),
    )
    encontradas = [mensagem for quantidade, mensagem in candidatas if quantidade]

    if not auditoria.slot.existe:
        encontradas.append("o slot do CDC não existe")
    elif auditoria.slot.situacao_do_wal == SLOT_INVALIDADO:
        encontradas.append("o slot do CDC foi invalidado por excesso de WAL retido")

    if auditoria.frescor_s is None:
        encontradas.append("o consumidor nunca registrou um dreno do slot")
    elif auditoria.frescor_s > auditoria.sla_de_frescor_s:
        encontradas.append(f"frescor de {decimal_br(auditoria.frescor_s, 1)} s acima do SLA de {decimal_br(auditoria.sla_de_frescor_s, 1)} s")

    return encontradas


def formatar(auditoria: Auditoria) -> str:
    secoes = [
        "# Auditoria do CDC",
        "",
        "## Consistência",
        "",
        *_formatar_checksum(auditoria.linhas),
        "",
        *_formatar_saldo(auditoria.saldo),
        "",
        *_formatar_ordem(auditoria.ordem),
        "",
        "## Latência e frescor",
        "",
        "Desde que a telemetria começou. Um commit feito com o consumidor parado conta o tempo parado até ser entregue.",
        "",
        *formatar_latencias(auditoria.latencias),
        "",
        *_formatar_frescor(auditoria),
        "",
        "## Resiliência",
        "",
        *_formatar_resiliencia(auditoria),
        "",
        "## Resultado",
        "",
    ]
    encontradas = violacoes(auditoria)
    secoes += [f"- {violacao}" for violacao in encontradas] if encontradas else ["Nenhuma violação."]

    return "\n".join(secoes)


def _formatar_checksum(linhas: ComparacaoDeLinhas) -> list[str]:
    so_na_origem = linhas.pendentes + linhas.na_fila + linhas.sem_explicacao
    saida = [
        "Checksum da tabela inteira, por linha:",
        "",
        "| Caminho | Iguais | Diferentes |",
        "| --- | ---: | ---: |",
        f"| Linha de base e recarga (sem LSN) | {linhas.iguais_sem_lsn} | {linhas.diferentes_sem_lsn} |",
        f"| CDC (com LSN) | {linhas.iguais_com_lsn} | {linhas.diferentes_com_lsn} |",
        "",
        f"Só na origem: {so_na_origem} ({linhas.pendentes} pendentes no slot, {linhas.na_fila} na fila de erros, "
        f"{linhas.sem_explicacao} sem explicação). Só no Delta: {linhas.so_no_delta}.",
    ]
    if linhas.divergencias_por_dia:
        saida += ["", "| Dia do registro | Situação | Linhas |", "| --- | --- | ---: |"]
        saida += [f"| {dia} | {situacao} | {quantidade} |" for dia, situacao, quantidade in linhas.divergencias_por_dia]

    return saida


def _formatar_saldo(saldo: list[SaldoDaOperacao]) -> list[str]:
    saida = [
        "Contagem por operação, desde que a telemetria começou:",
        "",
        "| Operação | Lidas do WAL | Aplicadas no Delta | Na fila de erros | Já estavam no Delta | Pendentes no slot |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    saida += [
        f"| {s.operacao} | {s.lidas_do_wal} | {s.aplicadas} | {s.na_fila} | {s.ja_estavam_no_delta} | {s.pendentes} |" for s in saldo
    ]

    return saida


def _formatar_ordem(ordem: ResumoDaOrdem) -> list[str]:
    return [
        f"Ordem, em {ordem.transacoes} transações entregues: {ordem.inversoes_de_lsn} com LSN invertido, {ordem.reentregas} reentregas, "
        f"{ordem.inversoes_de_registro} com registrado_em invertido (maior diferença de {decimal_br(ordem.maior_inversao_de_registro_s)} s), "
        f"{ordem.fora_de_ordem_no_evento} com ocorrido_em fora de ordem e {ordem.alem_da_tolerancia} além da tolerância de "
        f"{telemetria_cdc.TOLERANCIA_DE_ATRASO.days} dias do ADR 0004.",
    ]


def formatar_latencias(latencias: dict[str, ResumoDeTempos]) -> list[str]:
    if not latencias:
        return ["Latência: telemetria com menos de 2 transações, sem resumo."]

    saida = ["| Medida | Amostras | Mediana (s) | p95 (s) | Máximo (s) |", "| --- | ---: | ---: | ---: | ---: |"]
    for campo, rotulo in MEDIDAS:
        tempos = latencias[campo]
        saida.append(f"| {rotulo} | {tempos.amostras} | {decimal_br(tempos.mediana_s)} | {decimal_br(tempos.p95_s)} | {decimal_br(tempos.maximo_s)} |")

    return saida


def _formatar_frescor(auditoria: Auditoria) -> list[str]:
    if auditoria.frescor_s is None:
        return ["Frescor: sem registro de dreno do slot."]

    return [
        f"Frescor: tudo o que confirmou até {auditoria.drenado_em.isoformat()} já estava legível, "
        f"{decimal_br(auditoria.frescor_s, 1)} s atrás (SLA de {decimal_br(auditoria.sla_de_frescor_s, 1)} s)."
    ]


def _formatar_resiliencia(auditoria: Auditoria) -> list[str]:
    catalogo = ", ".join(map(str, auditoria.desvios_no_catalogo)) or "nenhum"
    vistos = ", ".join(f"{desvio} ({quantidade}x)" for desvio, quantidade in auditoria.desvios_vistos_no_wal.items()) or "nenhum"
    fila = ", ".join(f"{motivo}: {quantidade}" for motivo, quantidade in auditoria.fila_por_motivo.items()) or "vazia"

    return [
        f"Desvios de esquema agora no catálogo: {catalogo}.",
        f"Desvios de esquema vistos no WAL: {vistos}.",
        f"Fila de erros: {sum(auditoria.fila_por_motivo.values())} linhas ({fila}).",
        f"Slot: {auditoria.slot.situacao_do_wal or 'inexistente'}, {auditoria.slot.atraso_em_bytes} bytes por entregar.",
    ]
