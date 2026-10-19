"""CDC de ponta a ponta contra o PostgreSQL de origem, com slot e tabela Delta descartáveis.

A origem é insert-only: cada teste deixa vistorias marcadas na observação.
"""

import os
from dataclasses import replace

import psycopg
import pytest

from eduinfra import cdc, eventos, fluxo_cdc
from eduinfra.configuracao import carregar_configuracao
from eduinfra.vistorias_de_teste import inserir_vistorias

pytestmark = pytest.mark.integracao

ITENS_POR_VISTORIA = 17
SLOT_DE_TESTE = "eduinfra_teste_integracao"
MATRICULA = "TESTE-E2-26"
OBSERVACAO = "Teste de integração do CDC"


@pytest.fixture
def configuracao(tmp_path):
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL não definida")

    return replace(carregar_configuracao(), diretorio_dados=tmp_path)


@pytest.fixture
def conexao(configuracao):
    with psycopg.connect(configuracao.dsn, autocommit=True) as aberta:
        cdc.remover_slot(aberta, SLOT_DE_TESTE)
        cdc.garantir_slot(aberta, SLOT_DE_TESTE)
        yield aberta
        cdc.remover_slot(aberta, SLOT_DE_TESTE)


def test_vistoria_nova_chega_a_tabela_analitica(configuracao, conexao):
    inserir_vistorias(conexao, 1, MATRICULA, OBSERVACAO)

    resultado = fluxo_cdc.passar_uma_vez(configuracao, slot=SLOT_DE_TESTE)

    assert resultado.itens_inseridos == ITENS_POR_VISTORIA
    assert eventos.contar(configuracao) == ITENS_POR_VISTORIA


def test_passada_sem_pendencia_nao_grava_nada(configuracao, conexao):
    resultado = fluxo_cdc.passar_uma_vez(configuracao, slot=SLOT_DE_TESTE)

    assert (resultado.transacoes, resultado.itens_inseridos) == (0, 0)


def test_falha_antes_de_confirmar_reentrega_sem_duplicar(configuracao, conexao):
    inserir_vistorias(conexao, 1, MATRICULA, OBSERVACAO)

    with pytest.raises(fluxo_cdc.FalhaSimulada):
        fluxo_cdc.passar_uma_vez(configuracao, falhar_antes_de_confirmar=True, slot=SLOT_DE_TESTE)
    retomada = fluxo_cdc.passar_uma_vez(configuracao, slot=SLOT_DE_TESTE)

    assert retomada.itens_lidos == ITENS_POR_VISTORIA
    assert retomada.itens_inseridos == 0
    assert eventos.contar(configuracao) == ITENS_POR_VISTORIA


def test_consumidor_parado_entrega_o_acumulado_ao_religar(configuracao, conexao):
    inserir_vistorias(conexao, 2, MATRICULA, OBSERVACAO)
    inserir_vistorias(conexao, 3, MATRICULA, OBSERVACAO)

    resultado = fluxo_cdc.passar_uma_vez(configuracao, slot=SLOT_DE_TESTE)

    assert resultado.transacoes == 2
    assert eventos.contar(configuracao) == 5 * ITENS_POR_VISTORIA
