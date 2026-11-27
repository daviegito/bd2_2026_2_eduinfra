"""Anonimização do técnico (LGPD): o dado pessoal sai e as vistorias continuam."""

import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from eduinfra import anonimizacao, cli
from eduinfra.configuracao import carregar_configuracao

pytestmark = pytest.mark.integracao
RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture
def banco(monkeypatch, tmp_path):
    admin_dsn = os.environ.get("DATABASE_URL")
    if not admin_dsn:
        pytest.skip("DATABASE_URL não definida")
    nome = f"eduinfra_anonimizacao_{uuid4().hex[:10]}"
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(f'create database "{nome}"')
    dsn = urlsplit(admin_dsn)._replace(path=f"/{nome}").geturl()
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("DIRETORIO_DADOS", str(tmp_path))
    try:
        command.upgrade(Config(str(RAIZ / "alembic.ini")), "head")
        config = carregar_configuracao()
        semear(config)
        yield config
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute("select pg_drop_replication_slot(slot_name) from pg_replication_slots where database = %s", (nome,))
            admin.execute(f'drop database "{nome}" with (force)')


def semear(config):
    with psycopg.connect(config.dsn, autocommit=True) as conn:
        conn.execute("insert into municipio values (1, 'Município', 'DF', 53)")
        conn.execute(
            "insert into escola (co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia) "
            "values (1, 'Escola', 1, 1, 1, 2024)"
        )
        conn.execute("insert into tecnico (matricula, nome, lotacao_uf) values ('T1', 'Maria da Silva', 'GO'), ('T2', 'João Souza', 'DF')")
        for _ in range(2):
            id = conn.execute(
                "insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em) "
                "values (1, (select id from tecnico where matricula = 'T1'), 'vistoria_presencial', now() - interval '1 day') returning id"
            ).fetchone()[0]
            conn.execute("insert into vistoria_item (vistoria_id, item_codigo, situacao) values (%s, 'biblioteca', 'existente')", (id,))


def tecnicos(config):
    with psycopg.connect(config.dsn) as conn:
        return conn.execute("select id, matricula, nome, lotacao_uf, ativo from tecnico order by id").fetchall()


def vistorias(config):
    with psycopg.connect(config.dsn) as conn:
        return conn.execute("select to_jsonb(v)::text from vistoria v order by id").fetchall()


def test_anonimiza_sem_tocar_nas_vistorias(banco, capsys):
    antes = vistorias(banco)
    (id_t1, *_), t2_antes = tecnicos(banco)

    assert cli.main(["anonimizar-tecnico", "--matricula", "T1"]) == 0

    t1, t2 = tecnicos(banco)
    assert t1 == (id_t1, f"{anonimizacao.PREFIXO}{id_t1}", anonimizacao.NOME_ANONIMO, "GO", False)
    assert t2 == t2_antes
    assert vistorias(banco) == antes
    saida = capsys.readouterr().out
    assert "2 vistorias" in saida
    assert "Maria" not in saida and "T1" not in saida


def test_repetir_nao_altera_e_avisa(banco):
    assert cli.main(["anonimizar-tecnico", "--matricula", "T1"]) == 0
    depois = tecnicos(banco)

    assert cli.main(["anonimizar-tecnico", "--matricula", "T1"]) == 1
    assert tecnicos(banco) == depois


def test_matricula_inexistente_ou_ausente(banco):
    antes = tecnicos(banco)

    assert cli.main(["anonimizar-tecnico", "--matricula", "T9"]) == 1
    assert cli.main(["anonimizar-tecnico"]) == 2
    assert cli.main(["anonimizar-tecnico", "--matricula", f"{anonimizacao.PREFIXO}1"]) == 2
    assert tecnicos(banco) == antes
