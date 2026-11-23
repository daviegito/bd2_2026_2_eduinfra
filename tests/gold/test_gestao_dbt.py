"""Tabelas de consumo da Gold construídas pelo dbt a partir de escolas com resultado conhecido."""

import json
import re
from dataclasses import replace

import psycopg
import pytest

from eduinfra.consumo import painel
from eduinfra.gold.fontes_dbt import Entradas, bloquear, preparar
from eduinfra.lake import silver_enem
from tests.experimentos.test_modelo_analitico import configuracao_de, consultar, dsn, vistoria
from tests.gold.test_fontes_dbt import gravar_consulta
from tests.lake.test_silver_enem import escrever_resultados, linha
from transformacao.scripts.executar import dbt

pytestmark = pytest.mark.integracao

PRIMEIRA = 101
# 60 escolas: cada quinto tem 12, acima do mínimo de 10 que libera a comparação.
ESCOLAS = range(PRIMEIRA, PRIMEIRA + 60)
ESCOLAS_POR_FAIXA = 12
PARTICIPANTES_POR_ESCOLA = 10
URBANA, RURAL = 1, 2


def nota_da(escola: int) -> int:
    return 400 + 5 * (escola - PRIMEIRA + 1)


def nota_de_matematica(escola: int) -> int:
    return nota_da(2 * PRIMEIRA + len(ESCOLAS) - 1 - escola)


def nota_de_redacao(escola: int) -> int:
    # Compensa a Matemática invertida: a média geral continua sendo nota_da(escola).
    return 2 * nota_da(escola) - nota_de_matematica(escola)


def tem_biblioteca(escola: int) -> bool:
    return escola >= PRIMEIRA + 30


def localizacao(escola: int) -> int:
    return RURAL if escola < PRIMEIRA + ESCOLAS_POR_FAIXA else URBANA


def semear_escolas(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conexao:
        for escola in ESCOLAS:
            conexao.execute(
                "insert into escola (co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia) "
                "values (%s, %s, 5300108, 2, %s, 2024)", (escola, f"ESCOLA {escola}", localizacao(escola)))
    for escola in ESCOLAS:
        situacao = "existente" if tem_biblioteca(escola) else "inexistente"
        vistoria(dsn, escola, "2024-05-29", {"biblioteca": situacao, "internet": "nao_informado"}, origem="censo_escolar")


def entradas_com_enem(dsn: str, pasta) -> Entradas:
    silver = pasta / "silver" / "ano_censo=2024"
    gravar_consulta(dsn, "select co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia from escola", silver / "escola.parquet")
    gravar_consulta(dsn, "select * from municipio", silver / "municipio.parquet")
    eventos = pasta / "eventos.parquet"
    gravar_consulta(dsn, "select v.id as vistoria_id, i.item_codigo, v.co_entidade, v.origem, v.ocorrido_em, v.registrado_em, i.situacao::text, i.quantidade from vistoria v join vistoria_item i on i.vistoria_id = v.id", eventos)
    linhas = []
    for escola in ESCOLAS:
        nota, matematica = str(nota_da(escola)), str(nota_de_matematica(escola))
        for _ in range(PARTICIPANTES_POR_ESCOLA):
            linhas.append(linha(len(linhas) + 1, escola, ("1", "1", "1", "1"), (nota, nota, nota, matematica),
                                "1", str(nota_de_redacao(escola))))
    configuracao = replace(configuracao_de(dsn), diretorio_dados=pasta)
    _, preparo = silver_enem.montar_silver_enem(configuracao, escrever_resultados(pasta, linhas), "fixture-gestao")
    enem = silver_enem.publicar_silver_enem(configuracao, preparo) / "desempenho_escola.parquet"
    return Entradas(silver.parent, eventos, enem)


@pytest.fixture
def gold(dsn, tmp_path, monkeypatch):
    semear_escolas(dsn)
    monkeypatch.setenv("DBT_TARGET_PATH", str(tmp_path / "target"))
    monkeypatch.setenv("DBT_LOG_PATH", str(tmp_path / "logs"))
    with bloquear(dsn) as conexao:
        preparar(conexao, entradas_com_enem(dsn, tmp_path / "dados"))
        dbt("build", dsn, True, "gold")
    return dsn


def test_comparacao_com_e_sem_o_item_responde_a_pergunta_em_uma_linha(gold):
    assert consultar(gold, """select media_com_item, n_com_item, media_sem_item, n_sem_item, diferenca
        from gold.comparacao_com_sem_item
        where uf = 'DF' and rede = 'estadual' and item_codigo = 'biblioteca' and area = 'media_geral'""") == [
        (627.5, 30, 477.5, 30, 150.0)]


def test_faixas_alta_e_baixa_sao_quintis_dentro_da_rede(gold):
    assert consultar(gold, """select faixa_desempenho, count(*), min(media_geral), max(media_geral)
        from gold.escola_desempenho_infra where rede = 'estadual' group by 1 order by 1""") == [
        ("alto", 12, 645.0, 700.0), ("baixo", 12, 405.0, 460.0), ("intermediario", 36, 465.0, 640.0)]


def test_perfil_alto_baixo_ordena_todas_as_caracteristicas_pelo_que_mais_diferencia(gold):
    perfil = consultar(gold, """select tipo, caracteristica, pct_alto, pct_baixo, diferenca_pp, ordem
        from gold.perfil_alto_baixo_desempenho where rede = 'estadual' and area = 'media_geral' and not suprimido
        order by ordem""")
    assert perfil == [
        ("conjunto_de_itens", "metade_dos_itens", 100.0, 0.0, 100.0, 1),
        ("item", "biblioteca", 100.0, 0.0, 100.0, 2),
        ("localizacao", "urbana", 100.0, 0.0, 100.0, 3),
        ("uf", "DF", 100.0, 100.0, 0.0, 4),
    ]


def test_perfil_suprime_item_sem_situacao_informada(gold):
    assert consultar(gold, """select pct_alto, pct_baixo, diferenca_pp, suprimido from gold.perfil_alto_baixo_desempenho
        where rede = 'estadual' and area = 'media_geral' and caracteristica = 'internet'""") == [(None, None, None, True)]


@pytest.mark.parametrize(("area", "diferenca"), [("media_mt", -100.0), ("media_redacao", 100.0)])
def test_perfil_calcula_a_faixa_de_cada_area_da_prova(gold, area, diferenca):
    assert consultar(gold, """select diferenca_pp from gold.perfil_alto_baixo_desempenho
        where rede = 'estadual' and area = %s and caracteristica = 'biblioteca'""", [area]) == [(diferenca,)]


def test_grupo_com_menos_de_dez_escolas_nao_publica_media(gold):
    assert consultar(gold, """select n_escolas_com_nota, media, desvio_padrao, suprimido from gold.desempenho_por_item
        where uf = 'DF' and rede = 'estadual' and item_codigo = 'internet' and situacao = 'existente' and area = 'media_geral'""") == []
    pequenos = consultar(gold, "select count(*) from gold.desempenho_por_item where suprimido and media is not null")
    assert pequenos == [(0,)]


def test_infraestrutura_conta_escolas_do_censo_sem_enem(gold):
    total = consultar(gold, """select sum(n_escolas) from gold.infra_por_uf
        where uf = 'BR' and rede = 'todas' and item_codigo = 'biblioteca'""")
    assert total == [(len(ESCOLAS) + 3,)]


def test_painel_exporta_so_as_escolas_das_faixas_alta_e_baixa(gold, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    configuracao = replace(configuracao_de(gold), ano_enem=2024)
    with painel.conectar(configuracao) as conexao:
        dados = painel.ler_dados(conexao, 2024)
    html = painel.renderizar_html(dados)
    exportado = json.loads(re.search(r'id="dados-painel">(.*?)</script>', html, re.S).group(1))
    assert exportado["cobertura"]["n_identificadas_no_censo"] == len(ESCOLAS)
    nomes = {escola[0] for escola in exportado["escolas_alto_baixo"]}
    assert {"ESCOLA 101", "ESCOLA 160"} <= nomes and "ESCOLA 131" not in html
    biblioteca = next(p for p in exportado["perfil"]
                      if p["rede"] == "estadual" and p["area"] == "media_geral" and p["caracteristica"] == "biblioteca")
    assert biblioteca["diferenca_pp"] == 100.0
