"""Frescor da Gold contra o banco (issue #52). Exige a Gold construída.

Insere vistorias de teste na origem, que é insert-only: elas ficam no banco, marcadas
na observação, e ficam fora da Gold até a próxima construção. Rode contra um banco de
desenvolvimento.
"""

import os
import time
from datetime import timedelta

import psycopg
import pytest

from eduinfra import cli, frescor, vistorias_de_teste

pytestmark = pytest.mark.integracao

MATRICULA = "TESTE-E3-52"
OBSERVACAO = "Teste da E3 (issue #52): vistoria fora da Gold"
SLA_LONGO = timedelta(days=36500)


@pytest.fixture(scope="module")
def dsn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL não definida")

    problema = None
    try:
        with psycopg.connect(url, connect_timeout=5) as conexao:
            construida = conexao.execute("select to_regclass('gold.fato_vistoria_item') is not null").fetchone()[0]
    except psycopg.Error as erro:
        problema = f"não foi possível consultar o banco em DATABASE_URL ({str(erro).strip()})"

    # Fora do except: a mensagem sai sozinha, sem o rastro da exceção original.
    if problema:
        pytest.fail(problema, pytrace=False)
    if not construida:
        pytest.skip("Gold não construída: rode `construir-gold` antes")

    return url


def inserir_vistoria(dsn: str) -> None:
    with psycopg.connect(dsn) as conexao:
        vistorias_de_teste.inserir_vistorias(conexao, 1, MATRICULA, OBSERVACAO)
    time.sleep(0.05)  # o frescor da vistoria recém-inserida fica acima de um SLA zero


def ultima_medicao(dsn: str) -> tuple:
    with psycopg.connect(dsn) as conexao:
        return conexao.execute(
            """
            select comando, tabela, vistorias_pendentes, frescor_s, sla_s, dentro_do_sla
            from monitoramento.frescor_gold order by id desc limit 1
            """
        ).fetchone()


def test_medicao_fica_registrada_e_consultavel(dsn):
    medicao = frescor.medir(dsn, SLA_LONGO, comando="teste")

    assert ultima_medicao(dsn) == (
        "teste",
        frescor.TABELA_MONITORADA,
        medicao.vistorias_pendentes,
        medicao.frescor.total_seconds(),
        SLA_LONGO.total_seconds(),
        True,
    )


def test_vistoria_nova_fica_pendente_ate_a_proxima_construcao(dsn):
    antes = frescor.medir(dsn, SLA_LONGO, comando="teste")

    inserir_vistoria(dsn)
    depois = frescor.medir(dsn, timedelta(0), comando="teste")

    assert depois.vistorias_pendentes == antes.vistorias_pendentes + 1
    assert depois.frescor > timedelta(0)
    assert not depois.dentro_do_sla
    assert ultima_medicao(dsn)[-1] is False


def test_medir_frescor_falha_quando_o_sla_e_violado(dsn):
    inserir_vistoria(dsn)

    assert cli.main(["medir-frescor", "--sla-horas", "0"]) == 1
    assert cli.main(["medir-frescor", "--sla-horas", str(SLA_LONGO.total_seconds() / 3600)]) == 0
