"""O gerador real deve refletir o grão e o perfil temporal da Gold."""

from types import SimpleNamespace

import duckdb
import pytest

from eduinfra import cli, painel


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    conexao = duckdb.connect()
    conexao.execute("attach ':memory:' as pg; create schema pg.gold; create schema pg.entrada_transformacao")
    conexao.execute("create table pg.gold.dim_item (item_codigo varchar, descricao varchar)")
    conexao.execute("insert into pg.gold.dim_item values ('biblioteca', 'Biblioteca')")
    conexao.execute("create table pg.gold.dim_escola as select i as co_entidade, 2 as tp_dependencia from range(1,21) r(i)")
    conexao.execute("""create table pg.gold.dim_escola_infraestrutura as
        select i as co_entidade, i*10 as escola_infra_sk,
               '{"biblioteca":"existente"}'::json as itens from range(1,21) r(i)
        union all
        select i, i*10+1, '{"biblioteca":"inexistente"}'::json from range(1,21) r(i)""")
    conexao.execute("""create table pg.gold.fato_desempenho_escola_ano as
        select i as co_entidade, 2024 as ano, i*10 as escola_infra_sk,
               500.0+i as media_geral, date '2024-11-03' as data_referencia_infraestrutura
        from range(1,21) r(i)""")
    conexao.execute("""create table pg.entrada_transformacao.enem_escola_ano as
        select co_entidade, ano from pg.gold.fato_desempenho_escola_ano
        union all select 60000001, 2024""")
    configuracao = SimpleNamespace(dsn="fixture", ano_enem=2024)
    monkeypatch.setattr(painel, "conectar", lambda config: conexao)
    monkeypatch.setattr(painel, "carregar_configuracao", lambda: configuracao)
    monkeypatch.setattr(cli, "carregar_configuracao", lambda: configuracao)
    monkeypatch.chdir(tmp_path)
    quadros = []
    bar = painel.px.bar

    def registrar(dados, **opcoes):
        quadros.append(dados.copy())
        return bar(dados, **opcoes)

    monkeypatch.setattr(painel.px, "bar", registrar)
    return conexao, quadros, tmp_path / "docs" / "painel.html"


def test_cli_gera_grafico_real_sem_duplicar_perfis_ou_anos(ambiente):
    conexao, quadros, destino = ambiente
    conexao.execute("""insert into pg.gold.fato_desempenho_escola_ano
        select co_entidade,2025,escola_infra_sk+1,900,date '2025-11-03'
        from pg.gold.fato_desempenho_escola_ano""")
    assert cli.main(["gerar-painel"]) == 0
    html = destino.read_text()
    assert "Desempenho x Biblioteca" in html
    assert 'data-indicador="escolas-enem">21<' in html
    assert 'data-indicador="escolas-com-censo">20 (95.2%)<' in html
    assert "Sem correspondência no Censo: <strong>1</strong>" in html
    assert len(quadros) == 2
    grupo = quadros[1].iloc[0]
    assert grupo["infra_status"] == "Possui o item"
    assert grupo["amostra_n"] == 20
    assert grupo["media_enem"] == 510.5
    assert quadros[0]["qtd_escolas"].sum() == 20


@pytest.mark.parametrize("estado,rotulo", [
    ("existente", "Possui o item"), ("inexistente", "Não possui"),
    ("em_obra", "Em obra"), ("inutilizavel", "Inutilizável"),
    ("nao_informado", "Não informado"), (None, "Não informado"),
])
def test_estados_sao_extraidos_do_json_sem_inventar_ausencia(ambiente, estado, rotulo):
    conexao, quadros, _ = ambiente
    conexao.execute("update pg.gold.dim_escola_infraestrutura set itens = ?", [
        '{}' if estado is None else '{"biblioteca":"'+estado+'"}'
    ])
    painel.gerar_painel()
    assert quadros[1]["infra_status"].tolist() == [rotulo]
    assert quadros[1]["amostra_n"].tolist() == [20]


def test_escola_sem_perfil_na_referencia_permanece_desconhecida(ambiente):
    conexao, quadros, destino = ambiente
    conexao.execute("update pg.gold.fato_desempenho_escola_ano set escola_infra_sk = null")
    painel.gerar_painel()
    assert quadros[1]["infra_status"].tolist() == ["Não informado"]
    assert "Com perfil na referência do ENEM: <strong>0</strong>" in destino.read_text()


def test_sem_nota_nao_infla_n_e_grupo_pequeno_e_sinalizado(ambiente):
    conexao, quadros, destino = ambiente
    conexao.execute("update pg.gold.fato_desempenho_escola_ano set media_geral = null where co_entidade > 9")
    painel.gerar_painel()
    assert len(quadros) == 1
    assert "Nenhum grupo atingiu o mínimo de 10 escolas com nota válida" in destino.read_text()
    assert "Escolas sem média geral válida: <strong>11</strong>" in destino.read_text()


def test_fato_ausente_falha_na_cli_sem_criar_html(ambiente):
    conexao, _, destino = ambiente
    conexao.execute("drop table pg.gold.fato_desempenho_escola_ano")
    with pytest.raises(RuntimeError, match="transformacao"):
        cli.main(["gerar-painel"])
    assert not destino.exists()
