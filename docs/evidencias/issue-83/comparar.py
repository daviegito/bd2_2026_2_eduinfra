"""Compara MetricFlow e views SQL em PostgreSQL descartável, sem acessar a origem."""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import shutil
import copy

import yaml
import time
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

RAIZ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RAIZ / "src"))
from eduinfra import semantica

PASTA = Path(__file__).resolve().parent
SITUACOES = ("existente", "inexistente", "em_obra", "inutilizavel", "nao_informado")


def executar(comando, ambiente, pasta=None):
    inicio = time.perf_counter()
    resultado = subprocess.run(comando, cwd=pasta or PASTA / "metricflow", env=ambiente,
                               capture_output=True, text=True, check=True)
    return resultado.stdout, (time.perf_counter() - inicio) * 1000


def medir(conexao, consulta, parametros, repeticoes):
    conexao.execute(consulta, parametros).fetchall()  # aquecimento fora das amostras
    tempos = []
    for _ in range(repeticoes):
        inicio = time.perf_counter()
        cursor = conexao.execute(consulta, parametros)
        linhas = cursor.fetchall()
        tempos.append((time.perf_counter() - inicio) * 1000)
    colunas = [c.name for c in cursor.description]
    return [dict(zip(colunas, linha)) for linha in linhas], tempos


def casos():
    predicado = "{{ Dimension('escola__ano') }} = 2024 and {{ Dimension('escola__area') }} = 'media_geral' and {{ Dimension('escola__item') }} = 'biblioteca'"
    principais = [
        ("media_enem_escolas", {"ano": 2024}, ["media_enem_escolas", "n_validas", "desvio_padrao"], predicado, None),
        ("diferenca_media_com_sem_item", {"ano": 2024, "item": "biblioteca"},
         ["diferenca_media_com_sem_item", "n_existente", "n_inexistente", "soma_existente", "soma_inexistente"], predicado, "escola__rede"),
        ("pct_escolas_por_situacao_do_item", {"ano": 2024, "item": "biblioteca"},
         ["n_censo"] + ["censo_" + s for s in SITUACOES] + ["pct_" + s for s in SITUACOES], predicado, None),
        ("cobertura_enem_censo", {"ano": 2024},
         ["cobertura_enem_censo", "escolas_enem", "encontradas", "nao_encontradas", "mascaradas"],
         "{{ Dimension('cobertura__ano') }} = 2024", None),
    ]
    adicionais = [
        ("media_enem_escolas", {"ano": 2024, "uf": "DF", "area": "media_mt"},
         ["media_enem_escolas", "n_validas", "desvio_padrao"],
         predicado.replace("media_geral", "media_mt") + " and {{ Dimension('escola__uf') }} = 'DF'", None),
        ("media_enem_escolas", {"ano": 2024, "uf": "AC"},
         ["media_enem_escolas", "n_validas", "desvio_padrao"],
         predicado + " and {{ Dimension('escola__uf') }} = 'AC'", None),
    ]
    return principais + adicionais


def normalizar(nome, linhas):
    if nome == "media_enem_escolas":
        r = linhas[0]
        return [] if r["media_enem_escolas"] is None else [{"media": r["media_enem_escolas"], "n_escolas": r["n_validas"], "desvio_padrao": r["desvio_padrao"]}]
    if nome == "diferenca_media_com_sem_item":
        return sorted([{"rede": r["escola__rede"], "n_com": r["n_existente"], "n_sem": r["n_inexistente"],
                        "media_com": r["soma_existente"] / r["n_existente"],
                        "media_sem": r["soma_inexistente"] / r["n_inexistente"],
                        "diferenca": r[nome]} for r in linhas if r[nome] is not None], key=lambda r: r["rede"])
    if nome == "pct_escolas_por_situacao_do_item":
        r = linhas[0]
        return sorted([{"situacao": s, "n_escolas": r["censo_" + s], "percentual": r["pct_" + s]}
                       for s in SITUACOES if r["censo_" + s]], key=lambda r: r["situacao"])
    return [{"escolas_enem": r["escolas_enem"], "encontradas": r["encontradas"], "nao_encontradas": r["nao_encontradas"],
             "mascaradas": r["mascaradas"], "percentual": r[nome]} for r in linhas]


def conferir(a, b, caminho="resultado"):
    if isinstance(a, list):
        assert len(a) == len(b), (caminho, a, b)
        for i, (x, y) in enumerate(zip(a, b)):
            conferir(x, y, f"{caminho}[{i}]")
    elif isinstance(a, dict):
        assert a.keys() == b.keys(), (caminho, a, b)
        for chave in a:
            conferir(a[chave], b[chave], f"{caminho}.{chave}")
    elif isinstance(a, (float, int)) and not isinstance(a, bool):
        assert abs(float(a) - float(b)) < 1e-7, (caminho, a, b)
    else:
        assert a == b, (caminho, a, b)


def medir_imagens(imagem_base, bins):
    """Mesmo pai e mesmo destino: mede apenas o delta dos ambientes de bancada."""
    def inspecionar(nome):
        return json.loads(subprocess.check_output(["docker", "image", "inspect", nome], text=True))[0]

    base = inspecionar(imagem_base)
    referencia = "eduinfra83-dependencias-pai:local"
    subprocess.run(["docker", "tag", base["Id"], referencia], check=True)
    imagens = {}
    for nome, binario in bins.items():
        codigo = "import sysconfig; print(sysconfig.get_paths()['purelib'])"
        site = Path(subprocess.check_output([str(binario / "python"), "-c", codigo], text=True).strip())
        contexto = Path(tempfile.mkdtemp(prefix="eduinfra83-imagem-"))
        shutil.copytree(site, contexto / "site-packages", symlinks=True)
        (contexto / "Dockerfile").write_text(
            "FROM " + referencia + "\nCOPY site-packages /opt/bancada/site-packages\n"
        )
        tag = "eduinfra83-dependencias-" + nome + ":local"
        build = subprocess.run(["docker", "build", "--network", "none", "-t", tag, str(contexto)],
                               capture_output=True, text=True)
        if build.returncode:
            raise RuntimeError("Falha na imagem de bancada: " + build.stderr)
        imagem = inspecionar(tag)
        imagens[nome] = {"id": imagem["Id"], "bytes_imagem": imagem["Size"],
                        "bytes_adicionados_ao_pai": imagem["Size"] - base["Size"]}
    return {"pai": base["Id"], "bytes_pai": base["Size"], "imagens": imagens,
            "delta_metricflow_menos_dbt_bytes": imagens["metricflow"]["bytes_imagem"] - imagens["dbt"]["bytes_imagem"],
            "metodo": "Imagens locais isoladas, mesmo pai, COPY dos ambientes já instalados para /opt/bancada. Nenhuma instalação ou alteração da imagem de produção. Tamanho Docker local não comprimido; não mede a imagem final de produção."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-metricflow", required=True, type=Path, help="pasta bin do venv aprovado de bancada")
    parser.add_argument("--bin-dbt-base", required=True, type=Path, help="pasta bin do venv apenas dbt/postgres, para custo incremental")
    parser.add_argument("--imagem-base", help="imagem local existente para medir camadas isoladas sem rede ou instalação")
    parser.add_argument("--preparar", action="store_true", help="cria apenas a fixture sintética, se o banco estiver vazio")
    parser.add_argument("--repeticoes", type=int, default=5)
    parser.add_argument("--saida", type=Path, required=True)
    opcoes = parser.parse_args()
    if opcoes.repeticoes < 3:
        parser.error("use ao menos três repetições")
    dsn = os.environ["SEMANTICA_BANCADA_DSN"]
    configuracao = conninfo_to_dict(dsn)
    if configuracao.get("dbname") != "eduinfra83" or configuracao.get("host") not in ("127.0.0.1", "localhost"):
        parser.error("a medição exige o banco local descartável eduinfra83")
    ambiente = {**os.environ, "PGHOST": configuracao["host"], "PGPORT": configuracao.get("port", "5432"),
                "POSTGRES_USER": configuracao["user"], "POSTGRES_PASSWORD": configuracao["password"],
                "POSTGRES_DB": configuracao["dbname"], "DBT_PROFILES_DIR": str(PASTA / "metricflow")}
    with psycopg.connect(dsn, autocommit=True) as conexao:
        if opcoes.preparar and conexao.execute("select to_regclass('gold.dim_escola')").fetchone()[0] is None:
            conexao.execute((PASTA / "fixture.sql").read_text())
        escolas = conexao.execute("select count(*) from gold.dim_escola").fetchone()[0]
        atualizacao_b = []
        for _ in range(opcoes.repeticoes):
            inicio = time.perf_counter()
            semantica.publicar(dsn)
            atualizacao_b.append((time.perf_counter() - inicio) * 1000)
        atualizacao_a = []
        for _ in range(opcoes.repeticoes):
            _, tempo = executar([str(opcoes.bin_metricflow / "dbt"), "run"], ambiente)
            atualizacao_a.append(tempo)
        def tamanho_site(binario):
            codigo = "import json,sysconfig,pathlib; p=pathlib.Path(sysconfig.get_paths()['purelib']); print(json.dumps({'bytes_aparentes':sum(f.stat().st_size for f in p.rglob('*') if f.is_file()),'pacotes':len(list(p.glob('*.dist-info'))),'distribuicoes':sorted(x.name for x in p.glob('*.dist-info'))}))"
            return json.loads(subprocess.check_output([str(binario / "python"), "-c", codigo], text=True))
        def linhas(arquivos):
            return {str(a.relative_to(RAIZ)): sum(bool(l.strip()) and not l.lstrip().startswith(("#", "--")) for l in a.read_text().splitlines()) for a in arquivos}
        resultados = {"dependencias": {"dbt_base": tamanho_site(opcoes.bin_dbt_base), "metricflow": tamanho_site(opcoes.bin_metricflow)},
                      "linhas_nao_vazias": {
                          "a_definicoes": linhas(sorted((PASTA / "metricflow" / "models").glob("*"))),
                          "b_catalogo": linhas([semantica.CATALOGO / (n + ".yml") for n in semantica.carregar_definicoes()]),
                          "bases_e_validacao_sql": linhas(sorted((semantica.CATALOGO / "sql").glob("*.sql"))),
                          "publicador_e_interface": linhas([RAIZ / "src/eduinfra/semantica.py"]),
                      }, "escolas_sinteticas": escolas, "repeticoes": opcoes.repeticoes,
                      "postgres": conexao.execute("show server_version").fetchone()[0],
                      "atualizacao_ms": {"metricflow_dbt_cli": atualizacao_a, "views_publicador_python": atualizacao_b},
                      "consultas": {}}
        pasta_sql = opcoes.saida.parent / "sql_compilado"
        pasta_sql.mkdir(parents=True, exist_ok=True)
        for nome, filtros, metricas, onde, grupo in casos():
            chave = nome + ("_" + filtros["uf"] if "uf" in filtros else "")
            comando = [str(opcoes.bin_metricflow / "mf"), "query", "--metrics", ",".join(metricas), "--where", onde, "--explain"]
            if grupo:
                comando.extend(["--group-by", grupo])
            saida, tempo_cli = executar(comando, ambiente)
            limpo = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", saida)
            inicio = re.search(r"(?m)^WITH |^SELECT ", limpo)
            if not inicio:
                raise RuntimeError(f"MetricFlow não retornou SQL para {nome}: {limpo}")
            consulta_a = limpo[inicio.start():].strip()
            (pasta_sql / f"{chave}.sql").write_text(consulta_a + "\n")
            dados_a, tempos_a = medir(conexao, consulta_a, None, opcoes.repeticoes)
            dados_b, tempos_b = medir(conexao, f"select metricas.{nome}(%s::jsonb) as dados", (json.dumps(filtros),), opcoes.repeticoes)
            a = normalizar(nome, dados_a)
            b = dados_b[0]["dados"]
            conferir(b, a)
            conferir(b, semantica.consultar(dsn, nome, filtros)["dados"])
            resultados["consultas"][chave] = {"iguais": True, "tolerancia_absoluta": 1e-7,
                "compilacao_metricflow_cli_ms": tempo_cli, "metricflow_sql_ms": tempos_a, "views_funcao_sql_ms": tempos_b,
                "mediana_metricflow_sql_ms": statistics.median(tempos_a), "mediana_views_ms": statistics.median(tempos_b), "resultado": b}
        # A mudança afeta somente esta métrica: o mínimo das demais fica intacto.
        pasta_mudanca = Path(tempfile.mkdtemp(prefix="eduinfra83-mudanca-"))
        copia = pasta_mudanca / "metricflow"
        shutil.copytree(PASTA / "metricflow", copia, ignore=shutil.ignore_patterns("target", "logs", ".user.yml"))
        arquivo = copia / "models/metricas.yml"
        definicoes_a = yaml.safe_load(arquivo.read_text())
        metrica_a = next(m for m in definicoes_a["metrics"] if m["name"] == "media_enem_escolas")
        metrica_a["type_params"]["expr"] = metrica_a["type_params"]["expr"].replace(">= 10", ">= 5000")
        arquivo.write_text(yaml.safe_dump(definicoes_a, sort_keys=False))
        ambiente_mudanca = {**ambiente, "DBT_PROFILES_DIR": str(copia)}
        executar([str(opcoes.bin_metricflow / "dbt"), "parse"], ambiente_mudanca, copia)
        nome, filtros, metricas, onde, _ = casos()[0]
        saida, _ = executar([str(opcoes.bin_metricflow / "mf"), "query", "--metrics", "media_enem_escolas,n_validas,desvio_padrao", "--where", onde, "--explain"], ambiente_mudanca, copia)
        limpo = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", saida)
        consulta = limpo[re.search(r"(?m)^WITH |^SELECT ", limpo).start():].strip()
        a, _ = medir(conexao, consulta, None, opcoes.repeticoes)
        definicao_b = copy.deepcopy(semantica.carregar_definicoes()[nome])
        definicao_b["supressao"]["minimo"] = 5000
        consulta = semantica.corpo_consulta(definicao_b).replace("p_filtros", "%s::jsonb")
        b = conexao.execute(consulta, (json.dumps(filtros),)).fetchone()[0]
        assert normalizar(nome, a) == b == []
        resultados["mudanca_de_definicao"] = {"cenario": "mínimo da média nacional de 10 para 5000 escolas",
            "a_campos_alterados": 1, "a_arquivos_alterados": 1, "b_campos_alterados": 1, "b_arquivos_alterados": 1,
            "ambos_suprimiram_grupo": True, "consumidor_que_recalcula_fora_da_interface_pode_divergir": True,
            "copia_alterada": "temporária; o catálogo publicado e os fontes medidos foram preservados"}
        if opcoes.imagem_base:
            resultados["custo_imagem_controlada"] = medir_imagens(opcoes.imagem_base, {"dbt": opcoes.bin_dbt_base, "metricflow": opcoes.bin_metricflow})
        opcoes.saida.parent.mkdir(parents=True, exist_ok=True)
        opcoes.saida.write_text(json.dumps(resultados, indent=2, ensure_ascii=False, default=str) + "\n")
        print(json.dumps(resultados, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
