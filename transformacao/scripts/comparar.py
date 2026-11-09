"""Mede SQL direto e dbt no MESMO banco descartável e verifica igualdade exata."""

import argparse
import csv
import importlib.metadata
import json
import os
import platform
import statistics
import time
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

import psycopg
from psycopg import sql

from alembic import command
from alembic.config import Config

from eduinfra import eventos, modelo_analitico
from eduinfra.configuracao import Configuracao, carregar_configuracao
from eduinfra.fontes_transformacao import Entradas, bloquear, preparar
from eduinfra.ingestao import ingerir
from eduinfra.microdados import garantir_microdados
from executar import PROJETO, dbt, verificar_slot


def comparar_tabelas(conexao) -> list[tuple[str, int, int]]:
    resultados = []
    for tabela in modelo_analitico.TABELAS_DA_GOLD:
        consulta = sql.SQL("""
            select count(*) from (
                (select * from gold.{t} except all select * from gold_dbt.{t})
                union all
                (select * from gold_dbt.{t} except all select * from gold.{t})
            ) diferencas
        """).format(t=sql.Identifier(tabela))
        diferencas = conexao.execute(consulta).fetchone()[0]
        linhas = conexao.execute(sql.SQL("select count(*) from gold.{}").format(sql.Identifier(tabela))).fetchone()[0]
        if diferencas:
            raise RuntimeError(f"{tabela}: {diferencas} diferenças entre SQL e dbt")
        resultados.append((tabela, linhas, diferencas))
    return resultados


def linhas_nao_vazias(texto: str) -> int:
    return sum(bool(linha.strip()) and not linha.lstrip().startswith(("--", "#")) for linha in texto.splitlines())


def preparar_recorte(configuracao: Configuracao) -> Configuracao:
    """Reproduz o recorte real sem depender de CSVs ou volumes desta máquina."""
    if configuracao.ano_censo != 2024:
        raise RuntimeError("o benchmark documentado usa o Censo 2024")
    with psycopg.connect(configuracao.dsn) as conexao:
        verificar_slot(conexao)
        if conexao.execute("select to_regclass('public.escola')").fetchone()[0]:
            if conexao.execute("select exists(select 1 from escola)").fetchone()[0]:
                raise RuntimeError("--preparar-recorte requer banco vazio para não misturar populações")
    bruto = garantir_microdados(configuracao)
    recorte = replace(configuracao, diretorio_dados=configuracao.diretorio_dados / "_benchmark48")
    destino = recorte.diretorio_dados / "bruto" / "microdados_ed_basica_2024.csv"
    destino.parent.mkdir(parents=True, exist_ok=True)
    with bruto.open(encoding="latin-1", newline="") as origem, destino.open("w", encoding="latin-1", newline="") as saida:
        leitor = csv.reader(origem, delimiter=";")
        cabecalho = next(leitor)
        indice_uf = cabecalho.index("SG_UF")
        escritor = csv.writer(saida, delimiter=";")
        escritor.writerow(cabecalho)
        escritor.writerows(linha for linha in leitor if linha[indice_uf] in {"SP", "DF", "GO", "MT", "MS"})
    command.upgrade(Config(str(configuracao.arquivo_alembic)), "head")
    ingerir(recorte)
    eventos.carregar_da_origem(recorte)
    return recorte


def medir(repeticoes: int, configuracao: Configuracao) -> str:
    tempos = {"SQL direto (com preparo, sem testes declarativos)": [], "dbt run (com preparo)": [], "dbt build (com preparo e testes)": []}
    with bloquear(configuracao.dsn) as conexao:
        verificar_slot(conexao)
        inicio = time.perf_counter()
        entradas = replace(Entradas.do_ambiente(configuracao), enem=None)
        resumo = preparar(conexao, entradas)
        segundos_preparo = time.perf_counter() - inicio
        for _ in range(repeticoes):
            inicio = time.perf_counter()
            modelo_analitico.construir(configuracao)
            tempos["SQL direto (com preparo, sem testes declarativos)"].append(time.perf_counter() - inicio)
            for comando, rotulo in [("run", "dbt run (com preparo)"), ("build", "dbt build (com preparo e testes)")]:
                inicio = time.perf_counter()
                preparar(conexao, entradas)
                dbt(comando, configuracao.dsn, False, "gold_dbt")
                tempos[rotulo].append(time.perf_counter() - inicio)
            resultados = comparar_tabelas(conexao)
        dbt("docs generate", configuracao.dsn, False, "gold_dbt")
        versao_pg = conexao.execute("select version()").fetchone()[0]
    alvo = Path(os.environ.get("DBT_TARGET_PATH", PROJETO / "target"))
    manifesto = json.loads((alvo / "manifest.json").read_text())
    # docs generate sobrescreve run_results: a quantidade de testes vem do grafo versionado.
    testes = [n for n in manifesto["nodes"].values()
              if n["resource_type"] == "test" and n["config"]["enabled"]]
    modelos = [n for n in manifesto["nodes"].values() if n["resource_type"] == "model"]
    arestas = sum(len(n["depends_on"]["nodes"]) for n in modelos)
    sql_nulo = (
        modelo_analitico.CONSULTAS_DA_DIMENSAO + modelo_analitico.CONSULTAS_DE_STAGING
        + modelo_analitico.CONSULTAS_DA_SCD + modelo_analitico.CONSULTAS_DO_FATO_FINO
        + modelo_analitico._consultas_do_fato_largo(["biblioteca"])
    )
    linhas_sql = sum(linhas_nao_vazias(consulta) for consulta in sql_nulo)
    linhas_dbt = sum(linhas_nao_vazias(p.read_text()) for p in (PROJETO / "models").rglob("*.sql")
                     if "enem" not in p.name and "desempenho" not in p.name)
    linhas = [
        "# Comparação da issue #48 — SQL direto e dbt", "",
        f"Data UTC: {datetime.now(timezone.utc).isoformat()}",
        f"Python: {platform.python_version()}; sistema: {platform.system()} {platform.machine()}.",
        f"PostgreSQL: {versao_pg}.",
        f"dbt-core: {importlib.metadata.version('dbt-core')}; dbt-postgres: {importlib.metadata.version('dbt-postgres')}.",
        "", "## Condições", "",
        "Banco de desenvolvimento dedicado, sem slot de CDC. Entradas e origem ficam paradas durante a medição.",
        "A opção nula é o construtor SQL de construir-gold, agendável por cron; mede-se a execução, sem instalar nem medir o agendador.",
        "Recorte público do Censo 2024: SP, DF, GO, MT e MS. ENEM não participa deste benchmark.",
        f"Preparo inicial de validação: {segundos_preparo:.3f} s (fora das amostras). Cada amostra inclui seu próprio preparo.",
        f"Entradas: `{json.dumps(resumo, ensure_ascii=False, sort_keys=True)}`.",
        "SQL direto lê cadastros do OLTP e copia a fonte canônica Delta; dbt prepara um retrato Silver/Delta. Cada tempo inclui esse preparo e a igualdade exata confere os resultados.",
        "Cada tempo dbt inclui inicialização do processo. As rodadas são sequenciais, com cache possivelmente aquecido, sem alegação de máquina dedicada.",
        "", "## Tempo de parede", "",
        "| Caminho | Rodadas (s) | Mediana (s) |", "| --- | --- | ---: |",
    ]
    for rotulo, valores in tempos.items():
        linhas.append(f"| {rotulo} | {', '.join(f'{t:.3f}' for t in valores)} | {statistics.median(valores):.3f} |")
    linhas += ["", "## Igualdade exata", "", "EXCEPT ALL nos dois sentidos, em todas as colunas, após cada rodada.", "",
               "| Tabela | Linhas | Diferenças |", "| --- | ---: | ---: |"]
    linhas += [f"| {tabela} | {quantidade} | {diferencas} |" for tabela, quantidade, diferencas in resultados]
    linhas += [
        "", "## Código, testes e linhagem", "",
        "| Critério | SQL direto | dbt |", "| --- | --- | --- |",
        f"| Linhas SQL não vazias/comentários | {linhas_sql} | {linhas_dbt} |",
        f"| Testes declarativos executados por dbt build | não há executor declarativo | {len(testes)} |",
        f"| Linhagem automática | não há manifest | {len(modelos)} modelos, {arestas} dependências diretas e sources em manifest.json |",
        "", "O contador SQL exclui comentários de linha e linhas vazias; não conta o runner Python, YAML ou testes SQL externos.",
        "A expansão do catálogo dinâmico é contada uma vez com um item nos dois caminhos. A medição não usa linhas de código como medida isolada de manutenção.",
        "A opção nula tem restrições PK/unique no SQL e a suíte pytest existente; não equivale a ausência de validação.",
        "", "## Como reproduzir", "", "```bash",
        "# DATABASE_URL deve apontar para banco de teste vazio; DIRETORIO_DADOS guarda o download:",
        "uv sync --group transformacao",
        "uv run --group transformacao python transformacao/scripts/comparar.py --confirmar-banco-de-teste --preparar-recorte --repeticoes 3 --saida docs/evidencias/issue-48/comparacao.md",
        "```", "",
        "Para repetir em banco já preparado, omita --preparar-recorte e aponte DIRETORIO_DADOS para o subdiretório _benchmark48.",
        "O comando recria gold e gold_dbt. Nunca apontar para produção ou para um banco com slot do CDC compartilhado (issue #60).",
        "Os resultados complementam o ADR 0005 integrado na main e não repetem a comparação com SQLMesh.",
    ]
    return "\n".join(linhas) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirmar-banco-de-teste", action="store_true", required=True)
    parser.add_argument("--repeticoes", type=int, default=3)
    parser.add_argument("--preparar-recorte", action="store_true", help="baixar o Censo e carregar SP/Centro-Oeste num banco vazio")
    parser.add_argument("--saida", type=Path, required=True)
    argumentos = parser.parse_args()
    if argumentos.repeticoes < 1:
        parser.error("--repeticoes deve ser pelo menos 1")
    configuracao = carregar_configuracao()
    if argumentos.preparar_recorte:
        configuracao = preparar_recorte(configuracao)
    texto = medir(argumentos.repeticoes, configuracao)
    argumentos.saida.parent.mkdir(parents=True, exist_ok=True)
    argumentos.saida.write_text(texto, encoding="utf-8")
    print(texto)
