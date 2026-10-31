"""Latência ponta a ponta do CDC e da recarga periódica, e WAL retido pelo slot (ADR 0004, issue #52).

Mede do commit na origem até a vistoria ficar legível na tabela Delta, com o
relógio de um único processo. O consumidor contínuo roda em outro contêiner
(`medir-latencia-cdc`) ou fica parado (`medir-recarga`). A origem é insert-only:
as vistorias da medição ficam no banco, rode em ambiente de desenvolvimento.
"""

import random
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg
import pyarrow as pa
import pyarrow.dataset as ds
from deltalake import DeltaTable
from deltalake.exceptions import CommitFailedError
from psycopg.rows import dict_row

from eduinfra import cdc, eventos
from eduinfra.configuracao import Configuracao
from eduinfra.vistorias_de_teste import contar_itens_por_vistoria, inserir_vistoria

MATRICULA = "MEDICAO-E3-52"
OBSERVACAO = "Medição de latência ponta a ponta (issue #52)"
INTERVALO_DO_CONSUMIDOR_S = 2.0
INTERVALO_DE_CONSULTA_S = 0.05
ESPERA_MAXIMA_S = 300.0
AMOSTRAS_MINIMAS = 2
PERCENTIS = 100
INDICE_DO_P95 = 94
ATRASO_DO_EVENTO_RECENTE = timedelta(minutes=1)
PERIODOS_DE_RECARGA_S = (2, 300, 3600)
VISTORIAS_POR_DIA_ESTIMADAS = 500

MARCA_DAGUA = "select coalesce(max(registrado_em), '-infinity') from vistoria"

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


@dataclass(frozen=True)
class ResumoDeTempos:
    amostras: int
    mediana_s: float
    p95_s: float
    minimo_s: float
    maximo_s: float


@dataclass(frozen=True)
class LatenciaDoCdc:
    cenario: str
    tempos: ResumoDeTempos
    arquivos_na_particao: int


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


def resumir(tempos: list[float]) -> ResumoDeTempos:
    if len(tempos) < AMOSTRAS_MINIMAS:
        raise ValueError(f"p95 exige ao menos {AMOSTRAS_MINIMAS} amostras, recebeu {len(tempos)}")

    return ResumoDeTempos(
        amostras=len(tempos),
        mediana_s=statistics.median(tempos),
        p95_s=statistics.quantiles(tempos, n=PERCENTIS, method="inclusive")[INDICE_DO_P95],
        minimo_s=min(tempos),
        maximo_s=max(tempos),
    )


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
        ("Evento recente, partição pequena", lambda agora: agora - ATRASO_DO_EVENTO_RECENTE),
        ("Evento atrasado, partição da linha de base", lambda _: data_do_censo),
    )

    tabela = DeltaTable(str(eventos.garantir_tabela(configuracao)))
    with psycopg.connect(configuracao.dsn, autocommit=True) as conexao:
        return [
            _medir_cenario(conexao, tabela, nome, ocorrido_em, repeticoes, espera_maxima_s)
            for nome, ocorrido_em in cenarios
        ]


def _medir_cenario(
    conexao: psycopg.Connection,
    tabela: DeltaTable,
    nome: str,
    ocorrido_em: Callable[[datetime], datetime],
    repeticoes: int,
    espera_maxima_s: float,
) -> LatenciaDoCdc:
    tempos = []
    mes = ""
    for _ in range(repeticoes):
        # Inserir logo depois de uma entrega cairia sempre na mesma fase do laço do
        # consumidor; o sorteio espalha os commits pelo intervalo entre passadas.
        time.sleep(random.uniform(0, INTERVALO_DO_CONSUMIDOR_S))
        evento = ocorrido_em(datetime.now(timezone.utc))
        vistoria_id = inserir_vistoria(conexao, MATRICULA, OBSERVACAO, evento)
        commit = time.perf_counter()
        mes = evento.strftime("%Y-%m")
        tempos.append(_esperar_no_delta(tabela, vistoria_id, mes, espera_maxima_s) - commit)

    return LatenciaDoCdc(nome, resumir(tempos), len(tabela.file_uris([(eventos.COLUNA_DE_PARTICAO, "=", mes)])))


def _esperar_no_delta(tabela: DeltaTable, vistoria_id: int, mes: str, espera_maxima_s: float) -> float:
    limite = time.perf_counter() + espera_maxima_s
    versao_lida = None
    while time.perf_counter() < limite:
        instante = time.perf_counter()
        tabela.update_incremental()
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


def _decimal(valor: float, casas: int = 2) -> str:
    return f"{valor:.{casas}f}".replace(".", ",")


def formatar_cdc(medidas: list[LatenciaDoCdc]) -> str:
    linhas = [
        "| Cenário | Amostras | Mediana (s) | p95 (s) | Mínimo (s) | Máximo (s) | Arquivos na partição ao fim |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for medida in medidas:
        t = medida.tempos
        linhas.append(
            f"| {medida.cenario} | {t.amostras} | {_decimal(t.mediana_s)} | {_decimal(t.p95_s)} "
            f"| {_decimal(t.minimo_s)} | {_decimal(t.maximo_s)} | {medida.arquivos_na_particao} |"
        )

    return "\n".join(linhas)


def formatar_recarga(medida: MedidaDaRecarga) -> str:
    execucao, wal = medida.execucao, medida.wal
    linhas = [
        f"Execução de um ciclo com 1 vistoria nova ({execucao.amostras} amostras): mediana {_decimal(execucao.mediana_s)} s, "
        f"p95 {_decimal(execucao.p95_s)} s, mínimo {_decimal(execucao.minimo_s)} s, máximo {_decimal(execucao.maximo_s)} s.",
        "",
        "| Período da recarga (s) | Latência média derivada (s) | Pior caso derivado (s) |",
        "| ---: | ---: | ---: |",
    ]
    for latencia in medida.latencias:
        linhas.append(f"| {latencia.periodo_s} | {_decimal(latencia.media_s)} | {_decimal(latencia.pior_caso_s)} |")
    linhas += [
        "",
        f"WAL retido pelo slot parado: {_decimal(wal.bytes_por_vistoria, 0)} bytes por vistoria. Com a estimativa de "
        f"{VISTORIAS_POR_DIA_ESTIMADAS} vistorias por dia, o limite de {wal.limite_em_bytes} bytes chega em "
        f"{_decimal(wal.dias_ate_o_limite, 1)} dias.",
    ]

    return "\n".join(linhas)
