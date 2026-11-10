"""Ingestão idempotente do ENEM, contra um banco descartável criado pelo módulo.

Mesmo protocolo do Censo (tests/test_ingestao.py): manifesto por (fonte, ano),
Silver trocada inteira e só depois do commit.
"""

import os
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from eduinfra import enem, ingestao_enem, transformacao
from eduinfra.configuracao import Configuracao
from eduinfra.ingestao import hash_do_arquivo
from eduinfra.ingestao_enem import ingerir_enem
from tests.test_enem import COMPLEMENTO, CASOS_DA_ESCOLA_REAL, OUTRAS, escrever_resultados, ler

pytestmark = pytest.mark.integracao

DSN_ADMIN = os.environ.get("DATABASE_URL")
RAIZ = Path(__file__).resolve().parents[1]
LINHAS = [*CASOS_DA_ESCOLA_REAL, *COMPLEMENTO, *OUTRAS]


@pytest.fixture(scope="module")
def dsn_teste():
    if not DSN_ADMIN:
        pytest.skip("DATABASE_URL não definida")

    nome = f"eduinfra_teste_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(DSN_ADMIN, autocommit=True) as admin:
        admin.execute(f'create database "{nome}"')

    dsn = urlsplit(DSN_ADMIN)._replace(path=f"/{nome}").geturl()
    os.environ["DATABASE_URL"] = dsn
    try:
        command.upgrade(Config(str(RAIZ / "alembic.ini")), "head")
        yield dsn
    finally:
        os.environ["DATABASE_URL"] = DSN_ADMIN
        with psycopg.connect(DSN_ADMIN, autocommit=True) as admin:
            admin.execute(f'drop database if exists "{nome}" with (force)')


@pytest.fixture(autouse=True)
def manifesto_zerado(dsn_teste):
    with psycopg.connect(dsn_teste, autocommit=True) as conexao:
        conexao.execute("truncate ingestao_execucao restart identity")
    yield


@pytest.fixture
def cfg(dsn_teste, tmp_path) -> Configuracao:
    return Configuracao(
        dsn=dsn_teste,
        url_censo="https://exemplo.invalido/censo.zip",
        ano_censo=2024,
        data_referencia=date(2024, 5, 29),
        diretorio_dados=tmp_path / "dados",
        arquivo_alembic=RAIZ / "alembic.ini",
        url_enem="https://exemplo.invalido/enem.zip",
    )


def manifesto(dsn: str) -> list[tuple]:
    with psycopg.connect(dsn) as conexao:
        return conexao.execute(
            "select fonte, ano, hash_sha256, forcada, linhas_publicadas, escolas_inseridas "
            "from ingestao_execucao order by id"
        ).fetchall()


def conteudo_da_silver(cfg: Configuracao) -> tuple[list[dict], list[dict]]:
    particao = enem.particao_enem(cfg)

    return ler(particao / enem.ARQUIVO_DESEMPENHO), ler(particao / enem.ARQUIVO_RESUMO)


def sobras_de_publicacao(cfg: Configuracao) -> list[str]:
    area = transformacao.area_de_publicacao(cfg)

    return sorted(p.name for p in area.iterdir()) if area.exists() else []


def test_reexecutar_e_ignorado_e_nao_altera_o_resultado(cfg):
    csv = escrever_resultados(cfg.diretorio_dados, LINHAS)

    primeira = ingerir_enem(cfg)
    antes = conteudo_da_silver(cfg), manifesto(cfg.dsn)
    segunda = ingerir_enem(cfg)
    depois = conteudo_da_silver(cfg), manifesto(cfg.dsn)

    assert not primeira.ignorada
    assert primeira.escolas == 2
    assert segunda.ignorada
    assert antes == depois
    assert manifesto(cfg.dsn) == [("enem", 2024, hash_do_arquivo(csv), False, 2, None)]
    assert enem.hash_da_silver_enem(cfg) == hash_do_arquivo(csv)
    assert sobras_de_publicacao(cfg) == []


def test_forcar_reexecuta_com_o_mesmo_resultado(cfg):
    escrever_resultados(cfg.diretorio_dados, LINHAS)

    ingerir_enem(cfg)
    antes = conteudo_da_silver(cfg)
    forcada = ingerir_enem(cfg, forcar=True)

    assert not forcada.ignorada
    assert conteudo_da_silver(cfg) == antes
    assert [linha[3] for linha in manifesto(cfg.dsn)] == [False, True]


def test_execucao_do_censo_no_mesmo_ano_nao_vale_para_o_enem(cfg):
    csv = escrever_resultados(cfg.diretorio_dados, LINHAS)
    with psycopg.connect(cfg.dsn) as conexao:
        conexao.execute(
            """
            insert into ingestao_execucao (fonte, ano, arquivo_origem, hash_sha256, municipios_inseridos,
                                           escolas_inseridas, vistorias_inseridas, itens_inseridos, iniciado_em)
            values ('censo', 2024, 'microdados_ed_basica_2024.csv', %s, 0, 0, 0, 0, now())
            """,
            (hash_do_arquivo(csv),),
        )

    assert not ingerir_enem(cfg).ignorada
    assert [linha[0] for linha in manifesto(cfg.dsn)] == ["censo", "enem"]


def test_arquivo_corrigido_troca_a_particao_inteira(cfg):
    vizinha = cfg.diretorio_dados / "silver" / "enem" / "ano=2023"
    vizinha.mkdir(parents=True)
    (vizinha / enem.ARQUIVO_DESEMPENHO).write_bytes(b"particao de outro ano")

    escrever_resultados(cfg.diretorio_dados, LINHAS)
    ingerir_enem(cfg)
    corrigido = escrever_resultados(cfg.diretorio_dados, CASOS_DA_ESCOLA_REAL + COMPLEMENTO)
    troca = ingerir_enem(cfg)

    desempenho, _ = conteudo_da_silver(cfg)
    assert not troca.ignorada
    assert {e["co_escola"] for e in desempenho} == {53000001}
    assert enem.hash_da_silver_enem(cfg) == hash_do_arquivo(corrigido)
    assert (vizinha / enem.ARQUIVO_DESEMPENHO).read_bytes() == b"particao de outro ano"
    assert len(manifesto(cfg.dsn)) == 2


def test_falha_antes_do_commit_nao_registra_nem_publica(cfg, monkeypatch):
    escrever_resultados(cfg.diretorio_dados, LINHAS)
    registrar_real = ingestao_enem._registrar

    def registrar_e_interromper(*argumentos, **nomeados):
        registrar_real(*argumentos, **nomeados)
        raise RuntimeError("interrupção simulada antes do commit")

    monkeypatch.setattr(ingestao_enem, "_registrar", registrar_e_interromper)

    with pytest.raises(RuntimeError, match="antes do commit"):
        ingerir_enem(cfg)

    assert manifesto(cfg.dsn) == []
    assert not enem.particao_enem(cfg).exists()
    assert sobras_de_publicacao(cfg) == []

    monkeypatch.setattr(ingestao_enem, "_registrar", registrar_real)
    assert not ingerir_enem(cfg).ignorada


def test_falha_depois_do_commit_republica_so_a_silver(cfg, monkeypatch):
    escrever_resultados(cfg.diretorio_dados, LINHAS)
    publicar_real = ingestao_enem.publicar_silver_enem

    def publicar_e_falhar(*argumentos, **nomeados):
        raise OSError("falha simulada na publicação")

    monkeypatch.setattr(ingestao_enem, "publicar_silver_enem", publicar_e_falhar)
    with pytest.raises(OSError, match="publicação"):
        ingerir_enem(cfg)

    assert len(manifesto(cfg.dsn)) == 1
    assert enem.hash_da_silver_enem(cfg) is None

    monkeypatch.setattr(ingestao_enem, "publicar_silver_enem", publicar_real)
    recuperada = ingerir_enem(cfg)

    assert recuperada.silver_republicada
    assert len(manifesto(cfg.dsn)) == 1
    assert ingerir_enem(cfg).ignorada
    assert sobras_de_publicacao(cfg) == []


def test_contagens_do_censo_continuam_obrigatorias_para_o_censo(cfg):
    with psycopg.connect(cfg.dsn, autocommit=True) as conexao, pytest.raises(psycopg.errors.CheckViolation):
        conexao.execute(
            "insert into ingestao_execucao (fonte, ano, arquivo_origem, hash_sha256, iniciado_em) "
            "values ('censo', 2024, 'x.csv', repeat('0', 64), now())"
        )
