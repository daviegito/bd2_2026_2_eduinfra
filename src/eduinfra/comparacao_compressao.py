"""Serialização e compressão da camada analítica, escolhidas pelo padrão de acesso.

Reescreve a camada Silver gerada pela carga em CSV e em Parquet com cada codec,
e mede o que importa para o uso analítico: espaço em disco, custo de escrita e
tempo das leituras típicas (poucas colunas de muitas linhas, filtro por UF e
varredura completa). Issue #28; resultados em docs/serializacao-compressao.md.
"""

import os
import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

TABELAS = ("municipio", "escola", "vistoria_item")

# Ordem da tabela de resultados: texto primeiro, depois Parquet do mais leve
# para o mais caro em CPU.
FORMATOS = ("csv", "uncompressed", "snappy", "zstd")

# As três formas de leitura da camada analítica. Cada uma devolve um resultado
# que precisa ser idêntico em todos os formatos: a medição também confere que
# trocar o codec não muda o dado.
CONSULTAS = {
    "poucas_colunas": """
        select item_codigo, count(*)
        from vistoria_item
        where situacao = 'inexistente'
        group by item_codigo
        order by item_codigo
    """,
    "filtro_uf": """
        select count(*)
        from vistoria_item i
        join escola e using (co_entidade)
        join municipio m using (co_municipio)
        where m.sg_uf = 'DF'
          and i.item_codigo = 'lab_ciencias'
          and i.situacao = 'inexistente'
    """,
    "varredura_completa": """
        select count(*), count(distinct co_entidade), count(distinct item_codigo), count(distinct situacao)
        from vistoria_item
    """,
}


@dataclass(frozen=True)
class Medida:
    formato: str
    bytes_em_disco: int
    escrita_s: float
    leitura_ms: dict[str, float]
    respostas: dict[str, list]


@dataclass(frozen=True)
class Comparacao:
    linhas: dict[str, int]
    repeticoes: int
    medidas: list[Medida]


def comparar(diretorio_silver: Path, repeticoes: int = 5) -> Comparacao:
    origem = {tabela: _localizar(diretorio_silver, tabela) for tabela in TABELAS}

    conexao = duckdb.connect()
    try:
        for tabela, arquivo in origem.items():
            conexao.execute(
                f"create table {tabela} as select * from read_parquet('{arquivo.as_posix()}')"
            )
        linhas = {
            tabela: conexao.execute(f"select count(*) from {tabela}").fetchone()[0] for tabela in TABELAS
        }

        with tempfile.TemporaryDirectory(dir=diretorio_silver.parent, prefix="comparacao_compressao_") as raiz:
            escritas = {formato: _escrever(conexao, Path(raiz) / formato, formato, repeticoes) for formato in FORMATOS}
            leituras, respostas = _ler(Path(raiz), repeticoes)
    finally:
        conexao.close()

    medidas = [
        Medida(
            formato=formato,
            bytes_em_disco=escritas[formato][0],
            escrita_s=escritas[formato][1],
            leitura_ms=leituras[formato],
            respostas=respostas[formato],
        )
        for formato in FORMATOS
    ]

    return Comparacao(linhas=linhas, repeticoes=repeticoes, medidas=medidas)


def _localizar(diretorio_silver: Path, tabela: str) -> Path:
    # Aceita a Silver plana da E1 e a particionada por ano (ano_censo=AAAA/).
    candidatos = sorted(diretorio_silver.rglob(f"{tabela}.parquet"))
    if not candidatos:
        raise RuntimeError(
            f"{tabela}.parquet não encontrado em {diretorio_silver}: rode a carga antes da comparação"
        )

    return candidatos[-1]


def _arquivo(raiz: Path, formato: str, tabela: str) -> Path:
    return raiz / formato / f"{tabela}.{'csv' if formato == 'csv' else 'parquet'}"


def _escrever(conexao: duckdb.DuckDBPyConnection, pasta: Path, formato: str, repeticoes: int) -> tuple[int, float]:
    pasta.mkdir(parents=True)
    opcoes = "format csv, header true" if formato == "csv" else f"format parquet, compression {formato}"
    arquivos = [_arquivo(pasta.parent, formato, tabela) for tabela in TABELAS]

    tempos = []
    for _ in range(repeticoes):
        inicio = time.perf_counter()
        for tabela, arquivo in zip(TABELAS, arquivos):
            conexao.execute(f"copy {tabela} to '{arquivo.as_posix()}' ({opcoes})")
        tempos.append(time.perf_counter() - inicio)

    return sum(os.path.getsize(arquivo) for arquivo in arquivos), statistics.median(tempos)


def _ler(raiz: Path, repeticoes: int) -> tuple[dict, dict]:
    # Uma conexão por formato, lendo dos arquivos, não das tabelas em memória.
    leitores = {}
    for formato in FORMATOS:
        leitor = duckdb.connect()
        for tabela in TABELAS:
            caminho = _arquivo(raiz, formato, tabela).as_posix()
            leitura = f"read_csv('{caminho}', header = true)" if formato == "csv" else f"read_parquet('{caminho}')"
            leitor.execute(f"create view {tabela} as select * from {leitura}")
        leitores[formato] = leitor

    try:
        respostas = {
            formato: {nome: leitor.execute(sql).fetchall() for nome, sql in CONSULTAS.items()}  # aquecimento
            for formato, leitor in leitores.items()
        }

        # Intercalado: em cada repetição, a mesma consulta roda em todos os
        # formatos em sequência, para que uma oscilação da máquina atinja
        # todos por igual em vez de cair inteira sobre um formato só.
        tempos = {formato: {nome: [] for nome in CONSULTAS} for formato in FORMATOS}
        for _ in range(repeticoes):
            for nome, sql in CONSULTAS.items():
                for formato, leitor in leitores.items():
                    inicio = time.perf_counter()
                    leitor.execute(sql).fetchall()
                    tempos[formato][nome].append(time.perf_counter() - inicio)
    finally:
        for leitor in leitores.values():
            leitor.close()

    leituras = {
        formato: {nome: statistics.median(valores) * 1000 for nome, valores in por_consulta.items()}
        for formato, por_consulta in tempos.items()
    }

    return leituras, respostas


def respostas_coincidem(comparacao: Comparacao) -> bool:
    referencia = comparacao.medidas[0].respostas
    return all(medida.respostas == referencia for medida in comparacao.medidas)


def formatar_markdown(comparacao: Comparacao) -> str:
    csv = next(m for m in comparacao.medidas if m.formato == "csv")
    linhas = [
        "Linhas: " + ", ".join(f"{t} {n:,}".replace(",", ".") for t, n in comparacao.linhas.items()),
        f"Mediana de {comparacao.repeticoes} repetições; leituras intercaladas entre os formatos, depois de uma execução de aquecimento.",
        "",
        "| Formato | Em disco | Relação ao CSV | Escrita (s) | Poucas colunas (ms) | Filtro por UF (ms) | Varredura completa (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for medida in comparacao.medidas:
        nome = "CSV (texto)" if medida.formato == "csv" else f"Parquet {medida.formato}"
        linhas.append(
            "| {} | {:.1f} MB | {:.0%} | {:.2f} | {:.1f} | {:.1f} | {:.1f} |".format(
                nome,
                medida.bytes_em_disco / 1e6,
                medida.bytes_em_disco / csv.bytes_em_disco,
                medida.escrita_s,
                medida.leitura_ms["poucas_colunas"],
                medida.leitura_ms["filtro_uf"],
                medida.leitura_ms["varredura_completa"],
            ).replace(".", ",")
        )
    linhas.append("")
    linhas.append(
        "Respostas idênticas em todos os formatos: "
        + ("sim" if respostas_coincidem(comparacao) else "NÃO — conferir antes de usar os números")
    )

    return "\n".join(linhas)
