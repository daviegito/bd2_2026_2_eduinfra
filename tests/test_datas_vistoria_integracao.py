"""Contrato de datas na origem e na publicação, sempre em bancos descartáveis."""

import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from deltalake import DeltaTable

from eduinfra import cli, eventos
from eduinfra.configuracao import carregar_configuracao

pytestmark = pytest.mark.integracao
RAIZ = Path(__file__).resolve().parents[1]
REGRA = "vistoria_ocorrido_em_representavel"
INVALIDAS = ["-infinity", "infinity", "4000-01-01 BC", "0001-12-31 23:59:59 BC", "10000-01-01 00:00:00+00"]


@pytest.fixture
def banco(monkeypatch, tmp_path):
    admin_dsn = os.environ.get("DATABASE_URL")
    if not admin_dsn:
        pytest.skip("DATABASE_URL não definida")
    nome = f"eduinfra_datas_{uuid4().hex[:10]}"
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(f'create database "{nome}"')
    dsn = urlsplit(admin_dsn)._replace(path=f"/{nome}").geturl()
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("DIRETORIO_DADOS", str(tmp_path))
    try:
        yield carregar_configuracao()
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute("select pg_drop_replication_slot(slot_name) from pg_replication_slots where database = %s", (nome,))
            admin.execute(f'drop database "{nome}" with (force)')


def migrar(revisao="head"):
    command.upgrade(Config(str(RAIZ / "alembic.ini")), revisao)


def semear(config):
    with psycopg.connect(config.dsn, autocommit=True) as conn:
        conn.execute("insert into municipio values (1, 'Município', 'DF', 53)")
        conn.execute("insert into escola (co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia) values (1, 'Escola', 1, 1, 1, 2024)")
        conn.execute("insert into tecnico (matricula, nome, lotacao_uf) values ('T1', 'Técnico', 'DF')")


def inserir(config, data, registro="infinity", com_item=True):
    with psycopg.connect(config.dsn) as conn:
        id = conn.execute(
            "insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em, registrado_em) "
            "values (1, (select id from tecnico limit 1), 'vistoria_presencial', %s::timestamptz, %s::timestamptz) returning id",
            (data, registro),
        ).fetchone()[0]
        if com_item:
            conn.execute("insert into vistoria_item (vistoria_id, item_codigo, situacao) values (%s, 'biblioteca', 'existente')", (id,))
        return id


def historico(config):
    with psycopg.connect(config.dsn) as conn:
        return conn.execute("select to_jsonb(v)::text from vistoria v order by id").fetchall()


@pytest.mark.parametrize("data", INVALIDAS)
def test_origem_rejeita_datas_invalidas(banco, data):
    migrar()
    semear(banco)
    with pytest.raises(psycopg.errors.CheckViolation) as erro:
        inserir(banco, data)
    assert erro.value.diag.constraint_name == REGRA
    assert historico(banco) == []


@pytest.mark.parametrize("data", ["0001-01-01 00:00:00+00", "1999-12-31 23:59:59+00", "2024-05-29", "9999-12-31 23:59:59.999999+00"])
def test_limites_validos_e_historico_preservados_na_migracao(banco, data):
    migrar("0008")
    semear(banco)
    inserir(banco, data, registro=data)
    antes = historico(banco)
    migrar()
    assert historico(banco) == antes
    with psycopg.connect(banco.dsn) as conn:
        conn.execute("set time zone 'America/Sao_Paulo'")
        conn.execute("alter table vistoria validate constraint " + REGRA)
    tabela = eventos.ler_da_origem(banco)
    assert tabela.num_rows == 1
    assert tabela["ocorrido_em"].to_pylist()[0].year == int(data[:4])
    assert tabela["mes_ocorrencia"].to_pylist() == [data[:7]]
    assert eventos.carregar_da_origem(banco).inseridas == 1
    assert DeltaTable(str(eventos.caminho_da_tabela(banco))).to_pyarrow_table()["ocorrido_em"].to_pylist()[0].year == int(data[:4])


@pytest.mark.parametrize("data", INVALIDAS)
def test_migracao_diagnostica_legado_sem_alterar_historico(banco, data):
    migrar("0008")
    semear(banco)
    id = inserir(banco, data)
    antes = historico(banco)
    with pytest.raises(RuntimeError, match=f"id={id}, ocorrido_em="):
        migrar()
    assert historico(banco) == antes
    with psycopg.connect(banco.dsn) as conn:
        assert conn.execute("select version_num from alembic_version").fetchone() == ("0008",)
        assert conn.execute("select count(*) from pg_constraint where conname = %s", (REGRA,)).fetchone() == (0,)


def test_migracao_vazia_e_reversao_preservam_regra_temporal(banco):
    migrar()
    semear(banco)
    with pytest.raises(psycopg.errors.CheckViolation) as erro:
        inserir(banco, "2024-05-30", registro="2024-05-29")
    assert erro.value.diag.constraint_name == "vistoria_evento_antes_do_registro"
    command.downgrade(Config(str(RAIZ / "alembic.ini")), "0008")
    inserir(banco, "-infinity")
    with psycopg.connect(banco.dsn) as conn:
        assert conn.execute("select count(*) from pg_constraint where conname = 'vistoria_evento_antes_do_registro'").fetchone() == (1,)


@pytest.mark.parametrize("data", INVALIDAS)
@pytest.mark.parametrize("delta_preexistente", [False, True])
def test_forcar_rejeita_legado_sem_criar_particao_ou_mudar_delta(banco, data, delta_preexistente):
    migrar("0008")
    semear(banco)
    inserir(banco, "2024-05-29", registro="2024-05-30")
    caminho = eventos.caminho_da_tabela(banco)
    if delta_preexistente:
        assert cli.main(["publicar-analitico", "--forcar"]) == 0
        tabela = DeltaTable(str(caminho))
        versao, antes = tabela.version(), tabela.to_pyarrow_table().to_pylist()
    inserir(banco, data, com_item=False)
    arquivos = sorted(str(p.relative_to(banco.diretorio_dados)) for p in banco.diretorio_dados.rglob("*") if p.is_file())
    with pytest.raises(RuntimeError, match="Publicação analítica interrompida.*id="):
        cli.main(["publicar-analitico", "--forcar"])
    assert sorted(str(p.relative_to(banco.diretorio_dados)) for p in banco.diretorio_dados.rglob("*") if p.is_file()) == arquivos
    if delta_preexistente:
        depois = DeltaTable(str(caminho))
        assert depois.version() == versao
        assert depois.to_pyarrow_table().to_pylist() == antes
    else:
        assert not caminho.exists()


def test_validacao_e_exportacao_observam_o_mesmo_snapshot(banco, monkeypatch):
    migrar("0008")
    semear(banco)
    inserir(banco, "2024-05-29", registro="2024-05-30")
    conectar = eventos.duckdb.connect

    class LeituraConcorrente:
        def __init__(self):
            self.conexao = conectar()

        def execute(self, sql):
            if sql == eventos.LINHA_DE_BASE:
                inserir(banco, "-infinity")
            return self.conexao.execute(sql)

        def close(self):
            self.conexao.close()

    monkeypatch.setattr(eventos.duckdb, "connect", LeituraConcorrente)
    tabela = eventos.ler_da_origem(banco)
    assert tabela.num_rows == 1
    assert tabela["mes_ocorrencia"].to_pylist() == ["2024-05"]
    assert len(historico(banco)) == 2
