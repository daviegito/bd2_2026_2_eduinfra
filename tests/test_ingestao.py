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

from eduinfra import ingestao, transformacao
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


def escrever_censo(
    pasta: Path,
    colunas: list[str],
    ano: int = 2024,
    nome_escola_um: str = "ESCOLA UM",
    nome_municipio_df: str = "Brasília",
) -> Path:
    cabecalho = ["NU_ANO_CENSO", "CO_UF", "SG_UF", "NO_MUNICIPIO", "CO_MUNICIPIO", "NO_ENTIDADE",
                 "CO_ENTIDADE", "TP_DEPENDENCIA", "TP_LOCALIZACAO", "TP_SITUACAO_FUNCIONAMENTO", *colunas]
    linhas = [";".join(cabecalho)]
    for codigo, nome, municipio, nome_mun, dependencia, localizacao in ESCOLAS:
        nome = nome_escola_um if codigo == 53000001 else nome
        nome_mun = nome_municipio_df if municipio == 5300108 else nome_mun
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


def ler_silver(cfg: Configuracao, tabela: str, coluna: str) -> set[str]:
    arquivo = transformacao.particao_silver(cfg) / f"{tabela}.parquet"
    consulta = f"select {coluna} from read_parquet('{arquivo.as_posix()}', hive_partitioning=false)"

    return {linha[0] for linha in duckdb.connect().execute(consulta).fetchall()}


def marcador_da_silver(cfg: Configuracao) -> str | None:
    arquivo = transformacao.particao_silver(cfg) / transformacao.MARCADOR_ORIGEM

    return arquivo.read_text().strip() if arquivo.exists() else None


def sobras_de_publicacao(cfg: Configuracao) -> list[str]:
    area = transformacao.area_de_publicacao(cfg)

    return sorted(p.name for p in area.iterdir()) if area.exists() else []


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
    assert not transformacao.particao_silver(cfg).exists()
    assert sobras_de_publicacao(cfg) == []

    monkeypatch.setattr(ingestao, "_consolidar", consolidar_real)
    refeita = ingerir(cfg)

    assert not refeita.ignorada
    assert contagens(cfg.dsn)["escola"] == 3


def test_parquet_e_gravado_sem_arquivo_temporario_sobrando(cfg, colunas_do_catalogo):
    escrever_censo(cfg.diretorio_dados, colunas_do_catalogo)
    ingerir(cfg)

    particao = cfg.diretorio_dados / "silver" / "ano_censo=2024"

    assert sorted(p.name for p in particao.iterdir()) == [
        "escola.parquet",
        "municipio.parquet",
        "origem.sha256",
        "vistoria_item.parquet",
    ]
    assert sobras_de_publicacao(cfg) == []


def hash_de(arquivo: Path) -> str:
    return ingestao.hash_do_arquivo(arquivo)


def test_falha_durante_a_troca_da_silver_nao_mistura_versoes(cfg, colunas_do_catalogo, monkeypatch):
    a = escrever_censo(cfg.diretorio_dados, colunas_do_catalogo)
    hash_a = hash_de(a)
    ingerir(cfg)

    b = escrever_censo(
        cfg.diretorio_dados, colunas_do_catalogo, nome_escola_um="ESCOLA UM B", nome_municipio_df="Brasília B"
    )
    hash_b = hash_de(b)

    renomeacoes = []
    rename_real = transformacao.os.rename

    def rename_que_falha_na_segunda(origem, destino):
        renomeacoes.append((origem, destino))
        if len(renomeacoes) == 2:
            raise OSError("falha simulada na segunda troca")
        return rename_real(origem, destino)

    monkeypatch.setattr(transformacao.os, "rename", rename_que_falha_na_segunda)

    with pytest.raises(OSError, match="segunda troca"):
        ingerir(cfg)

    # O banco foi confirmado ANTES da publicação: o manifesto já registra B, mesmo com a Silver falhando.
    assert contagens(cfg.dsn)["ingestao_execucao"] == 2

    # Município e escola continuam da MESMA versão (A): a referência entre eles não quebra.
    assert ler_silver(cfg, "municipio", "no_municipio") == {"Brasília", "Goiânia"}
    assert "ESCOLA UM" in ler_silver(cfg, "escola", "no_entidade")
    assert "ESCOLA UM B" not in ler_silver(cfg, "escola", "no_entidade")
    assert marcador_da_silver(cfg) == hash_a
    assert sobras_de_publicacao(cfg) == []

    # O banco já tinha confirmado a versão B: o manifesto registra B e a Silver ficou para trás.
    monkeypatch.setattr(transformacao.os, "rename", rename_real)
    recuperada = ingerir(cfg)

    assert recuperada.silver_republicada
    assert recuperada.carga is None
    assert ler_silver(cfg, "municipio", "no_municipio") == {"Brasília B", "Goiânia"}
    assert "ESCOLA UM B" in ler_silver(cfg, "escola", "no_entidade")
    assert marcador_da_silver(cfg) == hash_b
    assert contagens(cfg.dsn)["ingestao_execucao"] == 2
    assert sobras_de_publicacao(cfg) == []


def test_interrupcao_antes_do_commit_nao_publica_a_silver(cfg, colunas_do_catalogo, monkeypatch):
    a = escrever_censo(cfg.diretorio_dados, colunas_do_catalogo)
    hash_a = hash_de(a)
    ingerir(cfg)
    banco_antes = contagens(cfg.dsn), impressao_do_oltp(cfg.dsn)

    escrever_censo(cfg.diretorio_dados, colunas_do_catalogo, nome_escola_um="ESCOLA UM B", nome_municipio_df="Brasília B")
    registrar_real = ingestao._registrar

    def registrar_e_interromper(*argumentos, **nomeados):
        registrar_real(*argumentos, **nomeados)
        raise RuntimeError("interrupção simulada depois do INSERT no manifesto, antes do commit")

    monkeypatch.setattr(ingestao, "_registrar", registrar_e_interromper)

    with pytest.raises(RuntimeError, match="antes do commit"):
        ingerir(cfg)

    # Rollback real preservou o banco e o manifesto...
    assert (contagens(cfg.dsn), impressao_do_oltp(cfg.dsn)) == banco_antes
    # ...e a Silver publicada continua a de antes: a nova nunca foi publicada.
    assert marcador_da_silver(cfg) == hash_a
    assert ler_silver(cfg, "municipio", "no_municipio") == {"Brasília", "Goiânia"}
    assert "ESCOLA UM B" not in ler_silver(cfg, "escola", "no_entidade")
    assert sobras_de_publicacao(cfg) == []


def test_voltar_ao_arquivo_anterior_republica_a_silver(cfg, colunas_do_catalogo):
    a = escrever_censo(cfg.diretorio_dados, colunas_do_catalogo)
    hash_a = hash_de(a)
    ingerir(cfg)
    apos_a = impressao_do_oltp(cfg.dsn)

    escrever_censo(cfg.diretorio_dados, colunas_do_catalogo, nome_escola_um="ESCOLA UM B", nome_municipio_df="Brasília B")
    ingerir(cfg)
    assert "ESCOLA UM B" in ler_silver(cfg, "escola", "no_entidade")

    # A -> B -> A: a última execução NÃO pode ser ignorada, porque a versão vigente é B.
    escrever_censo(cfg.diretorio_dados, colunas_do_catalogo)
    volta = ingerir(cfg)

    assert not volta.ignorada
    assert marcador_da_silver(cfg) == hash_a
    assert ler_silver(cfg, "municipio", "no_municipio") == {"Brasília", "Goiânia"}
    assert "ESCOLA UM" in ler_silver(cfg, "escola", "no_entidade")
    assert "ESCOLA UM B" not in ler_silver(cfg, "escola", "no_entidade")
    assert impressao_do_oltp(cfg.dsn) == apos_a
    assert contagens(cfg.dsn)["ingestao_execucao"] == 3

    # Agora sim A é a versão vigente no manifesto e na Silver: repetir é ignorado.
    assert ingerir(cfg).ignorada


def test_queda_entre_as_duas_trocas_e_recuperada_na_proxima_execucao(cfg, colunas_do_catalogo):
    escrever_censo(cfg.diretorio_dados, colunas_do_catalogo)
    ingerir(cfg)

    # Estado deixado por um processo morto entre os dois renames: a cópia antiga existe e a partição não.
    antigo = transformacao.area_de_publicacao(cfg) / f"antigo-ano_censo={cfg.ano_censo}"
    antigo.parent.mkdir(parents=True, exist_ok=True)
    transformacao.particao_silver(cfg).rename(antigo)

    reexecucao = ingerir(cfg)

    assert reexecucao.ignorada
    assert transformacao.particao_silver(cfg).exists()
    assert ler_silver(cfg, "municipio", "no_municipio") == {"Brasília", "Goiânia"}
    assert sobras_de_publicacao(cfg) == []
