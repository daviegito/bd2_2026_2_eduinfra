"""Os dois caminhos de eventos numa fonte só (ADR 0008), contra um banco descartável com slot próprio.

O CDC e as janelas gravam na mesma tabela Delta, pela mesma chave: o evento que chega
pelos dois aparece uma vez, e a Gold lê só essa tabela.
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

from eduinfra import cdc, eventos, fluxo_cdc, janelas, modelo_analitico
from eduinfra.configuracao import Configuracao
from tests.test_modelo_analitico import _semear_cadastro, vistoria

pytestmark = pytest.mark.integracao

DSN_ADMIN = os.environ.get("DATABASE_URL")
RAIZ = Path(__file__).resolve().parents[1]
ITENS = {"biblioteca": "existente", "lab_ciencias": "inexistente"}


@pytest.fixture
def ambiente(tmp_path):
    if not DSN_ADMIN:
        pytest.skip("DATABASE_URL não definida")

    sufixo = uuid.uuid4().hex[:10]
    nome, slot = f"eduinfra_fonte_{sufixo}", f"eduinfra_teste_fonte_{sufixo}"
    with psycopg.connect(DSN_ADMIN, autocommit=True) as admin:
        admin.execute(f'create database "{nome}"')

    dsn = urlsplit(DSN_ADMIN)._replace(path=f"/{nome}").geturl()
    os.environ["DATABASE_URL"] = dsn
    try:
        command.upgrade(Config(str(RAIZ / "alembic.ini")), "head")
        _semear_cadastro(dsn)
        # O slot nasce antes de qualquer vistoria: o CDC vê tudo o que as janelas virem.
        with psycopg.connect(dsn, autocommit=True) as conexao:
            cdc.garantir_slot(conexao, slot)
        yield Configuracao(
            dsn=dsn,
            url_censo="https://exemplo.invalido/censo.zip",
            ano_censo=2024,
            data_referencia=date(2024, 5, 29),
            diretorio_dados=tmp_path,
            arquivo_alembic=RAIZ / "alembic.ini",
        ), slot
    finally:
        os.environ["DATABASE_URL"] = DSN_ADMIN
        with psycopg.connect(DSN_ADMIN, autocommit=True) as admin:
            admin.execute(f"select pg_drop_replication_slot(slot_name) from pg_replication_slots where database = '{nome}'")
            admin.execute(f'drop database if exists "{nome}" with (force)')


def na_fonte(configuracao: Configuracao, vistoria_id: int) -> list[tuple]:
    tabela, _ = eventos.ler(configuracao)
    linhas = zip(*(tabela.column(c).to_pylist() for c in ("vistoria_id", "item_codigo", "mes_ocorrencia", "lsn_origem")))

    return sorted((item, mes, lsn is not None) for id_, item, mes, lsn in linhas if id_ == vistoria_id)


def chaves_repetidas(configuracao: Configuracao) -> int:
    tabela, _ = eventos.ler(configuracao)
    chaves = list(zip(tabela.column("vistoria_id").to_pylist(), tabela.column("item_codigo").to_pylist()))

    return len(chaves) - len(set(chaves))


def itens_na_origem(configuracao: Configuracao) -> int:
    with psycopg.connect(configuracao.dsn) as conexao:
        return conexao.execute("select count(*) from vistoria_item").fetchone()[0]


def test_mesmo_evento_pelos_dois_caminhos_aparece_uma_vez(ambiente):
    configuracao, slot = ambiente

    # Janelas primeiro, CDC depois.
    censo = vistoria(configuracao.dsn, 1, "2024-05-29", ITENS, origem="censo_escolar")
    pelas_janelas = janelas.processar(configuracao)
    cdc_depois = fluxo_cdc.passar_uma_vez(configuracao, slot=slot)

    assert pelas_janelas.completados == 2
    assert (cdc_depois.itens_lidos, cdc_depois.itens_inseridos) == (2, 0)
    assert na_fonte(configuracao, censo) == [("biblioteca", "2024-05", False), ("lab_ciencias", "2024-05", False)]

    # CDC primeiro, janelas depois, com um evento atrasado de um mês já fechado.
    atrasada = vistoria(configuracao.dsn, 1, "2026-08-15", ITENS | {"lab_ciencias": "existente"})
    cdc_antes = fluxo_cdc.passar_uma_vez(configuracao, slot=slot)
    janelas_depois = janelas.processar(configuracao)

    assert cdc_antes.itens_inseridos == 2
    assert janelas_depois.completados == 0
    assert na_fonte(configuracao, atrasada) == [("biblioteca", "2026-08", True), ("lab_ciencias", "2026-08", True)]
    assert [e.vistoria_id for e in janelas_depois.eventos if not e.dentro_da_tolerancia] == [atrasada]
    assert janelas._ler_controle(configuracao)["reaberturas"][-1]["mes"] == "2026-08"
    assert all(j.confere_com_a_origem for j in janelas_depois.janelas)

    # Reprocessar a janela pelos dois caminhos de novo não muda nada.
    assert janelas.reprocessar_janela(configuracao, "2026-08") == janelas.reprocessar_janela(configuracao, "2026-08")
    assert fluxo_cdc.passar_uma_vez(configuracao, slot=slot).itens_inseridos == 0

    assert chaves_repetidas(configuracao) == 0
    assert eventos.ler(configuracao)[0].num_rows == itens_na_origem(configuracao) == 4


def test_gold_construida_da_fonte_unica_confere_com_a_origem(ambiente):
    configuracao, slot = ambiente
    vistoria(configuracao.dsn, 1, "2024-05-29", ITENS, origem="censo_escolar")
    vistoria(configuracao.dsn, 2, "2024-05-29", ITENS, origem="censo_escolar")
    janelas.processar(configuracao)
    vistoria(configuracao.dsn, 1, "2026-08-15", ITENS | {"lab_ciencias": "existente"})
    fluxo_cdc.passar_uma_vez(configuracao, slot=slot)

    resumo = modelo_analitico.construir(configuracao)

    assert resumo.fonte.confere
    assert resumo.fonte.na_fonte == resumo.fonte.na_origem == itens_na_origem(configuracao) == 6
    assert resumo.linhas["fato_vistoria_item"] == 6
