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


@pytest.fixture(scope="module")
def dsn():
    valor = os.environ.get("SEMANTICA_BANCADA_DSN")
    if not valor:
        pytest.skip("defina SEMANTICA_BANCADA_DSN para a bancada isolada da #83")
    semantica.publicar(valor)
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
        conexao.execute("insert into gold.dim_municipio(co_municipio, sg_uf) values (9999999, 'DF')")
        ids = [r[0] for r in conexao.execute(
            "select co_entidade from gold.fato_desempenho_escola_ano where media_geral is not null order by co_entidade limit 10"
        )]
        conexao.execute("update gold.dim_escola set co_municipio=9999999 where co_entidade=any(%s)", (ids[:9],))
        filtros = json.dumps({"ano": 2024, "municipio": 9999999})
        assert conexao.execute("select metricas.media_enem_escolas(%s::jsonb)", (filtros,)).fetchone()[0] == []
        conexao.execute("update gold.dim_escola set co_municipio=9999999 where co_entidade=%s", (ids[9],))
        dez = conexao.execute("select metricas.media_enem_escolas(%s::jsonb)", (filtros,)).fetchone()[0]
        assert dez[0]["n_escolas"] == 10


@pytest.mark.integracao
@pytest.mark.parametrize('arquivo', ['base_analise.sql', 'base_cobertura.sql', 'validar_filtros.sql'])
def test_sql_alterado_exige_republicacao(dsn, tmp_path, arquivo):
    import shutil
    pasta = tmp_path / 'metricas'
    shutil.copytree(semantica.CATALOGO, pasta)
    with (pasta / 'sql' / arquivo).open('a') as destino:
        destino.write('\n-- revisão do contrato SQL\n')
    with pytest.raises(RuntimeError, match='Catálogo mudou'):
        semantica.consultar(dsn, 'media_enem_escolas', {'ano': 2024}, pasta)


@pytest.mark.integracao
def test_municipio_inexistente_e_recusado(dsn):
    with pytest.raises(psycopg.Error, match='Município não encontrado'):
        semantica.consultar(dsn, 'media_enem_escolas', {'ano': 2024, 'municipio': 9999999})


@pytest.mark.integracao
def test_publicacao_com_sql_invalido_preserva_versao_anterior(dsn, tmp_path):
    import shutil
    import yaml
    pasta = tmp_path / 'metricas'
    shutil.copytree(semantica.CATALOGO, pasta)
    arquivo = pasta / 'media_enem_escolas.yml'
    definicao = yaml.safe_load(arquivo.read_text())
    definicao['formula'] = 'select isto nao e sql valido'
    arquivo.write_text(yaml.safe_dump(definicao))
    antes = semantica.consultar(dsn, 'media_enem_escolas', {'ano': 2024})
    with pytest.raises(psycopg.Error):
        semantica.publicar(dsn, pasta)
    assert semantica.consultar(dsn, 'media_enem_escolas', {'ano': 2024}) == antes


def test_catalogo_sem_ativas_e_recusado_antes_de_conectar(tmp_path):
    with pytest.raises(ValueError, match='sem métricas ativas'):
        semantica.publicar('dsn-invalida', tmp_path)


@pytest.mark.integracao
def test_desativar_metrica_remove_funcao_sql(dsn, tmp_path):
    import shutil
    import yaml
    pasta = tmp_path / 'metricas'
    shutil.copytree(semantica.CATALOGO, pasta)
    arquivo = pasta / 'media_enem_escolas.yml'
    definicao = yaml.safe_load(arquivo.read_text())
    definicao['status'] = 'rascunho'
    arquivo.write_text(yaml.safe_dump(definicao))
    try:
        semantica.publicar(dsn, pasta)
        with psycopg.connect(dsn) as conexao:
            assert conexao.execute("select to_regprocedure('metricas.media_enem_escolas(jsonb)')").fetchone()[0] is None
    finally:
        semantica.publicar(dsn)


@pytest.mark.integracao
@pytest.mark.parametrize('papel,senha', [('eduinfra_painel','senha_local_painel'), ('eduinfra_analise','senha_local_analise')])
def test_papeis_leem_interface_mas_nao_bases(dsn, papel, senha):
    from psycopg.conninfo import make_conninfo
    leitor = make_conninfo(dsn, user=papel, password=senha)
    assert semantica.consultar(leitor, 'media_enem_escolas', {'ano': 2024})['dados']
    for base in ('base_analise', 'base_cobertura'):
        with psycopg.connect(leitor) as conexao, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conexao.execute(f'select * from metricas.{base}')
    with psycopg.connect(leitor) as conexao, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conexao.execute('create table metricas.escrita_proibida(id int)')


@pytest.mark.integracao
def test_dono_publicador_e_execute_public(dsn):
    from psycopg.conninfo import make_conninfo
    publicador = make_conninfo(dsn, user='eduinfra_orquestrador', password='senha_local_orquestrador')
    semantica.publicar(publicador)
    with psycopg.connect(dsn) as conexao:
        dono, protegido, busca = conexao.execute("select pg_get_userbyid(proowner), prosecdef, proconfig from pg_proc where oid='metricas.media_enem_escolas(jsonb)'::regprocedure").fetchone()
        assert dono == 'eduinfra_dbt' and protegido
        assert 'search_path=pg_catalog, pg_temp' in busca
        assert not conexao.execute("select has_function_privilege('eduinfra_tecnico_df', 'metricas.media_enem_escolas(jsonb)', 'execute')").fetchone()[0]
    carga = make_conninfo(dsn, user='eduinfra_carga', password='senha_local_carga')
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        semantica.publicar(carga)


@pytest.mark.integracao
def test_gold_sem_enem_preserva_metrica_de_infraestrutura(dsn):
    try:
        with psycopg.connect(dsn) as conexao:
            conexao.execute('alter table gold.fato_desempenho_escola_ano rename to desempenho_temporariamente_ausente')
            conexao.execute('alter table entrada_transformacao.enem_escola_ano rename to enem_temporariamente_ausente')
        assert semantica.publicar(dsn) == ['pct_escolas_por_situacao_do_item']
        assert semantica.consultar(dsn, 'pct_escolas_por_situacao_do_item', {'ano':2024,'item':'biblioteca'})['dados']
        with pytest.raises(RuntimeError, match='indisponível'):
            semantica.consultar(dsn, 'media_enem_escolas', {'ano':2024})
    finally:
        with psycopg.connect(dsn) as conexao:
            conexao.execute('alter table gold.desempenho_temporariamente_ausente rename to fato_desempenho_escola_ano')
            conexao.execute('alter table entrada_transformacao.enem_temporariamente_ausente rename to enem_escola_ano')
        semantica.publicar(dsn)


@pytest.mark.integracao
def test_percentuais_tem_oraculo_independente(dsn):
    from collections import Counter
    situacoes = ['existente', 'inexistente', 'em_obra', 'inutilizavel', 'nao_informado']
    contagens = Counter('nao_informado' if i % 11 == 0 else situacoes[i % 5] for i in range(1,6001))
    dados = semantica.consultar(dsn, 'pct_escolas_por_situacao_do_item', {'ano':2024,'item':'biblioteca'})['dados']
    assert len(dados) == 5
    for linha in dados:
        assert linha['n_escolas'] == contagens[linha['situacao']]
        assert linha['percentual'] == pytest.approx(round(100*contagens[linha['situacao']]/6000,1))


@pytest.mark.integracao
def test_diferenca_tem_oraculo_independente(dsn):
    from statistics import mean
    redes = ['municipal','estadual','federal','privada']
    dados = semantica.consultar(dsn, 'diferenca_media_com_sem_item', {'ano':2024,'item':'biblioteca'})['dados']
    assert len(dados) == 4
    for linha in dados:
        grupos = [[400+i%300 for i in range(1,6001) if i%6 and i%13 and i%11 and redes[i%4]==linha['rede'] and i%5==situacao] for situacao in [0,1]]
        assert linha['n_com'] == len(grupos[0])
        assert linha['n_sem'] == len(grupos[1])
        assert linha['media_com'] == pytest.approx(mean(grupos[0]))
        assert linha['media_sem'] == pytest.approx(mean(grupos[1]))
        assert linha['diferenca'] == pytest.approx(mean(grupos[0])-mean(grupos[1]))


@pytest.mark.integracao
def test_diferenca_suprime_rede_com_nove_em_um_lado(dsn):
    import json
    with psycopg.connect(dsn) as conexao, conexao.transaction(force_rollback=True):
        conexao.execute("insert into gold.dim_municipio(co_municipio,sg_uf) values (9999998,'DF')")
        ids = [r[0] for r in conexao.execute("select f.co_entidade from gold.fato_desempenho_escola_ano f join gold.dim_escola e using(co_entidade) where e.rede='estadual' and f.media_geral is not null order by f.co_entidade limit 20")]
        for grupo, situacao in [(ids[:10],'existente'),(ids[10:],'inexistente')]:
            conexao.execute("update gold.dim_escola_infraestrutura set itens=jsonb_set(itens,'{biblioteca}',to_jsonb(%s::text)) where co_entidade=any(%s)",(situacao,grupo))
        conexao.execute('update gold.dim_escola set co_municipio=9999998 where co_entidade=any(%s)',(ids[:19],))
        filtros = json.dumps({'ano':2024,'item':'biblioteca','municipio':9999998})
        assert conexao.execute('select metricas.diferenca_media_com_sem_item(%s::jsonb)',(filtros,)).fetchone()[0] == []
        conexao.execute('update gold.dim_escola set co_municipio=9999998 where co_entidade=%s',(ids[19],))
        dados = conexao.execute('select metricas.diferenca_media_com_sem_item(%s::jsonb)',(filtros,)).fetchone()[0]
        assert len(dados) == 1
        assert dados[0]['n_com'] == dados[0]['n_sem'] == 10
