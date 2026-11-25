"""Os mesmos arquivos lidos como Parquet e como Delta, conforme o número de arquivos cresce.

Separa o custo de ler o log do Delta do efeito do gravador, e mostra a partir de quantos
arquivos as estatísticas do log compensam esse custo. Issue #4; resultados no ADR 0003.
Rode com a Silver já gerada; este comando não precisa do PyIceberg:

    uv run python -m eduinfra.comparacao_leitor_delta dados/silver --repeticoes 15
"""

import argparse
import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pyarrow as pa
from deltalake import DeltaTable

from eduinfra.lake.camada_analitica import PARTICOES, abrir_no_duckdb, gravar, pastas_da_silver, tabela_larga
from eduinfra.experimentos.formatos_de_tabela import dividir_em_lotes

LOTES_POR_CENARIO = (1, 20, 200)
ARQUIVOS_ORDENADOS = 2_000
PADRAO_PARTICIONADO = "ano_censo=*/sg_uf=*/*.parquet"
# Sem `**`: a pasta _delta_log também guarda Parquet, o checkpoint, que não é dado da tabela.
PADRAO_SEM_PARTICAO = "*.parquet"

CONSULTAS = {
    "uma UF": "select count(*) from {tabela} where sg_uf = 'DF' and item_codigo = 'lab_ciencias' and situacao = 'inexistente'",
    "nacional": "select sg_uf, item_codigo, count(*) filter (where situacao = 'inexistente') from {tabela} group by all order by all",
    "uma escola": "select count(*) from {tabela} where co_entidade = {escola}",
}


@dataclass(frozen=True)
class Medida:
    cenario: str
    arquivos: int
    consulta: str
    read_parquet_ms: float
    delta_scan_ms: float
    attach_fixado_ms: float
    respostas_iguais: bool


def comparar(
    diretorio_silver: Path,
    repeticoes: int = 5,
    lotes_por_cenario: tuple[int, ...] = LOTES_POR_CENARIO,
    arquivos_ordenados: int = ARQUIVOS_ORDENADOS,
) -> list[Medida]:
    dados = pa.concat_tables(tabela_larga(pasta) for pasta in pastas_da_silver(diretorio_silver))
    escola = dados.column("co_entidade")[dados.num_rows // 2].as_py()

    medidas = []
    with tempfile.TemporaryDirectory(dir=diretorio_silver.parent, prefix="leitor_delta_") as pasta:
        for lotes in lotes_por_cenario:
            raiz = Path(pasta) / f"lotes_{lotes}"
            for pedaco in dividir_em_lotes(dados, lotes):
                gravar(raiz, pedaco, PARTICOES, modo="append")
            cenario = f"Por UF, {lotes} {'gravação' if lotes == 1 else 'gravações'}"
            medidas += _medir(cenario, raiz, PADRAO_PARTICIONADO, escola, repeticoes)

        raiz = Path(pasta) / "ordenada"
        _gravar_ordenada_por_escola(raiz, dados, arquivos_ordenados)
        medidas += _medir("Sem partição, ordenada por escola", raiz, PADRAO_SEM_PARTICAO, escola, repeticoes)

    return medidas


def _gravar_ordenada_por_escola(raiz: Path, dados: pa.Table, arquivos: int) -> None:
    # O caso em que o log deveria ganhar: cada arquivo cobre uma faixa estreita de escolas.
    ordenada = dados.sort_by("co_entidade")
    linhas_por_arquivo = ordenada.num_rows // arquivos + 1
    for inicio in range(0, ordenada.num_rows, linhas_por_arquivo):
        gravar(raiz, ordenada.slice(inicio, linhas_por_arquivo), (), modo="append")
    DeltaTable(str(raiz)).create_checkpoint()


def _medir(cenario: str, raiz: Path, padrao: str, escola: int, repeticoes: int) -> list[Medida]:
    leitores = _abrir_leitores(raiz, padrao)
    try:
        consultas = {
            nome: {leitor: sql.format(tabela=tabela, escola=escola) for leitor, (_, tabela) in leitores.items()}
            for nome, sql in CONSULTAS.items()
        }
        respostas = {
            nome: {leitor: leitores[leitor][0].execute(sql).fetchall() for leitor, sql in por_leitor.items()}
            for nome, por_leitor in consultas.items()
        }
        tempos = {nome: {leitor: [] for leitor in leitores} for nome in consultas}
        for _ in range(repeticoes):
            for nome, por_leitor in consultas.items():
                for leitor, sql in por_leitor.items():
                    inicio = time.perf_counter()
                    leitores[leitor][0].execute(sql).fetchall()
                    tempos[nome][leitor].append((time.perf_counter() - inicio) * 1000)
    finally:
        for conexao, _ in leitores.values():
            conexao.close()

    arquivos = len(DeltaTable(str(raiz)).file_uris())

    return [
        Medida(
            cenario,
            arquivos,
            nome,
            statistics.median(tempos[nome]["read_parquet"]),
            statistics.median(tempos[nome]["delta_scan"]),
            statistics.median(tempos[nome]["attach"]),
            all(resposta == respostas[nome]["read_parquet"] for resposta in respostas[nome].values()),
        )
        for nome in consultas
    ]


def _abrir_leitores(raiz: Path, padrao: str) -> dict[str, tuple[duckdb.DuckDBPyConnection, str]]:
    parquet = duckdb.connect()
    scan = duckdb.connect()
    abrir_no_duckdb(scan, raiz, "tabela_delta")
    fixado = duckdb.connect()
    fixado.execute("load delta")
    fixado.execute(f"attach '{raiz.as_posix()}' as tabela_fixada (type delta, pin_snapshot)")

    return {
        "read_parquet": (parquet, f"read_parquet('{raiz.as_posix()}/{padrao}', hive_partitioning = true)"),
        "delta_scan": (scan, "tabela_delta"),
        "attach": (fixado, "tabela_fixada"),
    }


def formatar_markdown(medidas: list[Medida]) -> str:
    linhas = [
        "| Cenário | Arquivos | Consulta | read_parquet (ms) | delta_scan (ms) | attach com snapshot fixado (ms) | Respostas iguais |",
        "| --- | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for medida in medidas:
        tempos = " | ".join(
            f"{tempo:.1f}".replace(".", ",")
            for tempo in (medida.read_parquet_ms, medida.delta_scan_ms, medida.attach_fixado_ms)
        )
        iguais = "sim" if medida.respostas_iguais else "NÃO"
        linhas.append(f"| {medida.cenario} | {medida.arquivos} | {medida.consulta} | {tempos} | {iguais} |")

    return "\n".join(linhas)


if __name__ == "__main__":
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos.add_argument("silver", type=Path)
    argumentos.add_argument("--repeticoes", type=int, default=5)
    opcoes = argumentos.parse_args()
    print(formatar_markdown(comparar(opcoes.silver, opcoes.repeticoes)))
