"""Mede o custo de capturar mudanças da origem: recarga incremental contra slot lógico (ADR 0004).

Os dois caminhos respondem à mesma pergunta, "o que entrou na origem desde a
última vez?", e são medidos sem o destino: o tempo é só o da captura. Cada
volume insere vistorias novas (a origem é insert-only, rode em banco de
desenvolvimento) e confere que os dois caminhos devolveram a mesma contagem.
"""

import statistics
import time
from dataclasses import dataclass

import psycopg

from eduinfra import cdc
from eduinfra.configuracao import Configuracao
from eduinfra.vistorias_de_teste import inserir_vistorias

SLOT_DE_MEDICAO = "eduinfra_medicao"
MATRICULA = "MEDICAO-E2-4"
OBSERVACAO = "Medição da E2 (ADR 0004)"
VOLUMES = (1, 100, 5000)
REPETICOES_OCIOSAS = 20

RECARGA_INCREMENTAL = """
select v.id, i.item_codigo
from vistoria v
join vistoria_item i on i.vistoria_id = v.id
where v.registrado_em > %s
"""


@dataclass(frozen=True)
class MedidaDeCaptura:
    vistorias: int
    itens: int
    recarga_ms: float
    cdc_ms: float
    contagens_iguais: bool


@dataclass(frozen=True)
class MedidaOciosa:
    recarga_ms: float
    cdc_ms: float


def _mediana_ms(acao, repeticoes: int) -> float:
    tempos = []
    for _ in range(repeticoes):
        inicio = time.perf_counter()
        acao()
        tempos.append((time.perf_counter() - inicio) * 1000)

    return statistics.median(tempos)


def _marca_dagua(conexao: psycopg.Connection):
    return conexao.execute("select coalesce(max(registrado_em), '-infinity') from vistoria").fetchone()[0]


def _medir_volume(conexao: psycopg.Connection, volume: int) -> MedidaDeCaptura:
    marca = _marca_dagua(conexao)
    inserir_vistorias(conexao, volume, MATRICULA, OBSERVACAO)

    inicio = time.perf_counter()
    por_recarga = conexao.execute(RECARGA_INCREMENTAL, (marca,)).fetchall()
    recarga_ms = (time.perf_counter() - inicio) * 1000

    inicio = time.perf_counter()
    transacoes = cdc.ler_transacoes(conexao, SLOT_DE_MEDICAO)
    cdc_ms = (time.perf_counter() - inicio) * 1000

    por_cdc = [i for t in transacoes for i in t.insercoes if i.tabela == "vistoria_item"]
    if transacoes:
        cdc.confirmar(conexao, transacoes[-1].lsn_fim, SLOT_DE_MEDICAO)

    return MedidaDeCaptura(volume, len(por_recarga), recarga_ms, cdc_ms, len(por_recarga) == len(por_cdc))


def _medir_ociosidade(conexao: psycopg.Connection) -> MedidaOciosa:
    marca = _marca_dagua(conexao)
    return MedidaOciosa(
        _mediana_ms(lambda: conexao.execute(RECARGA_INCREMENTAL, (marca,)).fetchall(), REPETICOES_OCIOSAS),
        _mediana_ms(lambda: cdc.ler_transacoes(conexao, SLOT_DE_MEDICAO), REPETICOES_OCIOSAS),
    )


def medir(configuracao: Configuracao) -> tuple[list[MedidaDeCaptura], MedidaOciosa]:
    with psycopg.connect(configuracao.dsn, autocommit=True) as conexao:
        cdc.remover_slot(conexao, SLOT_DE_MEDICAO)
        cdc.garantir_slot(conexao, SLOT_DE_MEDICAO)
        try:
            capturas = [_medir_volume(conexao, volume) for volume in VOLUMES]
            return capturas, _medir_ociosidade(conexao)
        finally:
            cdc.remover_slot(conexao, SLOT_DE_MEDICAO)


def formatar_markdown(capturas: list[MedidaDeCaptura], ociosa: MedidaOciosa) -> str:
    linhas = [
        "| Vistorias novas | Itens | Recarga incremental (ms) | Slot lógico (ms) | Mesma contagem |",
        "| ---: | ---: | ---: | ---: | :---: |",
    ]
    for c in capturas:
        linhas.append(
            f"| {c.vistorias} | {c.itens} | {c.recarga_ms:.1f} | {c.cdc_ms:.1f} | {'sim' if c.contagens_iguais else 'não'} |"
        )
    linhas += [
        "",
        f"Consulta sem nada novo (mediana de {REPETICOES_OCIOSAS}): recarga incremental {ociosa.recarga_ms:.1f} ms, "
        f"slot lógico {ociosa.cdc_ms:.1f} ms.",
    ]

    return "\n".join(linhas)
