"""O que acontece com cada formato quando a gravação morre ou há leitor concorrente.

Mata o processo gravador em pontos diferentes da gravação e confere a tabela depois;
em seguida, consulta a tabela sem parar enquanto ela recebe lotes e é compactada.
Issue #4; resultados no ADR 0003. Rode em Linux, com a Silver já gerada:

    uv run --with 'pyiceberg[sql-sqlite,pyarrow]==0.12.0' python -m eduinfra.comparacao_falha_tabela dados/silver
"""

import argparse
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import duckdb

from eduinfra.comparacao_formato_tabela import carregar_dados
from eduinfra.formatos_de_tabela import ALTERNATIVAS, ParquetPuroDireto, dividir_em_lotes

FRACOES_DA_GRAVACAO = (0.2, 0.4, 0.6, 0.8)
RODADAS_DA_FALHA = 3
LOTES_DA_COMPACTACAO = 20
RODADAS_DE_COMPACTACAO = 8
AQUECIMENTO_DO_LEITOR_S = 0.5
SINAL_DE_PRONTO = "pronto"

FORMATOS_NA_FALHA = (ParquetPuroDireto, *ALTERNATIVAS)
ANTES = "igual a antes"
COMPLETA = "completa"
PARCIAL = "parcial"
ILEGIVEL = "ilegível"
RECUPEROU = "recuperou"
NAO_RECUPEROU = "não recuperou"


@dataclass(frozen=True)
class ResultadoDaFalha:
    formato: str
    gravacao_s: float
    estados: Counter
    recuperacao: Counter


@dataclass(frozen=True)
class ResultadoDoLeitor:
    formato: str
    leituras: int
    validas: int
    contagem_errada: int
    erros: int


def classificar(total: int | None, linhas_antes: int, linhas_depois: int) -> str:
    if total is None:
        return ILEGIVEL
    if total == linhas_antes:
        return ANTES
    if total == linhas_depois:
        return COMPLETA

    return PARCIAL


def matar_gravador(
    diretorio_silver: Path, rodadas: int = RODADAS_DA_FALHA, classes: tuple = FORMATOS_NA_FALHA
) -> list[ResultadoDaFalha]:
    base, novo = dividir_em_lotes(carregar_dados(diretorio_silver), 2)
    linhas_antes, linhas_depois = base.num_rows, base.num_rows + novo.num_rows

    resultados = []
    with tempfile.TemporaryDirectory(dir=diretorio_silver.parent, prefix="falha_tabela_") as pasta:
        for classe in classes:
            medida = _preparar(classe, Path(pasta) / f"{classe.__name__}_medida", base)
            processo = _iniciar_gravador(classe, medida.raiz, diretorio_silver)
            inicio = time.perf_counter()
            processo.wait()
            gravacao_s = time.perf_counter() - inicio

            estados, recuperacao = Counter(), Counter()
            for rodada in range(rodadas):
                for fracao in FRACOES_DA_GRAVACAO:
                    raiz = Path(pasta) / f"{classe.__name__}_{rodada}_{int(fracao * 100)}"
                    formato = _preparar(classe, raiz, base)
                    processo = _iniciar_gravador(classe, raiz, diretorio_silver)
                    time.sleep(gravacao_s * fracao)
                    processo.kill()
                    processo.wait()
                    estados[classificar(_contar(formato), linhas_antes, linhas_depois)] += 1
                    recuperacao[_tentar_recuperar(formato, novo, linhas_depois)] += 1
            resultados.append(ResultadoDaFalha(classe.nome, gravacao_s, estados, recuperacao))

    return resultados


def _preparar(classe, raiz: Path, base):
    formato = classe(raiz)
    formato.iniciar(base.schema)
    formato.acrescentar(base)

    return formato


def _iniciar_gravador(classe, raiz: Path, diretorio_silver: Path) -> subprocess.Popen:
    processo = subprocess.Popen(
        [sys.executable, "-m", __spec__.name, "--gravador", classe.__name__, str(raiz), str(diretorio_silver)],
        stdout=subprocess.PIPE,
        text=True,
    )
    if processo.stdout.readline().strip() != SINAL_DE_PRONTO:
        raise RuntimeError("o processo gravador terminou antes de começar a gravar")

    return processo


def _gravar_e_sair(nome_da_classe: str, raiz: Path, diretorio_silver: Path) -> None:
    classe = next(classe for classe in FORMATOS_NA_FALHA if classe.__name__ == nome_da_classe)
    _, novo = dividir_em_lotes(carregar_dados(diretorio_silver), 2)
    formato = classe(raiz)
    print(SINAL_DE_PRONTO, flush=True)
    formato.acrescentar(novo)


def _contar(formato) -> int | None:
    conexao = duckdb.connect()
    try:
        formato.preparar(conexao)
        return conexao.execute(f"select count(*) from {formato.origem_de_leitura()}").fetchone()[0]
    except duckdb.Error:
        return None
    finally:
        conexao.close()


def _tentar_recuperar(formato, novo, linhas_depois: int) -> str:
    try:
        formato.reaplicar(novo)
    except duckdb.Error:
        return NAO_RECUPEROU

    return RECUPEROU if _contar(formato) == linhas_depois else NAO_RECUPEROU


def ler_durante_compactacao(
    diretorio_silver: Path,
    rodadas: int = RODADAS_DE_COMPACTACAO,
    lotes: int = LOTES_DA_COMPACTACAO,
    classes: tuple = ALTERNATIVAS,
) -> list[ResultadoDoLeitor]:
    pedacos = dividir_em_lotes(carregar_dados(diretorio_silver), lotes)
    iniciais, seguintes = pedacos[:lotes // 2], pedacos[lotes // 2:][:rodadas]

    resultados = []
    with tempfile.TemporaryDirectory(dir=diretorio_silver.parent, prefix="leitor_tabela_") as pasta:
        for classe in classes:
            formato = classe(Path(pasta) / classe.__name__)
            formato.iniciar(pedacos[0].schema)
            for pedaco in iniciais:
                formato.acrescentar(pedaco)
            resultados.append(_ler_enquanto_compacta(formato, iniciais, seguintes))

    return resultados


def _ler_enquanto_compacta(formato, iniciais: list, seguintes: list) -> ResultadoDoLeitor:
    soma = sum(pedaco.num_rows for pedaco in iniciais)
    contagens_validas = {soma}
    contagens, erros = Counter(), Counter()
    parar = threading.Event()

    def ler() -> None:
        conexao = duckdb.connect()
        formato.preparar(conexao)
        while not parar.is_set():
            try:
                contagens[conexao.execute(f"select count(*) from {formato.origem_de_leitura()}").fetchone()[0]] += 1
            except duckdb.Error:
                erros["leitura"] += 1
        conexao.close()

    leitor = threading.Thread(target=ler)
    leitor.start()
    try:
        time.sleep(AQUECIMENTO_DO_LEITOR_S)
        for pedaco in seguintes:
            formato.acrescentar(pedaco)
            soma += pedaco.num_rows
            contagens_validas.add(soma)
            formato.compactar()
    finally:
        parar.set()
        leitor.join()

    validas = sum(vezes for contagem, vezes in contagens.items() if contagem in contagens_validas)
    erradas = sum(contagens.values()) - validas

    return ResultadoDoLeitor(formato.nome, validas + erradas + erros["leitura"], validas, erradas, erros["leitura"])


def formatar_markdown(falhas: list[ResultadoDaFalha], leitores: list[ResultadoDoLeitor]) -> str:
    linhas = [
        "### Gravador morto no meio da gravação",
        "",
        "| Formato | Gravação (s) | Igual a antes | Completa | Parcial | Ilegível | Reaplicar recuperou |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for falha in falhas:
        tentativas = sum(falha.estados.values())
        linhas.append(
            f"| {falha.formato} | {f'{falha.gravacao_s:.2f}'.replace('.', ',')} | {falha.estados[ANTES]} "
            f"| {falha.estados[COMPLETA]} | {falha.estados[PARCIAL]} | {falha.estados[ILEGIVEL]} "
            f"| {falha.recuperacao[RECUPEROU]} de {tentativas} |"
        )
    linhas += [
        "",
        "### Leitor concorrente durante a compactação",
        "",
        "| Formato | Leituras | Válidas | Contagem errada | Erro de leitura |",
        "| --- | ---: | ---: | ---: | ---: |",
        *[f"| {r.formato} | {r.leituras} | {r.validas} | {r.contagem_errada} | {r.erros} |" for r in leitores],
    ]

    return "\n".join(linhas)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--gravador"]:
        _gravar_e_sair(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
    else:
        argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
        argumentos.add_argument("silver", type=Path)
        opcoes = argumentos.parse_args()
        print(formatar_markdown(matar_gravador(opcoes.silver), ler_durante_compactacao(opcoes.silver)))
