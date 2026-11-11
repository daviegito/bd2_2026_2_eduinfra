"""Valida o retrato real e os mesmos cálculos JavaScript usados pelos filtros."""

import json
import re
import shutil
import subprocess
from types import SimpleNamespace

import duckdb
import pytest

from eduinfra import cli, painel


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    conexao = duckdb.connect()
    conexao.execute("attach ':memory:' as pg; create schema pg.gold; create schema pg.entrada_transformacao")
    conexao.execute("""create table pg.gold.dim_item as
        select 'biblioteca' as item_codigo, 'Biblioteca' as descricao
        union all select 'internet', 'Internet'""")
    conexao.execute("""create table pg.gold.dim_municipio as
        select 1 as co_municipio, 'Brasília' as no_municipio, 'DF' as sg_uf
        union all select 2, 'São Paulo', 'SP'""")
    conexao.execute("""create table pg.gold.dim_escola as
        select i as co_entidade, case when i <= 10 then 1 else 2 end as co_municipio,
               2 as tp_dependencia, 'urbana' as localizacao, 2024 as ano_censo_referencia
        from range(1,21) r(i)""")
    conexao.execute("""create table pg.gold.dim_escola_infraestrutura as
        select i as co_entidade, i*10 as escola_infra_sk,
               '{"biblioteca":"existente","internet":"inexistente"}'::json as itens,
               timestamptz '2024-01-01 00:00:00+00' as valid_from, timestamptz '2025-01-01 00:00:00+00' as valid_to
        from range(1,21) r(i)
        union all
        select i, i*10+1, '{"biblioteca":"inexistente","internet":"existente"}'::json,
               timestamptz '2025-01-01 00:00:00+00', timestamptz '9999-12-31 00:00:00+00'
        from range(1,21) r(i)""")
    conexao.execute("""create table pg.gold.fato_desempenho_escola_ano as
        select i as co_entidade, 2024 as ano, i*10 as escola_infra_sk,
               500.0+i as media_geral, 400.0+i as media_cn, 450.0+i as media_ch,
               490.0+i as media_lc, 510.0+i as media_mt, 600.0+i as media_redacao,
               10 as n_participantes, date '2024-11-03' as data_referencia_infraestrutura
        from range(1,21) r(i)""")
    conexao.execute("""create table pg.entrada_transformacao.enem_escola_ano as
        select co_entidade, ano, false as poucos_participantes from pg.gold.fato_desempenho_escola_ano
        union all select 60000001, 2024, true""")
    configuracao = SimpleNamespace(dsn="fixture", ano_enem=2024)
    monkeypatch.setattr(painel, "conectar", lambda config: conexao)
    monkeypatch.setattr(painel, "carregar_configuracao", lambda: configuracao)
    monkeypatch.setattr(cli, "carregar_configuracao", lambda: configuracao)
    monkeypatch.setattr(painel, "get_plotlyjs", lambda: "/* biblioteca de gráficos */")
    monkeypatch.chdir(tmp_path)
    return conexao, tmp_path / "docs" / "painel.html"


def calcular(dados, **filtros):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node necessário apenas para conferir os cálculos JavaScript do painel")
    programa = painel.CALCULOS + """
        const entrada = JSON.parse(require('fs').readFileSync(0, 'utf8'));
        console.log(JSON.stringify(EduInfra.calcular(entrada.dados, entrada.filtros)));
    """
    resultado = subprocess.run([node, "-e", programa], input=json.dumps({"dados": dados, "filtros": filtros}),
                               text=True, capture_output=True, check=True)
    return json.loads(resultado.stdout)


def test_cli_exporta_dados_e_calculos_reais_sem_duplicar_perfis_ou_anos(ambiente):
    conexao, destino = ambiente
    conexao.execute("""insert into pg.gold.fato_desempenho_escola_ano
        select co_entidade,2025,escola_infra_sk+1,900,900,900,900,900,900,10,date '2025-11-03'
        from pg.gold.fato_desempenho_escola_ano""")
    assert cli.main(["gerar-painel"]) == 0
    html = destino.read_text()
    dados = json.loads(re.search(r'id="dados-painel">(.*?)</script>', html, re.S).group(1))
    assert dados["cobertura"] == {"entrada": 21, "identificadas": 20, "sem_juncao": 1, "mascaradas": 1}
    assert len(dados["escolas"]) == len(dados["enem"]) == 20
    assert dados["escolas"][0][3] == "12"
    assert dados["enem"][0][1] == "12"
    assert [registro[2][0] for registro in dados["enem"]] == [501.0 + i for i in range(20)]
    assert all(escola[3] == "12" for escola in dados["escolas"])
    assert "painel-calculos" in html and "Perguntas que os dados respondem" in html
    assert 'src="https://' not in html


@pytest.mark.parametrize("estado,esperado", [
    ("existente", 1), ("inexistente", 2), ("em_obra", 3), ("inutilizavel", 4),
    ("nao_informado", 0), (None, 0),
])
def test_estados_distintos_incluem_desconhecido_sem_inventar_ausencia(ambiente, estado, esperado):
    conexao, _ = ambiente
    itens = {} if estado is None else {"biblioteca": estado}
    conexao.execute("update pg.gold.dim_escola_infraestrutura set itens = ?", [json.dumps(itens)])
    dados = painel.ler_dados(conexao, 2024)
    assert all(escola[3][0] == str(esperado) for escola in dados["escolas"])
    assert all(registro[1][0] == str(esperado) for registro in dados["enem"])


def test_perfil_ausente_no_fato_nao_usa_perfil_atual_ou_o_do_censo(ambiente):
    conexao, _ = ambiente
    conexao.execute("update pg.gold.fato_desempenho_escola_ano set escola_infra_sk = null")
    dados = painel.ler_dados(conexao, 2024)
    assert all(escola[4] and escola[3] == "12" for escola in dados["escolas"])
    assert all(not registro[4] and registro[1] == "00" for registro in dados["enem"])


def test_filtros_geograficos_rede_e_localizacao_recalculam_n_e_media(ambiente):
    conexao, _ = ambiente
    conexao.execute("update pg.gold.dim_escola set localizacao = 'rural' where co_entidade <= 10")
    dados = painel.ler_dados(conexao, 2024)
    df = calcular(dados, uf="DF", rede="publica", local="2")
    assert (df["censo"], df["enem"], df["nota"]["n"], df["nota"]["media"]) == (10, 10, 10, 505.5)
    municipio_sp = next(i for i, municipio in enumerate(dados["municipios"]) if municipio[2] == "SP")
    sp = calcular(dados, municipio=str(municipio_sp), rede="2", local="1")
    assert sp["nota"]["media"] == 515.5
    vazio = calcular(dados, uf="DF", municipio=str(municipio_sp))
    assert vazio["censo"] == vazio["enem"] == 0
    assert vazio["nota"]["media"] is None and vazio["nota"]["desvio"] is None
    assert dados["cobertura"]["sem_juncao"] == 1


def test_infraestrutura_inclui_escolas_sem_enem_e_media_nao_e_ponderada(ambiente):
    conexao, _ = ambiente
    conexao.execute("""insert into pg.gold.dim_escola values (21,1,3,'rural',2024)""")
    conexao.execute("update pg.gold.fato_desempenho_escola_ano set n_participantes = 10000 where co_entidade = 20")
    dados = painel.ler_dados(conexao, 2024)
    resultado = calcular(dados)
    assert (resultado["censo"], resultado["enem"]) == (21, 20)
    assert resultado["infraestrutura"][0] == [1, 20, 0, 0, 0]
    assert resultado["nota"]["media"] == 510.5
    assert resultado["participantes"] == 10190


def test_nulo_nao_e_zero_cada_area_tem_sua_amostra_e_grupos_pequenos_sao_suprimidos(ambiente):
    conexao, _ = ambiente
    conexao.execute("update pg.gold.fato_desempenho_escola_ano set media_geral = null where co_entidade > 9")
    conexao.execute("update pg.gold.fato_desempenho_escola_ano set media_redacao = 0")
    dados = painel.ler_dados(conexao, 2024)
    geral = calcular(dados)
    assert geral["nota"] == {"n": 9, "media": None, "desvio": None}
    assert geral["escolasSemNota"] == 11
    assert all(grupo["media"] is None for grupo in geral["comparacoes"])
    redacao = calcular(dados, metrica="5")
    assert redacao["nota"] == {"n": 20, "media": 0, "desvio": 0}
    assert redacao["porArea"][0]["n"] == 9 and redacao["porArea"][1]["n"] == 20
    filtrada = calcular(dados, metrica="5", municipio="0", local="2")
    assert filtrada["nota"]["media"] is None


def test_comparacao_so_forma_par_com_duas_situacoes_validas_na_mesma_rede(ambiente):
    conexao, _ = ambiente
    conexao.execute("""update pg.gold.dim_escola_infraestrutura set itens =
        '{"biblioteca":"inexistente"}' where co_entidade > 10 and escola_infra_sk % 10 = 0""")
    dados = painel.ler_dados(conexao, 2024)
    resultado = calcular(dados)
    assert len(resultado["pares"]) == 1
    assert resultado["pares"][0]["com"]["media"] == 505.5
    assert resultado["pares"][0]["sem"]["media"] == 515.5
    assert resultado["pares"][0]["diferenca"] == -10
    assert calcular(dados, uf="DF")["pares"] == []
    # Duas médias em redes diferentes não formam uma comparação com/sem.
    dados["escolas"][10][1] = 4
    assert calcular(dados)["pares"] == []


@pytest.mark.parametrize("defeito", ["fato_ausente", "perfil_sobreposto", "referencias_distintas", "cobertura_incorreta"])
def test_fonte_inconsistente_falha_antes_de_exportar(ambiente, defeito):
    conexao, destino = ambiente
    if defeito == "fato_ausente":
        conexao.execute("drop table pg.gold.fato_desempenho_escola_ano")
    elif defeito == "perfil_sobreposto":
        conexao.execute("update pg.gold.dim_escola_infraestrutura set valid_from = timestamptz '2024-01-01 00:00:00+00'")
    elif defeito == "referencias_distintas":
        conexao.execute("update pg.gold.fato_desempenho_escola_ano set data_referencia_infraestrutura = date '2024-10-01' where co_entidade = 1")
    else:
        conexao.execute("update pg.entrada_transformacao.enem_escola_ano set co_entidade = 999 where co_entidade = 1")
    with pytest.raises(RuntimeError):
        cli.main(["gerar-painel"])
    assert not destino.exists()


def test_json_nao_permite_que_nome_da_fonte_execute_script(ambiente):
    conexao, _ = ambiente
    ataque = '</script><script>alert("xss")</script>'
    conexao.execute("update pg.gold.dim_municipio set no_municipio = ?", [ataque])
    dados = painel.ler_dados(conexao, 2024)
    html = painel.renderizar_html(dados)
    assert ataque not in html
    serializado = re.search(r'id="dados-painel">(.*?)</script>', html, re.S).group(1)
    assert json.loads(serializado)["municipios"][0][1] == ataque
