"""Comparação dos planos entre a view e a Gold, cada teste num banco descartável."""

import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from eduinfra import otimizacao_consulta

pytestmark = pytest.mark.integracao

DSN_ADMIN = os.environ.get("DATABASE_URL")
RAIZ = Path(__file__).resolve().parents[1]
ROTULOS = ("antes", "depois", "painel")


@pytest.fixture
def dsn():
    if not DSN_ADMIN:
        pytest.skip("DATABASE_URL não definida")

    nome = f"eduinfra_consulta_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(DSN_ADMIN, autocommit=True) as admin:
        admin.execute(f'create database "{nome}"')

    destino = urlsplit(DSN_ADMIN)._replace(path=f"/{nome}").geturl()
    os.environ["DATABASE_URL"] = destino
    try:
        command.upgrade(Config(str(RAIZ / "alembic.ini")), "head")
        _semear(destino)
        yield destino
    finally:
        os.environ["DATABASE_URL"] = DSN_ADMIN
        with psycopg.connect(DSN_ADMIN, autocommit=True) as admin:
            admin.execute(f'drop database if exists "{nome}" with (force)')


def _semear(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conexao:
        conexao.execute("insert into tecnico (matricula, nome, lotacao_uf) values ('T1', 'Técnico', 'DF')")
        conexao.execute("insert into municipio values (5300108, 'Brasília', 'DF', 53)")
        conexao.execute(
            """
            insert into escola (co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia)
            select n, 'ESCOLA ' || n, 5300108, 1 + n % 4, 1, 2024 from generate_series(1, 300) n
            """
        )
        conexao.execute(
            """
            insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em)
            select co_entidade, (select id from tecnico), 'censo_escolar', '2024-05-29T00:00:00Z' from escola
            """
        )
        conexao.execute(
            """
            insert into vistoria_item (vistoria_id, item_codigo, situacao)
            select v.id, i.codigo, case when v.co_entidade % 3 = 0 then 'inexistente' else 'existente' end::situacao_item
            from vistoria v cross join item_infraestrutura i
            """
        )
        conexao.execute("create schema gold")
        conexao.execute("create table gold.dim_escola as select co_entidade, tp_dependencia from escola")
        conexao.execute(
            """
            create table gold.fato_infra_escola_ano as
            select co_entidade, 2024 as ano, co_entidade % 3 <> 0 as biblioteca from escola
            """
        )


def _registrar_biblioteca_nova_em_2026(dsn: str, co_entidade: int) -> None:
    with psycopg.connect(dsn, autocommit=True) as conexao:
        vistoria_id = conexao.execute(
            """
            insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em)
            values (%s, (select id from tecnico), 'vistoria_presencial', '2026-03-01T00:00:00Z')
            returning id
            """,
            (co_entidade,),
        ).fetchone()[0]
        conexao.execute(
            "insert into vistoria_item (vistoria_id, item_codigo, situacao) values (%s, 'biblioteca', 'existente')",
            (vistoria_id,),
        )
        conexao.execute("insert into gold.fato_infra_escola_ano values (%s, 2026, true)", (co_entidade,))


def test_view_e_gold_dao_a_mesma_resposta(dsn):
    comparacao = otimizacao_consulta.comparar(dsn, repeticoes=2)

    assert comparacao.mesmo_resultado
    assert sum(escolas for _, escolas in comparacao.depois.linhas) == 100
    assert all(len(medicao.serie) == 2 for medicao in comparacao.medicoes().values())


def test_vistoria_posterior_muda_a_view_e_a_gold_mas_nao_o_painel_de_2024(dsn):
    _registrar_biblioteca_nova_em_2026(dsn, co_entidade=3)

    comparacao = otimizacao_consulta.comparar(dsn, repeticoes=1)

    assert comparacao.mesmo_resultado
    assert sum(escolas for _, escolas in comparacao.depois.linhas) == 99
    assert sum(escolas for _, escolas in comparacao.painel.linhas) == 100


def test_comparar_sem_a_gold_avisa_em_vez_de_medir(dsn):
    with psycopg.connect(dsn, autocommit=True) as conexao:
        conexao.execute("drop schema gold cascade")

    with pytest.raises(otimizacao_consulta.GoldAusente):
        otimizacao_consulta.comparar(dsn, repeticoes=1)


def test_planos_gravados_trazem_json_texto_e_serie_de_todos_os_lados(dsn, tmp_path):
    comparacao = otimizacao_consulta.comparar(dsn, repeticoes=2)

    otimizacao_consulta.gravar_planos(comparacao, tmp_path / "explain")

    pasta = tmp_path / "explain"
    serie = json.loads((pasta / "serie.json").read_text(encoding="utf-8"))
    for rotulo in ROTULOS:
        plano = json.loads((pasta / f"{rotulo}.json").read_text(encoding="utf-8"))
        assert "Shared Hit Blocks" in plano[0]["Plan"]
        assert "Settings" in plano[0]
        assert "Buffers:" in (pasta / f"{rotulo}.txt").read_text(encoding="utf-8")
        assert len(serie[rotulo]) == 2
    assert "Tempo de execução" in (pasta / "resumo.txt").read_text(encoding="utf-8")
