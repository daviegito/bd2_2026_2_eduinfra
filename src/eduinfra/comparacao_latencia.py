"""Medições ativas do CDC e da recarga periódica (ADR 0004, ADR 0007, issue #52).

A latência é vista de dois lados: por fora, do commit até a vistoria ficar legível no
Delta, com o relógio de um único processo; por dentro, pela telemetria do consumidor.
O consumidor contínuo roda em outro contêiner (`medir-cdc`) ou fica parado
(`medir-recarga`). A origem é insert-only: as vistorias da medição ficam no banco,
rode em ambiente de desenvolvimento.
"""

import random
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg
import pyarrow as pa
import pyarrow.dataset as ds
from deltalake import DeltaTable
from deltalake.exceptions import CommitFailedError
from psycopg.rows import dict_row

from eduinfra import auditoria_cdc, cdc, eventos, telemetria_cdc
from eduinfra.configuracao import Configuracao
from eduinfra.pgoutput import interpretar_lsn
from eduinfra.telemetria_cdc import ResumoDaOrdem, ResumoDeTempos, decimal_br, resumir
from eduinfra.vistorias_de_teste import contar_itens_por_vistoria, garantir_tecnico, inserir_vistoria, inserir_vistoria_retida

MATRICULA = "MEDICAO-E3-52"
OBSERVACAO = "Medição do CDC (issue #52)"
INTERVALO_DO_CONSUMIDOR_S = 2.0
INTERVALO_DE_CONSULTA_S = 0.05
ESPERA_MAXIMA_S = 300.0
ESPERA_PELA_TELEMETRIA_S = 60.0
ATRASO_DO_EVENTO_RECENTE = timedelta(minutes=1)
PERIODOS_DE_RECARGA_S = (2, 300, 3600)
VISTORIAS_POR_DIA_ESTIMADAS = 500
ESCRITORES_CONCORRENTES = 4
RETENCAO_MAXIMA_S = 1.0
PERIODO_DA_MARCA_DAGUA_S = 2.0
# ADR 0004: o processamento por janelas relê dez minutos antes da marca d'água.
MARGEM_DO_ADR_0004_S = 600.0

MARCA_DAGUA = "select coalesce(max(registrado_em), '-infinity') from vistoria"
LSN_ATUAL = "select pg_current_wal_insert_lsn()::text"

RECARGA_INCREMENTAL = """
select v.id as vistoria_id, i.item_codigo, v.co_entidade, m.sg_uf, v.origem, v.ocorrido_em, v.registrado_em,
       i.situacao::text as situacao, i.quantidade,
       to_char(v.ocorrido_em at time zone 'UTC', 'YYYY-MM') as mes_ocorrencia,
       null::bigint as lsn_origem
from vistoria v
join vistoria_item i on i.vistoria_id = v.id
join escola e on e.co_entidade = v.co_entidade
join municipio m on m.co_municipio = e.co_municipio
where v.registrado_em > %s
"""

POSICAO_DO_SLOT = """
select confirmed_flush_lsn::text, pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint
from pg_replication_slots
where slot_name = %s
"""

LIMITE_DE_WAL = "select pg_size_bytes(current_setting('max_slot_wal_keep_size'))"


class ConsumidorParado(RuntimeError):
    """A vistoria não chegou ao Delta dentro da espera máxima."""


class ConsumidorEmExecucao(RuntimeError):
    """O slot avançou durante uma medição que exige o consumidor parado."""


class ConsumidorSemTelemetria(RuntimeError):
    """A vistoria chegou ao Delta, mas o consumidor não registrou a passada."""


@dataclass(frozen=True)
class LatenciaDoCdc:
    cenario: str
    tempos: ResumoDeTempos
    partes: dict[str, ResumoDeTempos]
    arquivos_na_particao: int


@dataclass(frozen=True)
class MedidaDaOrdem:
    ordem: ResumoDaOrdem
    perdas_sem_margem: int
    perdas_com_margem: int
    latencias: dict[str, ResumoDeTempos]


@dataclass(frozen=True)
class LatenciaDeRecarga:
    periodo_s: int
    media_s: float
    pior_caso_s: float


@dataclass(frozen=True)
class CustoDeWal:
    bytes_por_vistoria: float
    limite_em_bytes: int
    dias_ate_o_limite: float


@dataclass(frozen=True)
class MedidaDaRecarga:
    execucao: ResumoDeTempos
    latencias: list[LatenciaDeRecarga]
    wal: CustoDeWal


def derivar_latencias(execucao: ResumoDeTempos) -> list[LatenciaDeRecarga]:
    # Um commit cai em ponto uniforme do período: espera metade em média e o período inteiro no pior caso.
    return [
        LatenciaDeRecarga(periodo, periodo / 2 + execucao.mediana_s, periodo + execucao.p95_s)
        for periodo in PERIODOS_DE_RECARGA_S
    ]


def projetar_wal(bytes_acumulados: int, vistorias: int, limite_em_bytes: int) -> CustoDeWal:
    if bytes_acumulados <= 0:
        raise ValueError(f"o WAL não cresceu durante a medição: {bytes_acumulados} bytes")

    por_vistoria = bytes_acumulados / vistorias
    return CustoDeWal(por_vistoria, limite_em_bytes, limite_em_bytes / (por_vistoria * VISTORIAS_POR_DIA_ESTIMADAS))


def medir_cdc(configuracao: Configuracao, repeticoes: int, espera_maxima_s: float = ESPERA_MAXIMA_S) -> list[LatenciaDoCdc]:
    data_do_censo = datetime.combine(configuracao.data_referencia, datetime.min.time(), timezone.utc)
    cenarios = (
        ("Evento recente, partição do mês corrente", lambda agora: agora - ATRASO_DO_EVENTO_RECENTE),
        ("Evento atrasado, partição da linha de base", lambda _: data_do_censo),
    )

    tabela = DeltaTable(str(eventos.garantir_tabela(configuracao)))
    with psycopg.connect(configuracao.dsn, autocommit=True) as conexao:
        return [
            _medir_cenario(configuracao, conexao, tabela, nome, ocorrido_em, repeticoes, espera_maxima_s)
            for nome, ocorrido_em in cenarios
        ]


def _medir_cenario(
    configuracao: Configuracao,
    conexao: psycopg.Connection,
    tabela: DeltaTable,
    nome: str,
    ocorrido_em: Callable[[datetime], datetime],
    repeticoes: int,
    espera_maxima_s: float,
) -> LatenciaDoCdc:
    tempos = []
    mes = ""
    lsn_inicial = _lsn_atual(conexao)
    for _ in range(repeticoes):
        # Inserir logo depois de uma entrega cairia sempre na mesma fase do laço do
        # consumidor; o sorteio espalha os commits pelo intervalo entre passadas.
        time.sleep(random.uniform(0, INTERVALO_DO_CONSUMIDOR_S))
        evento = ocorrido_em(datetime.now(timezone.utc))
        vistoria_id = inserir_vistoria(conexao, MATRICULA, OBSERVACAO, evento)
        commit = time.perf_counter()
        mes = evento.strftime("%Y-%m")
        tempos.append(_esperar_no_delta(tabela, vistoria_id, mes, espera_maxima_s) - commit)

    linhas = _esperar_telemetria(configuracao, lsn_inicial, _lsn_atual(conexao), repeticoes)
    arquivos = len(tabela.file_uris([(eventos.COLUNA_DE_PARTICAO, "=", mes)]))

    return LatenciaDoCdc(nome, resumir(tempos), auditoria_cdc.resumir_latencias(linhas), arquivos)


def _lsn_atual(conexao: psycopg.Connection) -> int:
    return interpretar_lsn(conexao.execute(LSN_ATUAL).fetchone()[0])


def _esperar_no_delta(tabela: DeltaTable, vistoria_id: int, mes: str, espera_maxima_s: float) -> float:
    limite = time.perf_counter() + espera_maxima_s
    versao_lida = None
    while time.perf_counter() < limite:
        tabela.update_incremental()
        # Marcar depois de atualizar: a versão pode ter nascido durante a atualização, e antes dela a latência sairia menor que a real.
        instante = time.perf_counter()
        if tabela.version() != versao_lida:
            versao_lida = tabela.version()
            if _tem_vistoria(tabela, vistoria_id, mes):
                return instante
        time.sleep(INTERVALO_DE_CONSULTA_S)

    raise ConsumidorParado(
        f"vistoria {vistoria_id} fora do Delta depois de {espera_maxima_s:.0f} s; suba o serviço cdc antes de medir"
    )


def _tem_vistoria(tabela: DeltaTable, vistoria_id: int, mes: str) -> bool:
    particao = tabela.to_pyarrow_dataset(partitions=[(eventos.COLUNA_DE_PARTICAO, "=", mes)])
    return particao.to_table(columns=["vistoria_id"], filter=ds.field("vistoria_id") == vistoria_id).num_rows > 0


def _esperar_telemetria(configuracao: Configuracao, lsn_inicial: int, lsn_final: int, transacoes: int) -> list[dict]:
    # O Delta fica legível antes de o consumidor escrever a telemetria da mesma passada.
    limite = time.perf_counter() + ESPERA_PELA_TELEMETRIA_S
    while time.perf_counter() < limite:
        linhas = telemetria_cdc.ler_telemetria(configuracao, lsn_inicial, lsn_final)
        if sum(1 for linha in linhas if linha["vistorias"]) >= transacoes:
            return linhas
        time.sleep(INTERVALO_DE_CONSULTA_S)

    raise ConsumidorSemTelemetria("sem telemetria das vistorias medidas; reconstrua a imagem do serviço cdc antes de medir")


def medir_ordem(configuracao: Configuracao, repeticoes: int) -> MedidaDaOrdem:
    with psycopg.connect(configuracao.dsn, autocommit=True) as conexao:
        garantir_tecnico(conexao, MATRICULA)
        lsn_inicial = _lsn_atual(conexao)
        with ThreadPoolExecutor(ESCRITORES_CONCORRENTES) as executor:
            list(executor.map(lambda _: _escrever_com_retencao(configuracao.dsn, repeticoes), range(ESCRITORES_CONCORRENTES)))
        lsn_final = _lsn_atual(conexao)

    linhas = _esperar_telemetria(configuracao, lsn_inicial, lsn_final, ESCRITORES_CONCORRENTES * repeticoes)
    pares = [(linha["registrado_em"], linha["confirmada_em"]) for linha in linhas if linha["registrado_em"]]

    return MedidaDaOrdem(
        ordem=telemetria_cdc.resumir_ordem(linhas),
        perdas_sem_margem=telemetria_cdc.perdas_por_marca_dagua(pares, PERIODO_DA_MARCA_DAGUA_S, 0),
        perdas_com_margem=telemetria_cdc.perdas_por_marca_dagua(pares, PERIODO_DA_MARCA_DAGUA_S, MARGEM_DO_ADR_0004_S),
        latencias=auditoria_cdc.resumir_latencias(linhas),
    )


def _escrever_com_retencao(dsn: str, transacoes: int) -> None:
    with psycopg.connect(dsn, autocommit=True) as conexao:
        for _ in range(transacoes):
            evento = datetime.now(timezone.utc) - ATRASO_DO_EVENTO_RECENTE
            inserir_vistoria_retida(conexao, MATRICULA, OBSERVACAO, evento, random.uniform(0, RETENCAO_MAXIMA_S))


def medir_recarga(configuracao: Configuracao, repeticoes: int, slot: str = cdc.SLOT_PADRAO) -> MedidaDaRecarga:
    with psycopg.connect(configuracao.dsn, autocommit=True) as conexao:
        cdc.garantir_slot(conexao, slot)
        lsn_inicial, atraso_inicial = conexao.execute(POSICAO_DO_SLOT, (slot,)).fetchone()
        tempos = [_medir_ciclo_de_recarga(configuracao, conexao) for _ in range(repeticoes)]

        # O consumidor em execução só mexe no slot na passada seguinte.
        time.sleep(2 * INTERVALO_DO_CONSUMIDOR_S)
        lsn_final, atraso_final = conexao.execute(POSICAO_DO_SLOT, (slot,)).fetchone()
        if lsn_final != lsn_inicial:
            raise ConsumidorEmExecucao("o slot avançou durante a medição; rode docker compose stop cdc antes de medir")

        limite = conexao.execute(LIMITE_DE_WAL).fetchone()[0]

    execucao = resumir(tempos)
    return MedidaDaRecarga(execucao, derivar_latencias(execucao), projetar_wal(atraso_final - atraso_inicial, repeticoes, limite))


def _medir_ciclo_de_recarga(configuracao: Configuracao, conexao: psycopg.Connection) -> float:
    marca = conexao.execute(MARCA_DAGUA).fetchone()[0]
    inserir_vistoria(conexao, MATRICULA, OBSERVACAO, datetime.now(timezone.utc) - ATRASO_DO_EVENTO_RECENTE)

    inicio = time.perf_counter()
    with conexao.cursor(row_factory=dict_row) as cursor:
        linhas = cursor.execute(RECARGA_INCREMENTAL, (marca,)).fetchall()
    try:
        gravacao = eventos.gravar_sem_duplicar(configuracao, pa.Table.from_pylist(linhas, schema=eventos.ESQUEMA))
    except CommitFailedError as conflito:
        raise ConsumidorEmExecucao("outro processo gravou no Delta durante a medição; rode docker compose stop cdc") from conflito
    duracao = time.perf_counter() - inicio

    esperado = contar_itens_por_vistoria(conexao)
    if gravacao.inseridas != esperado:
        raise ConsumidorEmExecucao(f"a recarga inseriu {gravacao.inseridas} itens, esperado {esperado}")

    return duracao


def formatar_cdc(medidas: list[LatenciaDoCdc]) -> str:
    linhas = ["# Medição ativa do CDC", ""]
    for medida in medidas:
        t = medida.tempos
        linhas += [
            f"## {medida.cenario}",
            "",
            f"Por fora, do commit até ficar legível no Delta ({t.amostras} amostras): mediana {decimal_br(t.mediana_s)} s, "
            f"p95 {decimal_br(t.p95_s)} s, mínimo {decimal_br(t.minimo_s)} s, máximo {decimal_br(t.maximo_s)} s. "
            f"Arquivos na partição ao fim: {medida.arquivos_na_particao}.",
            "",
            "Por dentro, pela telemetria do consumidor:",
            "",
            *auditoria_cdc.formatar_latencias(medida.partes),
            "",
        ]

    return "\n".join(linhas)


def formatar_ordem(medida: MedidaDaOrdem) -> str:
    ordem = medida.ordem
    percentual = 100 * ordem.inversoes_de_registro / ordem.transacoes if ordem.transacoes else 0.0

    return "\n".join(
        [
            "## Ordem sob escrita concorrente",
            "",
            f"Carga sintética: {ESCRITORES_CONCORRENTES} escritores segurando cada transação por até {decimal_br(RETENCAO_MAXIMA_S, 1)} s, "
            f"{ordem.transacoes} transações. A taxa depende dessa carga e não prevê a de produção.",
            "",
            f"LSN invertido na entrega: {ordem.inversoes_de_lsn}. Reentregas: {ordem.reentregas}.",
            f"registrado_em invertido em relação à ordem de commit: {ordem.inversoes_de_registro} ({decimal_br(percentual, 1)}%), "
            f"maior diferença de {decimal_br(ordem.maior_inversao_de_registro_s)} s.",
            f"Recarga por marca d'água a cada {decimal_br(PERIODO_DA_MARCA_DAGUA_S, 0)} s, simulada sobre estes commits: "
            f"perderia {medida.perdas_sem_margem} transações sem margem e {medida.perdas_com_margem} com a margem de "
            f"{decimal_br(MARGEM_DO_ADR_0004_S / 60, 0)} minutos do ADR 0004.",
            "",
            *auditoria_cdc.formatar_latencias(medida.latencias),
            "",
        ]
    )


def formatar_recarga(medida: MedidaDaRecarga) -> str:
    execucao, wal = medida.execucao, medida.wal
    linhas = [
        f"Execução de um ciclo com 1 vistoria nova ({execucao.amostras} amostras): mediana {decimal_br(execucao.mediana_s)} s, "
        f"p95 {decimal_br(execucao.p95_s)} s, mínimo {decimal_br(execucao.minimo_s)} s, máximo {decimal_br(execucao.maximo_s)} s.",
        "",
        "| Período da recarga (s) | Latência média derivada (s) | Pior caso derivado (s) |",
        "| ---: | ---: | ---: |",
    ]
    for latencia in medida.latencias:
        linhas.append(f"| {latencia.periodo_s} | {decimal_br(latencia.media_s)} | {decimal_br(latencia.pior_caso_s)} |")
    linhas += [
        "",
        f"WAL retido pelo slot parado: {decimal_br(wal.bytes_por_vistoria, 0)} bytes por vistoria. Com a estimativa de "
        f"{VISTORIAS_POR_DIA_ESTIMADAS} vistorias por dia, o limite de {wal.limite_em_bytes} bytes chega em "
        f"{decimal_br(wal.dias_ate_o_limite, 1)} dias.",
    ]

    return "\n".join(linhas)
