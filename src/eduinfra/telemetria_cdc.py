"""Telemetria do consumidor do CDC: uma linha por transação entregue e o instante do último dreno (ADR 0007).

Os instantes vêm do relógio do PostgreSQL, o mesmo que carimba o commit no WAL, para o
atraso não depender do relógio do contêiner do consumidor. Falhar ao registrar não
interrompe a entrega: a ausência de telemetria aparece na auditoria, e o dado é o que importa.
"""

import json
import logging
import os
import statistics
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import DeltaError

from eduinfra import contrato_cdc
from eduinfra.configuracao import Configuracao
from eduinfra.contrato_cdc import TABELA_DE_ITENS, TABELA_DE_VISTORIAS
from eduinfra.pgoutput import OPERACAO_DELETE, OPERACAO_INSERT, OPERACAO_TRUNCATE, OPERACAO_UPDATE, Transacao

log = logging.getLogger(__name__)

NOME_DA_TABELA = "telemetria_cdc"
ARQUIVO_DE_FRESCOR = "frescor_cdc.json"
AMOSTRAS_MINIMAS = 2
PERCENTIS = 100
INDICE_DO_P95 = 94
# ADR 0004: a tolerância de atraso do processamento por janelas é de sete dias.
TOLERANCIA_DE_ATRASO = timedelta(days=7)
SEPARADOR_DE_DESVIOS = "; "

MEDIDAS = (
    ("ponta_a_ponta_s", "Ponta a ponta (commit até gravar no Delta)"),
    ("atraso_de_captura_s", "Atraso de captura (commit até ler do WAL)"),
    ("espera_s", "- espera pela próxima passada"),
    ("leitura_s", "- leitura e decodificação do WAL"),
    ("atraso_de_carga_s", "Atraso de carga (fim da leitura até gravar no Delta)"),
    ("conversao_s", "- conversão e cabeçalhos"),
    ("gravacao_s", "- merge no Delta e fila de erros"),
)

ESQUEMA = pa.schema(
    [
        pa.field("passada_em", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("posicao_na_passada", pa.int32(), nullable=False),
        pa.field("lsn_fim", pa.int64(), nullable=False),
        pa.field("confirmada_em", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("espera_s", pa.float64(), nullable=False),
        pa.field("leitura_s", pa.float64(), nullable=False),
        pa.field("atraso_de_captura_s", pa.float64(), nullable=False),
        pa.field("conversao_s", pa.float64(), nullable=False),
        pa.field("gravacao_s", pa.float64(), nullable=False),
        pa.field("atraso_de_carga_s", pa.float64(), nullable=False),
        pa.field("ponta_a_ponta_s", pa.float64(), nullable=False),
        pa.field("vistorias", pa.int32(), nullable=False),
        pa.field("itens", pa.int32(), nullable=False),
        pa.field("atualizacoes", pa.int32(), nullable=False),
        pa.field("exclusoes", pa.int32(), nullable=False),
        pa.field("truncamentos", pa.int32(), nullable=False),
        pa.field("itens_rejeitados", pa.int32(), nullable=False),
        pa.field("registrado_em", pa.timestamp("us", tz="UTC")),
        pa.field("ocorrido_em", pa.timestamp("us", tz="UTC")),
        pa.field("desvios_de_esquema", pa.string()),
    ]
)


@dataclass(frozen=True)
class RelogioDaPassada:
    inicio_da_leitura: datetime
    fim_da_leitura: datetime
    fim_da_conversao: datetime
    fim_da_gravacao: datetime


@dataclass(frozen=True)
class ResumoDeTempos:
    amostras: int
    mediana_s: float
    p95_s: float
    minimo_s: float
    maximo_s: float


@dataclass(frozen=True)
class ResumoDaOrdem:
    transacoes: int
    reentregas: int
    inversoes_de_lsn: int
    inversoes_de_registro: int
    maior_inversao_de_registro_s: float
    fora_de_ordem_no_evento: int
    alem_da_tolerancia: int


def agora(conexao: psycopg.Connection) -> datetime:
    return conexao.execute("select clock_timestamp()").fetchone()[0]


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


def linhas_da_passada(transacoes: list[Transacao], relogio: RelogioDaPassada, eventos: pa.Table, rejeicoes: list[dict]) -> list[dict]:
    instantes = _instantes_por_lsn(eventos)
    rejeitados = Counter(linha["lsn_origem"] for linha in rejeicoes if linha["operacao"] == OPERACAO_INSERT)

    return [
        _linha_da_transacao(posicao, transacao, relogio, instantes.get(transacao.lsn_fim, (None, None)), rejeitados[transacao.lsn_fim])
        for posicao, transacao in enumerate(transacoes)
    ]


def _instantes_por_lsn(eventos: pa.Table) -> dict[int, tuple[datetime, datetime]]:
    instantes: dict[int, tuple[datetime, datetime]] = {}
    for linha in eventos.select(["lsn_origem", "registrado_em", "ocorrido_em"]).to_pylist():
        registrado, ocorrido = instantes.get(linha["lsn_origem"], (linha["registrado_em"], linha["ocorrido_em"]))
        instantes[linha["lsn_origem"]] = (max(registrado, linha["registrado_em"]), min(ocorrido, linha["ocorrido_em"]))

    return instantes


def _linha_da_transacao(
    posicao: int,
    transacao: Transacao,
    relogio: RelogioDaPassada,
    instantes: tuple[datetime | None, datetime | None],
    itens_rejeitados: int,
) -> dict:
    confirmada_em = transacao.confirmada_em
    operacoes = Counter(alteracao.operacao for alteracao in transacao.alteracoes)
    tabelas = Counter(insercao.tabela for insercao in transacao.insercoes)
    desvios = [str(desvio) for relacao in transacao.relacoes for desvio in contrato_cdc.comparar(relacao.nome, relacao.colunas)]

    return {
        "passada_em": relogio.inicio_da_leitura,
        "posicao_na_passada": posicao,
        "lsn_fim": transacao.lsn_fim,
        "confirmada_em": confirmada_em,
        # O commit pode cair entre a leitura do relógio e a do slot: aí não houve espera.
        "espera_s": max(0.0, _segundos_entre(confirmada_em, relogio.inicio_da_leitura)),
        "leitura_s": _segundos_entre(relogio.inicio_da_leitura, relogio.fim_da_leitura),
        "atraso_de_captura_s": _segundos_entre(confirmada_em, relogio.fim_da_leitura),
        "conversao_s": _segundos_entre(relogio.fim_da_leitura, relogio.fim_da_conversao),
        "gravacao_s": _segundos_entre(relogio.fim_da_conversao, relogio.fim_da_gravacao),
        "atraso_de_carga_s": _segundos_entre(relogio.fim_da_leitura, relogio.fim_da_gravacao),
        "ponta_a_ponta_s": _segundos_entre(confirmada_em, relogio.fim_da_gravacao),
        "vistorias": tabelas[TABELA_DE_VISTORIAS],
        "itens": tabelas[TABELA_DE_ITENS],
        "atualizacoes": operacoes[OPERACAO_UPDATE],
        "exclusoes": operacoes[OPERACAO_DELETE],
        "truncamentos": operacoes[OPERACAO_TRUNCATE],
        "itens_rejeitados": itens_rejeitados,
        "registrado_em": instantes[0],
        "ocorrido_em": instantes[1],
        "desvios_de_esquema": SEPARADOR_DE_DESVIOS.join(desvios) or None,
    }


def _segundos_entre(inicio: datetime, fim: datetime) -> float:
    return (fim - inicio).total_seconds()


def registrar_passada(configuracao: Configuracao, linhas: list[dict]) -> None:
    caminho = _caminho_da_tabela(configuracao)
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        write_deltalake(str(caminho), pa.Table.from_pylist(linhas, schema=ESQUEMA), mode="append")
    except (OSError, DeltaError) as erro:
        log.error("telemetria da passada não registrada: %s", erro)


def ler_telemetria(configuracao: Configuracao, lsn_acima: int = -1, lsn_ate: int | None = None) -> list[dict]:
    caminho = str(_caminho_da_tabela(configuracao))
    if not DeltaTable.is_deltatable(caminho):
        return []

    linhas = [
        linha
        for linha in DeltaTable(caminho).to_pyarrow_table().to_pylist()
        if linha["lsn_fim"] > lsn_acima and (lsn_ate is None or linha["lsn_fim"] <= lsn_ate)
    ]

    return sorted(linhas, key=lambda linha: (linha["passada_em"], linha["posicao_na_passada"]))


def registrar_frescor(configuracao: Configuracao, drenado_em: datetime) -> None:
    caminho = _caminho_do_frescor(configuracao)
    temporario = caminho.with_suffix(".tmp")
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario.write_text(json.dumps({"drenado_em": drenado_em.isoformat()}), encoding="utf-8")
        os.replace(temporario, caminho)
    except OSError as erro:
        log.error("frescor da passada não registrado: %s", erro)


def ler_frescor(configuracao: Configuracao) -> datetime | None:
    caminho = _caminho_do_frescor(configuracao)
    if not caminho.exists():
        return None

    return datetime.fromisoformat(json.loads(caminho.read_text(encoding="utf-8"))["drenado_em"])


def _caminho_da_tabela(configuracao: Configuracao) -> Path:
    return configuracao.diretorio_dados / "warehouse" / NOME_DA_TABELA


def _caminho_do_frescor(configuracao: Configuracao) -> Path:
    return configuracao.diretorio_dados / "warehouse" / ARQUIVO_DE_FRESCOR


def resumir_ordem(linhas: Iterable[dict]) -> ResumoDaOrdem:
    vistos: set[int] = set()
    reentregas = inversoes_de_lsn = inversoes_de_registro = fora_de_ordem = alem_da_tolerancia = 0
    maior_inversao = 0.0
    maior_registro = maior_evento = None

    for linha in linhas:
        lsn = linha["lsn_fim"]
        if lsn in vistos:
            reentregas += 1
            continue
        if vistos and lsn < max(vistos):
            inversoes_de_lsn += 1
        vistos.add(lsn)

        registrado, ocorrido = linha["registrado_em"], linha["ocorrido_em"]
        if registrado is None:
            continue
        if maior_registro is not None and registrado < maior_registro:
            inversoes_de_registro += 1
            maior_inversao = max(maior_inversao, _segundos_entre(registrado, maior_registro))
        if maior_evento is not None and ocorrido < maior_evento:
            fora_de_ordem += 1
        if registrado - ocorrido > TOLERANCIA_DE_ATRASO:
            alem_da_tolerancia += 1
        maior_registro = registrado if maior_registro is None else max(maior_registro, registrado)
        maior_evento = ocorrido if maior_evento is None else max(maior_evento, ocorrido)

    return ResumoDaOrdem(len(vistos), reentregas, inversoes_de_lsn, inversoes_de_registro, maior_inversao, fora_de_ordem, alem_da_tolerancia)


def perdas_por_marca_dagua(pares: list[tuple[datetime, datetime]], periodo_s: float, margem_s: float) -> int:
    """Conta quantas transações (registrado_em, confirmada_em) uma recarga por marca d'água não veria."""
    if not pares:
        return 0

    inicio = min(confirmada for _, confirmada in pares)
    ciclos: dict[int, list[datetime]] = {}
    for registrado, confirmada in pares:
        ciclos.setdefault(int(_segundos_entre(inicio, confirmada) // periodo_s), []).append(registrado)

    margem = timedelta(seconds=margem_s)
    marca = None
    perdidas = 0
    for ciclo in sorted(ciclos):
        visiveis = [registrado for registrado in ciclos[ciclo] if marca is None or registrado > marca - margem]
        perdidas += len(ciclos[ciclo]) - len(visiveis)
        if visiveis:
            marca = max(visiveis) if marca is None else max(marca, *visiveis)

    return perdidas


def decimal_br(valor: float, casas: int = 2) -> str:
    return f"{valor:.{casas}f}".replace(".", ",")
