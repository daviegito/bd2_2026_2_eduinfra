"""O painel lê só a Gold de consumo, e o JavaScript só escolhe linhas e escreve as frases."""

import json
import re
import shutil
import subprocess
from types import SimpleNamespace

import duckdb
import pytest

from eduinfra import cli
from eduinfra.consumo import painel


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    conexao = duckdb.connect()
    conexao.execute("attach ':memory:' as pg; create schema pg.gold")
    conexao.execute("""create table pg.gold.dim_item as
        select * from (values ('biblioteca', 'Biblioteca', 'pedagogico'), ('internet', 'Acesso à internet', 'tecnologia'))
        t(item_codigo, descricao, categoria)""")
    conexao.execute("""create table pg.gold.escola_desempenho_infra as select
        i as co_entidade, 2024 as ano, date '2024-11-03' as data_referencia_infraestrutura,
        case when i = 1 then 'ESCOLA SIGILOSA' when i <= 11 then 'ALTA ' || i else 'BAIXA ' || i end as no_entidade,
        'Brasília' as no_municipio, 'DF' as sg_uf, 'estadual' as rede,
        case when i = 1 then 'intermediario' when i <= 11 then 'alto' else 'baixo' end as faixa_desempenho,
        (700 - i)::decimal(6,1) as media_geral,
        case when i <= 11 then ['biblioteca'] else []::varchar[] end as itens_existentes
        from range(1, 22) t(i)""")
    conexao.execute("""create table pg.gold.cobertura_enem as select 2024 as ano, 21 as n_codigos_enem,
        20 as n_identificadas_no_censo, 1 as n_sem_juncao, 1 as n_sem_juncao_poucos_participantes, 95.2::decimal(5,1) as pct_identificadas""")
    conexao.execute("""create table pg.gold.desempenho_por_recorte as select * from (values
        (2024, 'BR', 'todas', 'media_geral', 20, 20, 510.5::decimal(6,1), 5.9::decimal(6,1)),
        (2024, 'DF', 'estadual', 'media_geral', 4, 4, null, null))
        t(ano, uf, rede, area, n_escolas, n_escolas_com_nota, media, desvio_padrao)""")
    conexao.execute("""create table pg.gold.comparacao_com_sem_item as select * from (values
        (2024, 'BR', 'todas', 'biblioteca', 'media_geral', 552.5::decimal(6,1), 12, 452.5::decimal(6,1), 11, 100.0::decimal(6,1)),
        (2024, 'BR', 'todas', 'internet', 'media_geral', 500.0::decimal(6,1), 15, null, 3, null),
        (2024, 'BR', 'todas', 'lab_ciencias', 'media_geral', null, 0, null, 0, null))
        t(ano, uf, rede, item_codigo, area, media_com_item, n_com_item, media_sem_item, n_sem_item, diferenca)""")
    conexao.execute("""create table pg.gold.perfil_alto_baixo_desempenho as select * from (values
        (2024, 'todas', 'media_geral', 'item', 'biblioteca', 'Biblioteca', 10, 10, 72.5::decimal(5,1), 10, 10, 30.0::decimal(5,1), 42.5::decimal(5,1), false, 1),
        (2024, 'todas', 'media_geral', 'localizacao', 'urbana', 'Localização urbana', 10, 10, 90.0, 10, 10, 60.0, 30.0, false, 2),
        (2024, 'todas', 'media_geral', 'uf', 'DF', 'Escola em DF', 10, 10, 50.0, 10, 10, 50.0, 0.0, false, 3),
        (2024, 'todas', 'media_geral', 'item', 'internet', 'Acesso à internet', 10, 2, null, 10, 1, null, null, true, 4),
        (2024, 'todas', 'media_mt', 'item', 'biblioteca', 'Biblioteca', 10, 10, 20.0, 10, 10, 60.0, -40.0, false, 1))
        t(ano, rede, area, tipo, caracteristica, descricao, n_alto, n_alto_conhecido, pct_alto, n_baixo, n_baixo_conhecido,
          pct_baixo, diferenca_pp, suprimido, ordem)""")
    conexao.execute("""create table pg.gold.infra_por_uf as select * from (values
        (2024, 'BR', 'todas', 'biblioteca', 'existente', 15, 71.4::decimal(5,1)),
        (2024, 'BR', 'todas', 'biblioteca', 'inexistente', 6, 28.6::decimal(5,1)),
        (2024, 'DF', 'todas', 'biblioteca', 'existente', 10, 100.0::decimal(5,1)),
        (2024, 'NI', 'todas', 'biblioteca', 'existente', 5, 100.0::decimal(5,1)))
        t(ano, uf, rede, item_codigo, situacao, n_escolas, pct_das_escolas)""")
    configuracao = SimpleNamespace(dsn="fixture", ano_enem=2024)
    monkeypatch.setattr(painel, "conectar", lambda config: conexao)
    monkeypatch.setattr(painel, "carregar_configuracao", lambda: configuracao)
    monkeypatch.setattr(cli, "carregar_configuracao", lambda: configuracao)
    monkeypatch.setattr(painel, "get_plotlyjs", lambda: "/* biblioteca de gráficos */")
    monkeypatch.chdir(tmp_path)
    return conexao, tmp_path / painel.DESTINO


def exportado(destino):
    html = destino.read_text(encoding="utf-8")
    return html, json.loads(re.search(r'id="dados-painel">(.*?)</script>', html, re.S).group(1))


def executar_js(chamada, dados, filtro):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node necessário apenas para conferir o JavaScript do painel")
    programa = painel.CALCULOS + f"""
        const entrada = JSON.parse(require('fs').readFileSync(0, 'utf8'));
        console.log(JSON.stringify(EduInfra.{chamada}(entrada.dados, entrada.filtro)));
    """
    resultado = subprocess.run([node, "-e", programa], input=json.dumps({"dados": dados, "filtro": filtro}),
                               text=True, capture_output=True, check=True, encoding="utf-8")
    return json.loads(resultado.stdout)


def test_cli_exporta_so_tabelas_agregadas_da_gold(ambiente):
    _, destino = ambiente
    assert cli.main(["gerar-painel"]) == 0
    html, dados = exportado(destino)
    assert dados["cobertura"]["n_identificadas_no_censo"] == 20
    assert dados["ufs"] == ["DF"]
    assert {c["item_codigo"] for c in dados["comparacoes"]} == {"biblioteca", "internet"}
    assert "ESCOLA SIGILOSA" not in html and "ALTA 2" in html
    assert "enem" not in dados
    assert "95,2%" in html and 'src="https://' not in html


def test_numero_decimal_da_gold_vira_numero_no_json(ambiente):
    conexao, _ = ambiente
    dados = painel.ler_dados(conexao, 2024)
    biblioteca = next(c for c in dados["comparacoes"] if c["item_codigo"] == "biblioteca")
    assert biblioteca["diferenca"] == 100.0 and isinstance(biblioteca["diferenca"], float)


@pytest.mark.parametrize("defeito", ["tabela_ausente", "duas_referencias", "cobertura_ausente"])
def test_gold_incompleta_falha_antes_de_exportar(ambiente, defeito):
    conexao, destino = ambiente
    if defeito == "tabela_ausente":
        conexao.execute("drop table pg.gold.perfil_alto_baixo_desempenho")
    elif defeito == "duas_referencias":
        conexao.execute("""insert into pg.gold.escola_desempenho_infra (co_entidade, ano, data_referencia_infraestrutura, no_entidade)
            values (99, 2024, date '2024-10-01', 'OUTRA')""")
    else:
        conexao.execute("delete from pg.gold.cobertura_enem")
    with pytest.raises(RuntimeError):
        cli.main(["gerar-painel"])
    assert not destino.exists()


def test_conclusoes_usam_o_perfil_e_trazem_o_que_nao_quer_dizer(ambiente):
    conexao, _ = ambiente
    dados = painel.ler_dados(conexao, 2024)
    conclusoes = executar_js("conclusoes", dados, {})
    assert [c.get("rotulo") for c in conclusoes] == ["Onde olhar primeiro", None, None, None]
    assert conclusoes[0]["texto"] == ("No Brasil, em todas as redes, os itens que mais separam as escolas de maior e de menor "
                                      "desempenho são biblioteca.")
    assert conclusoes[1]["texto"] == ("No Brasil, em todas as redes, 72,5% das escolas de maior desempenho têm biblioteca, "
                                      "contra 30,0% das de menor desempenho.")
    assert "Não quer dizer" in conclusoes[1]["ressalva"]
    assert conclusoes[2]["texto"] == ("No Brasil, em todas as redes, 90,0% das escolas de maior desempenho ficam em área urbana, "
                                      "contra 60,0% das de menor desempenho.")
    assert not any("acesso à internet" in c["texto"] and "%" in c["texto"] for c in conclusoes)
    assert not any("ficam em DF" in c["texto"] for c in conclusoes)
    assert conclusoes[3]["texto"].startswith("No Brasil, em todas as redes, a maior diferença de nota aparece em biblioteca")
    assert "100,0 pontos (12 escolas com, 11 sem)" in conclusoes[3]["texto"]


def test_recorte_sem_escolas_suficientes_diz_isso_em_vez_de_mostrar_numero(ambiente):
    conexao, _ = ambiente
    dados = painel.ler_dados(conexao, 2024)
    conclusoes = executar_js("conclusoes", dados, {"uf": "DF", "rede": "estadual"})
    assert len(conclusoes) == 1 and "pelo menos 10 escolas" in conclusoes[0]["texto"]


def test_recorte_escolhe_linhas_sem_recalcular_media(ambiente):
    conexao, _ = ambiente
    dados = painel.ler_dados(conexao, 2024)
    brasil = executar_js("recorte", dados, {})
    assert brasil["geral"]["media"] == 510.5 and brasil["censo"] == 21
    assert [c["item_codigo"] for c in brasil["comparacoes"]][0] == "biblioteca"
    distrito = executar_js("recorte", dados, {"uf": "DF", "rede": "estadual"})
    assert distrito["geral"]["media"] is None and distrito["comparacoes"] == []


def test_json_nao_permite_que_nome_da_fonte_execute_script(ambiente):
    conexao, _ = ambiente
    ataque = '</script><script>alert("xss")</script>'
    conexao.execute("update pg.gold.dim_item set descricao = ? where item_codigo = 'biblioteca'", [ataque])
    html = painel.renderizar_html(painel.ler_dados(conexao, 2024))
    assert ataque not in html
    serializado = re.search(r'id="dados-painel">(.*?)</script>', html, re.S).group(1)
    assert ataque in [item[1] for item in json.loads(serializado)["itens"]]


def test_perfil_das_faixas_e_nacional_mesmo_com_estado_no_filtro(ambiente):
    conexao, _ = ambiente
    conexao.execute("""insert into pg.gold.desempenho_por_recorte values
        (2024, 'DF', 'todas', 'media_geral', 12, 12, 505.0, 3.0)""")
    dados = painel.ler_dados(conexao, 2024)
    conclusoes = executar_js("conclusoes", dados, {"uf": "DF"})
    assert conclusoes[1]["texto"].startswith("No Brasil, em todas as redes, 72,5%")


def test_perfil_segue_a_area_da_nota_escolhida(ambiente):
    conexao, _ = ambiente
    dados = painel.ler_dados(conexao, 2024)
    matematica = executar_js("recorte", dados, {"area": "media_mt"})
    assert [(p["caracteristica"], p["diferenca_pp"]) for p in matematica["perfil"]] == [("biblioteca", -40.0)]
    conclusoes = executar_js("conclusoes", dados, {"area": "media_mt"})
    assert not any("têm biblioteca" in c["texto"] for c in conclusoes)


def test_lista_traz_as_faixas_em_ordem_e_suprime_faixa_pequena(ambiente):
    conexao, _ = ambiente
    dados = painel.ler_dados(conexao, 2024)
    escolas = executar_js("escolas", dados, {})
    assert [e["nome"] for e in escolas["alto"]][:2] == ["ALTA 2", "ALTA 3"]
    assert [e["nome"] for e in escolas["baixo"]][:2] == ["BAIXA 21", "BAIXA 20"]
    assert "ESCOLA SIGILOSA" not in {e["nome"] for e in escolas["alto"] + escolas["baixo"]}
    assert executar_js("escolas", dados, {"rede": "federal"}) == {"alto": [], "baixo": []}
    conexao.execute("delete from pg.gold.escola_desempenho_infra where no_entidade = 'BAIXA 21'")
    menos_de_dez = executar_js("escolas", painel.ler_dados(conexao, 2024), {})
    assert menos_de_dez["baixo"] == [] and len(menos_de_dez["alto"]) == 10


def test_csv_abre_na_planilha_em_portugues_sem_executar_formula(ambiente):
    conexao, _ = ambiente
    conexao.execute("""update pg.gold.escola_desempenho_infra set no_entidade = '=HYPERLINK("x")' where co_entidade = 2""")
    arquivo = executar_js("csv", painel.ler_dados(conexao, 2024), {})
    linhas = arquivo.split("\r\n")
    assert linhas[0] == '\ufeff"escola";"municipio";"uf";"rede";"faixa";"media_geral";"itens_existentes"'
    assert len(linhas) == 21
    assert linhas[1] == '"\'=HYPERLINK(""x"")";"Brasília";"DF";"estadual";"maior desempenho";"698,0";"Biblioteca"'
