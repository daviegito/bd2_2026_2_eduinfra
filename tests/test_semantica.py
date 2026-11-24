"""Contrato público; integração usa somente a bancada explicitamente configurada."""

import os

import psycopg
import pytest

from eduinfra import semantica


def test_catalogo_publica_somente_metricas_ativas():
    definicoes = semantica.carregar_definicoes()
    assert len(definicoes) == 4
    assert "prioridade_vistoria" not in definicoes


def test_nome_nao_e_interpolado_da_entrada():
    with pytest.raises(ValueError, match="não encontrada"):
        semantica.consultar("", "x);drop schema gold;--", {"ano": 2024})


@pytest.fixture
def dsn():
    valor = os.environ.get("SEMANTICA_BANCADA_DSN")
    if not valor:
        pytest.skip("defina SEMANTICA_BANCADA_DSN para a bancada isolada da #83")
    return valor


@pytest.mark.integracao
@pytest.mark.parametrize("filtros", [
    {}, {"ano": "2024"}, {"ano": True}, {"ano": 0}, {"ano": 2024, "uf": "XX"},
    {"ano": 2024, "municipio": "5300108"}, {"ano": 2024, "rede": "todas"},
    {"ano": 2024, "area": "media_x"}, {"ano": 2024, "situacao": "existente"},
    {"ano": 2024, "item": "nao_existe"}, {"ano": 2024, "item": "biblioteca"}, {"ano": 2024, "filtro_inexistente": 1},
    {"ano": 1900, "filtro_inexistente": 1}, [], None,
])
def test_filtros_invalidos_nao_passam_silenciosamente(dsn, filtros):
    with pytest.raises((ValueError, psycopg.Error)):
        semantica.consultar(dsn, "media_enem_escolas", filtros)


@pytest.mark.integracao
@pytest.mark.parametrize("nome,filtros", [
    ("media_enem_escolas", {"ano": 2024}),
    ("media_enem_escolas", {"ano": 2024, "uf": "DF", "area": "media_mt", "item": "biblioteca", "situacao": "existente"}),
    ("diferenca_media_com_sem_item", {"ano": 2024, "item": "biblioteca"}),
    ("pct_escolas_por_situacao_do_item", {"ano": 2024, "item": "biblioteca"}),
    ("cobertura_enem_censo", {"ano": 2024}),
])
def test_interface_python_e_sql_entregam_mesmo_resultado(dsn, nome, filtros):
    import json
    from psycopg import sql

    resultado = semantica.consultar(dsn, nome, filtros)
    with psycopg.connect(dsn) as conexao:
        direto = conexao.execute(sql.SQL("select metricas.{}(%s::jsonb)").format(sql.Identifier(nome)),
                                (json.dumps(filtros),)).fetchone()[0]
    assert resultado["dados"] == direto


@pytest.mark.integracao
def test_cobertura_nao_aceita_filtro_que_nao_pode_aplicar(dsn):
    with pytest.raises(psycopg.Error, match="Filtro não aceito"):
        semantica.consultar(dsn, "cobertura_enem_censo", {"ano": 2024, "uf": "DF"})


@pytest.mark.integracao
def test_item_obrigatorio(dsn):
    with pytest.raises(psycopg.Error, match="Informe o item"):
        semantica.consultar(dsn, "pct_escolas_por_situacao_do_item", {"ano": 2024})


@pytest.mark.integracao
def test_supressao_e_populacao_vazia(dsn):
    assert semantica.consultar(dsn, "media_enem_escolas", {"ano": 1900})["dados"] == []
    assert semantica.consultar(dsn, "pct_escolas_por_situacao_do_item", {"ano": 1900, "item": "biblioteca"})["dados"] == []
    cobertura = semantica.consultar(dsn, "cobertura_enem_censo", {"ano": 1900})["dados"][0]
    assert cobertura["escolas_enem"] == 0
    assert cobertura["percentual"] is None


@pytest.mark.integracao
def test_catalogo_desatualizado_exige_republicacao(dsn, tmp_path):
    import shutil
    import yaml

    pasta = tmp_path / "metricas"
    shutil.copytree(semantica.CATALOGO, pasta)
    arquivo = pasta / "media_enem_escolas.yml"
    definicao = yaml.safe_load(arquivo.read_text())
    definicao["supressao"]["minimo"] = 12
    arquivo.write_text(yaml.safe_dump(definicao))
    with pytest.raises(RuntimeError, match="Catálogo mudou"):
        semantica.consultar(dsn, "media_enem_escolas", {"ano": 2024}, pasta)


@pytest.mark.integracao
def test_fixture_tem_resultado_independente_das_duas_rotas(dsn):
    from statistics import mean, stdev

    if psycopg.conninfo.conninfo_to_dict(dsn).get("dbname") != "eduinfra83":
        pytest.skip("oráculo reservado à fixture sintética da #83")
    notas = [400 + i % 300 for i in range(1, 6001) if i % 6 != 0 and i % 13 != 0]
    resultado = semantica.consultar(dsn, "media_enem_escolas", {"ano": 2024})["dados"][0]
    assert resultado["n_escolas"] == len(notas)
    assert resultado["media"] == pytest.approx(mean(notas))
    assert resultado["desvio_padrao"] == pytest.approx(stdev(notas))
    cobertura = semantica.consultar(dsn, "cobertura_enem_censo", {"ano": 2024})["dados"][0]
    assert cobertura == {"escolas_enem": 5100, "encontradas": 5000, "nao_encontradas": 100, "mascaradas": 100, "percentual": 98.0}


@pytest.mark.integracao
def test_limiar_de_supressao_nove_e_dez_escolas(dsn):
    import json

    if psycopg.conninfo.conninfo_to_dict(dsn).get("dbname") != "eduinfra83":
        pytest.skip("alteração com rollback reservada à bancada #83")
    with psycopg.connect(dsn) as conexao, conexao.transaction(force_rollback=True):
        ids = [r[0] for r in conexao.execute(
            "select co_entidade from gold.fato_desempenho_escola_ano where media_geral is not null order by co_entidade limit 10"
        )]
        conexao.execute("update gold.dim_escola set co_municipio=9999999 where co_entidade=any(%s)", (ids[:9],))
        filtros = json.dumps({"ano": 2024, "municipio": 9999999})
        assert conexao.execute("select metricas.media_enem_escolas(%s::jsonb)", (filtros,)).fetchone()[0] == []
        conexao.execute("update gold.dim_escola set co_municipio=9999999 where co_entidade=%s", (ids[9],))
        dez = conexao.execute("select metricas.media_enem_escolas(%s::jsonb)", (filtros,)).fetchone()[0]
        assert dez[0]["n_escolas"] == 10
