"""Dado atrasado atravessando a origem até a janela certa (issue #27). Exige banco carregado.

Insere vistorias de teste na origem, que é insert-only: elas ficam no banco,
marcadas na observação. Rode contra um banco de desenvolvimento.
"""

import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest

from eduinfra import janelas
from eduinfra.configuracao import carregar_configuracao
from eduinfra.demonstracao_atraso import _escola_de_exemplo, _inserir_vistoria, _tecnico_da_demonstracao

pytestmark = pytest.mark.integracao


@pytest.fixture(scope="module")
def configuracao(tmp_path_factory):
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL não definida")

    base = carregar_configuracao()
    problema = None
    try:
        with psycopg.connect(base.dsn, connect_timeout=5) as conexao:
            carregado = conexao.execute("select exists (select 1 from vistoria)").fetchone()[0]
    except psycopg.ProgrammingError as erro:
        problema = (
            f"DATABASE_URL não é uma URL de conexão válida ({str(erro).strip()}). Carregue o .env "
            "(`set -a && source .env && set +a`) ou exporte a URL completa, "
            "como postgresql://eduinfra:<senha>@localhost:5432/eduinfra"
        )
    except psycopg.OperationalError as erro:
        problema = (
            f"não foi possível conectar ao banco em DATABASE_URL ({str(erro).strip()}). "
            "Suba o banco com `docker compose up -d banco` e confira a porta do .env"
        )

    # Fora do except: a mensagem sai sozinha, sem o rastro da exceção original.
    if problema:
        pytest.fail(problema, pytrace=False)
    if not carregado:
        pytest.skip("banco sem carga: rode `docker compose up` antes")

    configuracao = replace(base, diretorio_dados=Path(tmp_path_factory.mktemp("dados")))
    janelas.processar(configuracao)  # estado de partida: todas as janelas existentes

    return configuracao


@pytest.fixture(scope="module")
def escola_e_tecnico(configuracao):
    with psycopg.connect(configuracao.dsn) as conexao:
        return _escola_de_exemplo(conexao), _tecnico_da_demonstracao(conexao)


def agora(configuracao):
    with psycopg.connect(configuracao.dsn) as conexao:
        return conexao.execute("select now()").fetchone()[0]


def test_vistoria_atrasada_cai_no_mes_em_que_ocorreu(configuracao, escola_e_tecnico):
    ocorrido = agora(configuracao) - timedelta(days=3)
    vistoria = _inserir_vistoria(configuracao, *escola_e_tecnico, ocorrido)

    relatorio = janelas.processar(configuracao)

    evento = next(e for e in relatorio.eventos if e.vistoria_id == vistoria)
    assert evento.mes == janelas.mes_de(ocorrido)
    assert janelas.mes_de(ocorrido) in {j.mes for j in relatorio.janelas}


def test_segunda_execucao_nao_reprocessa_o_que_ja_entrou(configuracao):
    relatorio = janelas.processar(configuracao)

    assert relatorio.eventos == []
    assert relatorio.janelas == []


def test_janela_fechada_e_reaberta_e_registrada(configuracao, escola_e_tecnico):
    ocorrido = janelas.limites("2026-08")[0] + timedelta(days=14)
    _inserir_vistoria(configuracao, *escola_e_tecnico, ocorrido)

    relatorio = janelas.processar(configuracao)

    assert any(not e.dentro_da_tolerancia for e in relatorio.eventos)
    controle = janelas._ler_controle(janelas.pasta_eventos(configuracao))
    assert controle["reaberturas"][-1]["mes"] == "2026-08"


def test_reprocessar_a_janela_duas_vezes_da_o_mesmo_resultado(configuracao):
    mes = janelas.mes_de(agora(configuracao) - timedelta(days=3))

    assert janelas.reprocessar_janela(configuracao, mes) == janelas.reprocessar_janela(configuracao, mes)


def test_janelas_somam_o_mesmo_que_a_origem(configuracao):
    janelas.processar(configuracao)
    with psycopg.connect(configuracao.dsn) as conexao:
        na_origem = conexao.execute("select count(*) from vistoria_item").fetchone()[0]

    assert sum(j.linhas for j in janelas.ler_janelas(configuracao).values()) == na_origem


def test_vistoria_sem_item_nao_volta_como_nova(configuracao, escola_e_tecnico):
    escola, tecnico = escola_e_tecnico
    with psycopg.connect(configuracao.dsn) as conexao:
        vistoria = conexao.execute(
            """
            insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em, observacao)
            values (%s, %s, 'vistoria_presencial', now() - interval '1 day', 'Teste das janelas: vistoria sem item')
            returning id
            """,
            (escola, tecnico),
        ).fetchone()[0]

    primeira = janelas.processar(configuracao)
    segunda = janelas.processar(configuracao)

    assert vistoria not in {e.vistoria_id for e in primeira.eventos}
    assert segunda.eventos == []
