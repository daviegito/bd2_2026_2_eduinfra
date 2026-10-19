"""Particionamento, compactação e evolução de esquema da tabela Delta.

Grava a tabela analítica (camada_analitica.tabela_larga) numa pasta temporária
de três formas e mede arquivos, bytes e tempo das leituras típicas:

- particionamento: sem partição, por ano e por ano e UF, cada um gravado de uma vez;
- compactação: a mesma tabela gravada em muitos lotes pequenos, como faria uma
  ingestão incremental, antes e depois de OPTIMIZE (compact) e VACUUM;
- evolução de esquema: acrescenta uma coluna e confere que os arquivos antigos
  não foram reescritos.

Issue #24; resultados em docs/camada-analitica.md.
"""

import shutil
import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
from deltalake import DeltaTable, Field, write_deltalake

from eduinfra.camada_analitica import (
    PARTICOES,
    TABELA,
    abrir_no_duckdb,
    arquivos_da_tabela,
    gravar,
    pastas_da_silver,
    tabela_larga,
)

LAYOUTS = {
    "sem_particao": (),
    "ano": ("ano_censo",),
    "ano_uf": ("ano_censo", "sg_uf"),
}

# Leituras da camada analítica. As respostas têm de ser iguais em todos os
# cenários: trocar o layout não pode mudar o dado.
CONSULTAS = {
    "uma_uf": """
        select count(*)
        from vistoria_item
        where sg_uf = 'DF' and item_codigo = 'lab_ciencias' and situacao = 'inexistente'
    """,
    "uma_uf_por_rede": """
        select tp_dependencia, tp_localizacao, count(*) filter (where situacao = 'inexistente')
        from vistoria_item
        where sg_uf = 'BA'
        group by all
        order by all
    """,
    "nacional": """
        select sg_uf, item_codigo, count(*) filter (where situacao = 'inexistente')
        from vistoria_item
        group by all
        order by all
    """,
}

# Coluna da demonstração de evolução de esquema: a posição no WAL de cada
# evento, que a captura de mudanças (issue #25) usa para deduplicar.
COLUNA_NOVA = "lsn_origem"


@dataclass(frozen=True)
class Medida:
    cenario: str
    arquivos: int
    bytes_em_disco: int
    escrita_s: float
    leitura_ms: dict[str, float]
    respostas: dict[str, list]


@dataclass(frozen=True)
class EvolucaoEsquema:
    coluna: str
    versao_antes: int
    versao_depois: int
    arquivos_antigos: int
    arquivos_antigos_intactos: bool
    linhas_antigas_com_nulo: int
    linhas_novas_com_valor: int


@dataclass(frozen=True)
class Comparacao:
    linhas: int
    repeticoes: int
    lotes: int
    particionamento: list[Medida]
    compactacao: list[Medida]
    evolucao: EvolucaoEsquema


def comparar(diretorio_silver: Path, repeticoes: int = 5, lotes: int = 20) -> Comparacao:
    dados = pa.concat_tables(tabela_larga(pasta) for pasta in pastas_da_silver(diretorio_silver))

    with tempfile.TemporaryDirectory(dir=diretorio_silver.parent, prefix="comparacao_particionamento_") as raiz:
        raiz = Path(raiz)

        escritas = {
            nome: _escrever_de_uma_vez(raiz / nome, dados, particoes, repeticoes)
            for nome, particoes in LAYOUTS.items()
        }

        antes, depois = raiz / "em_lotes", raiz / "compactada"
        escritas[antes.name] = _escrever_em_lotes(antes, dados, lotes)
        shutil.copytree(antes, depois)
        escritas[depois.name] = _compactar(depois)

        caminhos = {nome: raiz / nome for nome in escritas}
        tamanhos = {nome: _tamanho(caminho) for nome, caminho in caminhos.items()}
        leituras, respostas = _ler(caminhos, repeticoes)

        evolucao = _evoluir_esquema(depois, dados)

    medidas = {
        nome: Medida(
            cenario=nome,
            arquivos=tamanhos[nome][0],
            bytes_em_disco=tamanhos[nome][1],
            escrita_s=escritas[nome],
            leitura_ms=leituras[nome],
            respostas=respostas[nome],
        )
        for nome in caminhos
    }

    return Comparacao(
        linhas=dados.num_rows,
        repeticoes=repeticoes,
        lotes=lotes,
        particionamento=[medidas[nome] for nome in LAYOUTS],
        compactacao=[medidas["em_lotes"], medidas["compactada"]],
        evolucao=evolucao,
    )


def _escrever_de_uma_vez(destino: Path, dados: pa.Table, particoes: tuple[str, ...], repeticoes: int) -> float:
    # Cada repetição grava numa tabela nova, para não medir o custo de
    # sobrescrever uma versão anterior.
    tempos = []
    for _ in range(repeticoes):
        shutil.rmtree(destino, ignore_errors=True)
        inicio = time.perf_counter()
        gravar(destino, dados, particoes, modo="error")
        tempos.append(time.perf_counter() - inicio)

    return statistics.median(tempos)


def _escrever_em_lotes(destino: Path, dados: pa.Table, lotes: int) -> float:
    # Cada lote leva escolas inteiras e cai em todas as UFs: é o que uma carga
    # incremental por execução produz, um arquivo por partição por commit.
    conexao = duckdb.connect()
    try:
        conexao.register("dados", dados)
        partes = [
            conexao.execute("select * from dados where co_entidade % ? = ?", [lotes, lote]).to_arrow_table()
            for lote in range(lotes)
        ]
    finally:
        conexao.close()

    inicio = time.perf_counter()
    for parte in partes:
        gravar(destino, parte, PARTICOES, modo="append")

    return time.perf_counter() - inicio


def _compactar(destino: Path) -> float:
    tabela = DeltaTable(str(destino))
    inicio = time.perf_counter()
    tabela.optimize.compact()
    # Sem período de retenção: a pasta é temporária e ninguém lê versões antigas.
    tabela.vacuum(retention_hours=0, enforce_retention_duration=False, dry_run=False)
    # O checkpoint resume o log num Parquet: o leitor deixa de reler um JSON
    # por commit para descobrir quais arquivos valem.
    tabela.create_checkpoint()

    return time.perf_counter() - inicio


def _tamanho(caminho: Path) -> tuple[int, int]:
    arquivos = arquivos_da_tabela(DeltaTable(str(caminho)))

    return len(arquivos), sum(arquivo.stat().st_size for arquivo in arquivos)


def _ler(caminhos: dict[str, Path], repeticoes: int) -> tuple[dict, dict]:
    leitores = {}
    for nome, caminho in caminhos.items():
        leitor = duckdb.connect()
        abrir_no_duckdb(leitor, caminho)
        leitores[nome] = leitor

    try:
        respostas = {
            nome: {consulta: leitor.execute(sql).fetchall() for consulta, sql in CONSULTAS.items()}  # aquecimento
            for nome, leitor in leitores.items()
        }

        # Intercalado, como em comparacao_compressao: uma oscilação da máquina
        # atinge todos os cenários por igual.
        tempos = {nome: {consulta: [] for consulta in CONSULTAS} for nome in leitores}
        for _ in range(repeticoes):
            for consulta, sql in CONSULTAS.items():
                for nome, leitor in leitores.items():
                    inicio = time.perf_counter()
                    leitor.execute(sql).fetchall()
                    tempos[nome][consulta].append(time.perf_counter() - inicio)
    finally:
        for leitor in leitores.values():
            leitor.close()

    leituras = {
        nome: {consulta: statistics.median(valores) * 1000 for consulta, valores in por_consulta.items()}
        for nome, por_consulta in tempos.items()
    }

    return leituras, respostas


def _evoluir_esquema(caminho: Path, dados: pa.Table) -> EvolucaoEsquema:
    tabela = DeltaTable(str(caminho))
    versao_antes = tabela.version()
    antigos = {arquivo: arquivo.stat().st_mtime_ns for arquivo in arquivos_da_tabela(tabela)}

    tabela.alter.add_columns([Field(COLUNA_NOVA, "long", nullable=True)])

    novos = dados.filter(pc.equal(dados.column("sg_uf"), "DF")).slice(0, 1000)
    novos = novos.append_column(COLUNA_NOVA, pa.array(range(1, novos.num_rows + 1), pa.int64()))
    write_deltalake(str(caminho), novos, mode="append", partition_by=list(PARTICOES))

    tabela = DeltaTable(str(caminho))
    atuais = set(arquivos_da_tabela(tabela))
    intactos = all(
        arquivo in atuais and arquivo.exists() and arquivo.stat().st_mtime_ns == mtime
        for arquivo, mtime in antigos.items()
    )

    conexao = duckdb.connect()
    try:
        abrir_no_duckdb(conexao, caminho)
        com_nulo, com_valor = conexao.execute(
            f"select count(*) filter (where {COLUNA_NOVA} is null), count({COLUNA_NOVA}) from {TABELA}"
        ).fetchone()
    finally:
        conexao.close()

    return EvolucaoEsquema(
        coluna=COLUNA_NOVA,
        versao_antes=versao_antes,
        versao_depois=tabela.version(),
        arquivos_antigos=len(antigos),
        arquivos_antigos_intactos=intactos,
        linhas_antigas_com_nulo=com_nulo,
        linhas_novas_com_valor=com_valor,
    )


def respostas_coincidem(comparacao: Comparacao) -> bool:
    medidas = comparacao.particionamento + comparacao.compactacao
    referencia = medidas[0].respostas

    return all(medida.respostas == referencia for medida in medidas)


NOMES = {
    "sem_particao": "Sem partição",
    "ano": "Por ano",
    "ano_uf": "Por ano e UF",
    "em_lotes": "Antes: {lotes} lotes, por ano e UF",
    "compactada": "Depois de compactar e limpar",
}


def formatar_markdown(comparacao: Comparacao) -> str:
    linhas = [
        f"Linhas: {_inteiro(comparacao.linhas)}",
        (
            f"Mediana de {comparacao.repeticoes} repetições; leituras pela extensão delta do DuckDB, "
            "intercaladas entre os cenários, depois de uma execução de aquecimento."
        ),
        "",
        "### Particionamento",
        "",
        *_tabela(comparacao.particionamento, comparacao.lotes, "Escrita (s)"),
        "",
        "### Compactação",
        "",
        *_tabela(comparacao.compactacao, comparacao.lotes, "Escrita ou compactação (s)"),
        "",
        "### Evolução de esquema",
        "",
        _evolucao(comparacao.evolucao),
        "",
        "Respostas idênticas em todos os cenários: "
        + ("sim" if respostas_coincidem(comparacao) else "NÃO — conferir antes de usar os números"),
    ]

    return "\n".join(linhas)


def _tabela(medidas: list[Medida], lotes: int, rotulo_escrita: str) -> list[str]:
    linhas = [
        f"| Cenário | Arquivos | Em disco | Tamanho médio | {rotulo_escrita} | Uma UF (ms) | Uma UF por rede (ms) | Nacional (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for medida in medidas:
        linhas.append(
            "| {} | {} | {:.1f} MB | {:.0f} kB | {:.2f} | {:.1f} | {:.1f} | {:.1f} |".format(
                NOMES[medida.cenario].format(lotes=lotes),
                medida.arquivos,
                medida.bytes_em_disco / 1e6,
                medida.bytes_em_disco / medida.arquivos / 1e3,
                medida.escrita_s,
                medida.leitura_ms["uma_uf"],
                medida.leitura_ms["uma_uf_por_rede"],
                medida.leitura_ms["nacional"],
            ).replace(".", ",")
        )

    return linhas


def _evolucao(evolucao: EvolucaoEsquema) -> str:
    situacao = "continuam os mesmos, sem reescrita" if evolucao.arquivos_antigos_intactos else "FORAM REESCRITOS"

    return (
        f"Coluna `{evolucao.coluna}` acrescentada da versão {evolucao.versao_antes} para a "
        f"{evolucao.versao_depois} da tabela. Os {evolucao.arquivos_antigos} arquivos anteriores {situacao}; "
        f"{_inteiro(evolucao.linhas_antigas_com_nulo)} linhas antigas leem a coluna como nula e "
        f"{_inteiro(evolucao.linhas_novas_com_valor)} linhas novas a trazem preenchida."
    )


def _inteiro(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")
