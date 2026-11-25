"""Parquet puro, Delta e Iceberg sobre o mesmo Censo e as mesmas operações.

Mede o que a camada analítica faz de fato: carga anual, gravações pequenas do CDC,
compactação, reentrega idempotente e leitura pelo DuckDB. Issue #4; resultados no
ADR 0003. Rode em Linux, com a Silver já gerada:

    uv run --with 'pyiceberg[sql-sqlite,pyarrow]==0.12.0' python -m eduinfra.comparacao_formato_tabela dados/silver --repeticoes 15
"""

import argparse
import shutil
import statistics
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pyarrow as pa

from eduinfra.lake.camada_analitica import TABELA, pastas_da_silver, tabela_larga
from eduinfra.experimentos.comparacao_particionamento import CONSULTAS
from eduinfra.experimentos.formatos_de_tabela import ALTERNATIVAS, Iceberg, Tamanho, dividir_em_lotes, padronizar

LOTES = 20
# Estimativa de docs/caracterizacao.md: 500 vistorias por dia, com 17 itens cada.
ITENS_POR_DIA = 8_500
ITENS_POR_ESCOLA = 17
# Acima disso o upsert do PyIceberg não termina em tempo útil; o ADR 0003 registra os 8.500.
ITENS_DO_UPSERT = (34, 340, 1_700)


@dataclass(frozen=True)
class Medida:
    formato: str
    tamanho: Tamanho
    escrita_s: float
    leitura_ms: dict[str, float]
    respostas: dict[str, list]


@dataclass(frozen=True)
class Reentrega:
    formato: str
    itens: int
    primeira_s: float
    segunda_s: float
    linhas: int
    chaves_distintas: int


@dataclass(frozen=True)
class LeituraDaCopia:
    formato: str
    forma: str
    linhas: int | None
    erro: str | None


@dataclass(frozen=True)
class Upsert:
    itens: int
    primeira_s: float
    segunda_s: float


@dataclass(frozen=True)
class Comparacao:
    linhas: int
    repeticoes: int
    lotes: int
    carga: list[Medida]
    em_lotes: list[Medida]
    compactada: list[Medida]
    reentrega: list[Reentrega]
    copia: list[LeituraDaCopia]
    upsert: list[Upsert]


def comparar(
    diretorio_silver: Path, repeticoes: int = 5, lotes: int = LOTES, itens_do_upsert: tuple[int, ...] = ITENS_DO_UPSERT
) -> Comparacao:
    dados = carregar_dados(diretorio_silver)

    with tempfile.TemporaryDirectory(dir=diretorio_silver.parent, prefix="formato_tabela_") as pasta:
        raiz = Path(pasta)
        carga = _novos_formatos(raiz, "carga", dados.schema)
        medidas_carga = _medir(carga, {nome: _gravar_lotes(formato, [dados]) for nome, formato in carga.items()}, repeticoes)

        em_lotes = _novos_formatos(raiz, "lotes", dados.schema)
        pedacos = dividir_em_lotes(dados, lotes)
        medidas_lotes = _medir(em_lotes, {nome: _gravar_lotes(formato, pedacos) for nome, formato in em_lotes.items()}, repeticoes)
        medidas_compactada = _medir(em_lotes, {nome: _cronometrar(formato.compactar) for nome, formato in em_lotes.items()}, repeticoes)

        return Comparacao(
            linhas=dados.num_rows,
            repeticoes=repeticoes,
            lotes=lotes,
            carga=medidas_carga,
            em_lotes=medidas_lotes,
            compactada=medidas_compactada,
            reentrega=_medir_reentrega(raiz, dados),
            copia=_ler_copias(raiz, carga),
            upsert=[_medir_upsert(raiz / f"upsert_{itens}", dados, itens) for itens in itens_do_upsert],
        )


def carregar_dados(diretorio_silver: Path) -> pa.Table:
    return padronizar(pa.concat_tables(tabela_larga(pasta) for pasta in pastas_da_silver(diretorio_silver)))


def separar_lote_diario(dados: pa.Table, itens: int = ITENS_POR_DIA) -> tuple[pa.Table, pa.Table]:
    escolas = max(itens // ITENS_POR_ESCOLA, 1)
    conexao = duckdb.connect()
    try:
        conexao.register("dados", dados)
        conexao.execute(
            f"create table escolhidas as select distinct co_entidade from dados order by hash(co_entidade) limit {escolas}"
        )
        base = conexao.execute("select d.* from dados d anti join escolhidas using (co_entidade)").to_arrow_table()
        lote = conexao.execute("select d.* from dados d semi join escolhidas using (co_entidade)").to_arrow_table()
    finally:
        conexao.close()

    return padronizar(base), padronizar(lote).slice(0, itens)


def _novos_formatos(raiz: Path, rotulo: str, esquema: pa.Schema) -> dict:
    formatos = {classe.nome: classe(raiz / f"{rotulo}_{classe.__name__}") for classe in ALTERNATIVAS}
    for formato in formatos.values():
        formato.iniciar(esquema)

    return formatos


def _cronometrar(operacao: Callable[[], None]) -> float:
    inicio = time.perf_counter()
    operacao()

    return time.perf_counter() - inicio


def _gravar_lotes(formato, pedacos: list[pa.Table]) -> float:
    inicio = time.perf_counter()
    for pedaco in pedacos:
        formato.acrescentar(pedaco)

    return time.perf_counter() - inicio


def _medir(formatos: dict, escritas: dict[str, float], repeticoes: int) -> list[Medida]:
    leituras, respostas = _ler(formatos, repeticoes)

    return [
        Medida(nome, formato.tamanho(), escritas[nome], leituras[nome], respostas[nome])
        for nome, formato in formatos.items()
    ]


def _ler(formatos: dict, repeticoes: int) -> tuple[dict, dict]:
    leitores = {}
    for nome, formato in formatos.items():
        leitor = duckdb.connect()
        formato.preparar(leitor)
        leitor.execute(f"create view {TABELA} as select * from {formato.origem_de_leitura()}")
        leitores[nome] = leitor

    try:
        respostas = {
            nome: {consulta: leitor.execute(sql).fetchall() for consulta, sql in CONSULTAS.items()}  # aquecimento
            for nome, leitor in leitores.items()
        }
        # Intercalado, como nas outras comparações: uma oscilação da máquina atinge todos por igual.
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


def _medir_reentrega(raiz: Path, dados: pa.Table) -> list[Reentrega]:
    base, lote = separar_lote_diario(dados)
    medidas = []
    for nome, formato in _novos_formatos(raiz, "reentrega", dados.schema).items():
        formato.acrescentar(base)
        primeira = _cronometrar(lambda: formato.reaplicar(lote))
        segunda = _cronometrar(lambda: formato.reaplicar(lote))
        linhas, distintas = _contar_chaves(formato)
        medidas.append(Reentrega(nome, lote.num_rows, primeira, segunda, linhas, distintas))

    return medidas


def _contar_chaves(formato) -> tuple[int, int]:
    conexao = duckdb.connect()
    try:
        formato.preparar(conexao)
        return conexao.execute(
            f"select count(*), count(distinct (ano_censo, co_entidade, item_codigo)) from {formato.origem_de_leitura()}"
        ).fetchone()
    finally:
        conexao.close()


def _ler_copias(raiz: Path, formatos: dict) -> list[LeituraDaCopia]:
    leituras = []
    for nome, formato in formatos.items():
        destino = raiz / f"copia_{type(formato).__name__}"
        shutil.copytree(formato.raiz, destino)
        formas = formato.leituras_da_copia(destino)
        # Com a pasta original no lugar, um caminho absoluto no metadado leria a origem e a prova não valeria nada.
        escondida = formato.raiz.with_name(f"{formato.raiz.name}_escondida")
        formato.raiz.rename(escondida)
        try:
            leituras += [_ler_copia(nome, forma, formato, sql) for forma, sql in formas.items()]
        finally:
            escondida.rename(formato.raiz)

    return leituras


def _ler_copia(nome: str, forma: str, formato, origem: str) -> LeituraDaCopia:
    conexao = duckdb.connect()
    try:
        formato.preparar(conexao)
        return LeituraDaCopia(nome, forma, conexao.execute(f"select count(*) from {origem}").fetchone()[0], None)
    except duckdb.Error as erro:
        return LeituraDaCopia(nome, forma, None, type(erro).__name__)
    finally:
        conexao.close()


def _medir_upsert(pasta: Path, dados: pa.Table, itens: int) -> Upsert:
    base, lote = separar_lote_diario(dados, itens)
    formato = Iceberg(pasta)
    formato.iniciar(dados.schema)
    formato.acrescentar(base)
    tempos = [_cronometrar(lambda: formato.upsert(lote)) for _ in range(2)]

    return Upsert(itens, *tempos)


def respostas_coincidem(comparacao: Comparacao) -> bool:
    medidas = comparacao.carga + comparacao.em_lotes + comparacao.compactada
    referencia = medidas[0].respostas

    return all(medida.respostas == referencia for medida in medidas)


def formatar_markdown(comparacao: Comparacao) -> str:
    linhas = [
        f"Linhas: {_inteiro(comparacao.linhas)}; mediana de {comparacao.repeticoes} repetições, intercaladas entre os formatos.",
        "",
        "### Carga de uma vez",
        "",
        *_tabela_de_medidas(comparacao.carga),
        "",
        f"### {comparacao.lotes} gravações pequenas",
        "",
        *_tabela_de_medidas(comparacao.em_lotes),
        "",
        "### Depois de compactar",
        "",
        *_tabela_de_medidas(comparacao.compactada),
        "",
        "### Reaplicar o mesmo lote duas vezes",
        "",
        "| Formato | Itens | Primeira (s) | Segunda (s) | Linhas | Chaves distintas |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        *[
            f"| {r.formato} | {_inteiro(r.itens)} | {_decimal(r.primeira_s, 3)} | {_decimal(r.segunda_s, 3)} "
            f"| {_inteiro(r.linhas)} | {_inteiro(r.chaves_distintas)} |"
            for r in comparacao.reentrega
        ],
        "",
        "### Upsert do PyIceberg",
        "",
        "| Itens | Primeira (s) | Segunda (s) |",
        "| ---: | ---: | ---: |",
        *[f"| {_inteiro(u.itens)} | {_decimal(u.primeira_s, 1)} | {_decimal(u.segunda_s, 1)} |" for u in comparacao.upsert],
        "",
        "### Copiar a pasta e ler na cópia, sem a original",
        "",
        "| Formato | Forma de leitura | Resultado |",
        "| --- | --- | --- |",
        *[
            f"| {c.formato} | {c.forma} | {_inteiro(c.linhas) + ' linhas' if c.erro is None else 'falha: ' + c.erro} |"
            for c in comparacao.copia
        ],
        "",
        "Respostas idênticas em todos os cenários: " + ("sim" if respostas_coincidem(comparacao) else "NÃO"),
    ]

    return "\n".join(linhas)


def _tabela_de_medidas(medidas: list[Medida]) -> list[str]:
    return [
        "| Formato | Arquivos | Dados | Metadados | Escrita (s) | Uma UF (ms) | Uma UF por rede (ms) | Nacional (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        *[
            f"| {m.formato} | {m.tamanho.arquivos} | {_decimal(m.tamanho.bytes_em_disco / 1e6, 2)} MB "
            f"| {m.tamanho.arquivos_de_metadados} | {_decimal(m.escrita_s, 2)} | {_decimal(m.leitura_ms['uma_uf'], 1)} "
            f"| {_decimal(m.leitura_ms['uma_uf_por_rede'], 1)} | {_decimal(m.leitura_ms['nacional'], 1)} |"
            for m in medidas
        ],
    ]


def _decimal(valor: float, casas: int) -> str:
    return f"{valor:.{casas}f}".replace(".", ",")


def _inteiro(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")


if __name__ == "__main__":
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos.add_argument("silver", type=Path)
    argumentos.add_argument("--repeticoes", type=int, default=5)
    argumentos.add_argument("--lotes", type=int, default=LOTES)
    opcoes = argumentos.parse_args()
    print(formatar_markdown(comparar(opcoes.silver, opcoes.repeticoes, opcoes.lotes)))
