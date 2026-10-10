"""Ingestão em lote idempotente (E2-2), contra um banco descartável criado por teste.

Cada módulo cria o próprio banco, migra até a última revisão e o remove no fim:
como vistoria é insert-only, não há como "limpar" linhas num banco compartilhado.
"""

import hashlib
import os
import uuid
from datetime import date
from pathlib import Path

import duckdb
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from urllib.parse import urlsplit

from eduinfra import ingestao
from eduinfra.configuracao import Configuracao
from eduinfra.ingestao import ingerir

pytestmark = pytest.mark.integracao

DSN_ADMIN = os.environ.get("DATABASE_URL")
RAIZ = Path(__file__).resolve().parents[1]

ESCOLAS = [
    (53000001, "ESCOLA UM", 5300108, "Brasília", 2, 1),
    (53000002, "ESCOLA DOIS", 5300108, "Brasília", 3, 2),
    (53000003, "ESCOLA TRES", 5208707, "Goiânia", 4, 1),
]


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


@pytest.fixture(scope="module")
def colunas_do_catalogo(dsn_teste) -> list[str]:
    with psycopg.connect(dsn_teste) as conexao:
        return [l[0] for l in conexao.execute("select coluna_censo from item_infraestrutura order by codigo")]


def escrever_censo(pasta: Path, colunas: list[str], ano: int = 2024, nome_escola_um: str = "ESCOLA UM") -> Path:
    cabecalho = ["NU_ANO_CENSO", "CO_UF", "SG_UF", "NO_MUNICIPIO", "CO_MUNICIPIO", "NO_ENTIDADE",
                 "CO_ENTIDADE", "TP_DEPENDENCIA", "TP_LOCALIZACAO", "TP_SITUACAO_FUNCIONAMENTO", *colunas]
    linhas = [";".join(cabecalho)]
    for codigo, nome, municipio, nome_mun, dependencia, localizacao in ESCOLAS:
        nome = nome_escola_um if codigo == 53000001 else nome
        fixos = [str(ano), "53", "DF", nome_mun, str(municipio), nome, str(codigo),
                 str(dependencia), str(localizacao), "1"]
        linhas.append(";".join([*fixos, *["1"] * len(colunas)]))

    destino = pasta / "bruto"
    destino.mkdir(parents=True, exist_ok=True)
    arquivo = destino / f"microdados_ed_basica_{ano}.csv"
    arquivo.write_text("\n".join(linhas), encoding="latin-1")

    return arquivo


@pytest.fixture
def cfg(dsn_teste, tmp_path) -> Configuracao:
    return Configuracao(
        dsn=dsn_teste,
        url_censo="https://exemplo.invalido/censo.zip",
        ano_censo=2024,
        data_referencia=date(2024, 5, 29),
        diretorio_dados=tmp_path / "dados",
        arquivo_alembic=RAIZ / "alembic.ini",
    )


def contagens(dsn: str) -> dict[str, int]:
    tabelas = ("municipio", "escola", "vistoria", "vistoria_item", "ingestao_execucao")
    with psycopg.connect(dsn) as conexao:
        return {t: conexao.execute(f"select count(*) from {t}").fetchone()[0] for t in tabelas}


def impressao_do_oltp(dsn: str) -> str:
    """Hash do conteúdo das tabelas de dado, ignorando ids e carimbos gerados."""
    with psycopg.connect(dsn) as conexao:
        linhas = conexao.execute(
            """
            select e.co_entidade, e.no_entidade, i.item_codigo, i.situacao
            from escola e
            join vistoria v on v.co_entidade = e.co_entidade
            join vistoria_item i on i.vistoria_id = v.id
            order by 1, 3
            """
        ).fetchall()

    return hashlib.sha256(repr(linhas).encode()).hexdigest()


def limpar_oltp(dsn: str) -> None:
    # insert-only bloqueia DELETE e TRUNCATE; o banco é descartável, então
    # desligamos os gatilhos de sessão só para reiniciar entre testes.
    with psycopg.connect(dsn, autocommit=True) as conexao:
        conexao.execute("set session_replication_role = replica")
        conexao.execute("truncate vistoria_item, vistoria, escola, municipio, ingestao_execucao restart identity cascade")


@pytest.fixture(autouse=True)
def banco_zerado(dsn_teste):
    limpar_oltp(dsn_teste)
    yield


def test_duas_execucoes_seguidas_produzem_as_mesmas_linhas(cfg, colunas_do_catalogo):
    escrever_censo(cfg.diretorio_dados, colunas_do_catalogo)

    primeira = ingerir(cfg)
    antes = contagens(cfg.dsn), impressao_do_oltp(cfg.dsn)
    segunda = ingerir(cfg)
    depois = contagens(cfg.dsn), impressao_do_oltp(cfg.dsn)

    assert not primeira.ignorada
    assert primeira.carga.escolas == 3
    assert segunda.ignorada
    assert antes == depois
    assert antes[0]["ingestao_execucao"] == 1


def test_forcar_reexecuta_sem_duplicar(cfg, colunas_do_catalogo):
    escrever_censo(cfg.diretorio_dados, colunas_do_catalogo)

    ingerir(cfg)
    antes = impressao_do_oltp(cfg.dsn)
    forcada = ingerir(cfg, forcar=True)

    assert not forcada.ignorada
    assert (forcada.carga.escolas, forcada.carga.vistorias, forcada.carga.itens) == (0, 0, 0)
    assert impressao_do_oltp(cfg.dsn) == antes
    assert contagens(cfg.dsn)["ingestao_execucao"] == 2


def test_arquivo_corrigido_troca_so_a_particao_do_ano(cfg, colunas_do_catalogo):
    vizinha = cfg.diretorio_dados / "silver" / "ano_censo=2023"
    vizinha.mkdir(parents=True)
    (vizinha / "escola.parquet").write_bytes(b"particao de outro ano")

    escrever_censo(cfg.diretorio_dados, colunas_do_catalogo)
    ingerir(cfg)

    escrever_censo(cfg.diretorio_dados, colunas_do_catalogo, nome_escola_um="ESCOLA UM CORRIGIDA")
    corrigida = ingerir(cfg)

    silver = cfg.diretorio_dados / "silver" / "ano_censo=2024" / "escola.parquet"
    nomes = {l[0] for l in duckdb.connect().execute(f"select no_entidade from read_parquet('{silver.as_posix()}')").fetchall()}

    assert not corrigida.ignorada
    assert "ESCOLA UM CORRIGIDA" in nomes
    assert (vizinha / "escola.parquet").read_bytes() == b"particao de outro ano"
    # O passado do OLTP não é reescrito: a correção não inseriu linhas nem alterou o nome.
    assert corrigida.carga.vistorias == 0
    with psycopg.connect(cfg.dsn) as conexao:
        assert conexao.execute("select no_entidade from escola where co_entidade = 53000001").fetchone()[0] == "ESCOLA UM"
    assert contagens(cfg.dsn)["ingestao_execucao"] == 2


def test_falha_no_meio_nao_deixa_estado_parcial(cfg, colunas_do_catalogo, monkeypatch):
    escrever_censo(cfg.diretorio_dados, colunas_do_catalogo)
    consolidar_real = ingestao._consolidar

    def consolidar_e_falhar(*argumentos, **nomeados):
        consolidar_real(*argumentos, **nomeados)
        raise RuntimeError("falha simulada depois de inserir as linhas")

    monkeypatch.setattr(ingestao, "_consolidar", consolidar_e_falhar)

    with pytest.raises(RuntimeError, match="falha simulada"):
        ingerir(cfg)

    assert contagens(cfg.dsn) == {
        "municipio": 0, "escola": 0, "vistoria": 0, "vistoria_item": 0, "ingestao_execucao": 0,
    }

    monkeypatch.setattr(ingestao, "_consolidar", consolidar_real)
    refeita = ingerir(cfg)

    assert not refeita.ignorada
    assert contagens(cfg.dsn)["escola"] == 3


def test_parquet_e_gravado_sem_arquivo_temporario_sobrando(cfg, colunas_do_catalogo):
    escrever_censo(cfg.diretorio_dados, colunas_do_catalogo)
    ingerir(cfg)

    particao = cfg.diretorio_dados / "silver" / "ano_censo=2024"

    assert sorted(p.name for p in particao.iterdir()) == ["escola.parquet", "municipio.parquet", "vistoria_item.parquet"]
