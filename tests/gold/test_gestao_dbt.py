"""Tabelas de consumo da Gold construídas pelo dbt a partir de escolas com resultado conhecido."""

from dataclasses import replace

import psycopg
import pytest

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
PARTICIPANTES_POR_ESCOLA = 10


def nota_da(escola: int) -> int:
    return 400 + 5 * (escola - PRIMEIRA + 1)


def tem_biblioteca(escola: int) -> bool:
    return escola >= PRIMEIRA + 30


def semear_escolas(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conexao:
        for escola in ESCOLAS:
            conexao.execute(
                "insert into escola (co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia) "
                "values (%s, %s, 5300108, 2, 1, 2024)", (escola, f"ESCOLA {escola}"))
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
        nota = str(nota_da(escola))
        for _ in range(PARTICIPANTES_POR_ESCOLA):
            linhas.append(linha(len(linhas) + 1, escola, ("1", "1", "1", "1"), (nota, nota, nota, nota), "1", nota))
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


def test_perfil_alto_baixo_ordena_o_que_mais_diferencia_e_suprime_o_desconhecido(gold):
    perfil = consultar(gold, """select item_codigo, pct_alto_com_item, pct_baixo_com_item, diferenca_pp, suprimido, ordem
        from gold.perfil_alto_baixo_desempenho where rede = 'estadual' and item_codigo in ('biblioteca', 'internet')
        order by ordem""")
    assert perfil[0] == ("biblioteca", 100.0, 0.0, 100.0, False, 1)
    assert perfil[1][:5] == ("internet", None, None, None, True)


def test_grupo_com_menos_de_dez_escolas_nao_publica_media(gold):
    assert consultar(gold, """select n_escolas_com_nota, media, desvio_padrao, suprimido from gold.desempenho_por_item
        where uf = 'DF' and rede = 'estadual' and item_codigo = 'internet' and situacao = 'existente' and area = 'media_geral'""") == []
    pequenos = consultar(gold, "select count(*) from gold.desempenho_por_item where suprimido and media is not null")
    assert pequenos == [(0,)]


def test_infraestrutura_conta_escolas_do_censo_sem_enem(gold):
    total = consultar(gold, """select sum(n_escolas) from gold.infra_por_uf
        where uf = 'BR' and rede = 'todas' and item_codigo = 'biblioteca'""")
    assert total == [(len(ESCOLAS) + 3,)]

