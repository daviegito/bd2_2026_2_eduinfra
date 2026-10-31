"""Medição de latência contra o PostgreSQL de origem, com slot e tabela Delta descartáveis.

A origem é insert-only: cada teste deixa vistorias marcadas na observação.
"""

import os
import threading
from dataclasses import replace

import psycopg
import pytest
from deltalake.exceptions import CommitFailedError

from eduinfra import cdc, comparacao_latencia, eventos, fluxo_cdc
from eduinfra.configuracao import carregar_configuracao
from eduinfra.vistorias_de_teste import contar_itens_por_vistoria

pytestmark = pytest.mark.integracao

SLOT_DE_TESTE = "eduinfra_teste_latencia"
REPETICOES = 2
ESPERA_CURTA_S = 1.0
INTERVALO_DO_CONSUMIDOR_DE_TESTE_S = 0.2
CENARIOS = 2


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


@pytest.fixture
def consumidor(configuracao, conexao):
    parar = threading.Event()

    def laco():
        while not parar.wait(INTERVALO_DO_CONSUMIDOR_DE_TESTE_S):
            try:
                fluxo_cdc.passar_uma_vez(configuracao, slot=SLOT_DE_TESTE)
            except CommitFailedError:
                continue

    thread = threading.Thread(target=laco)
    thread.start()
    yield
    parar.set()
    thread.join()


def test_latencia_do_cdc_cobre_os_dois_cenarios_e_entrega_as_vistorias(configuracao, conexao, consumidor):
    medidas = comparacao_latencia.medir_cdc(configuracao, REPETICOES)

    assert [medida.tempos.amostras for medida in medidas] == [REPETICOES] * CENARIOS
    assert all(medida.tempos.mediana_s > 0 for medida in medidas)
    assert eventos.contar(configuracao) == CENARIOS * REPETICOES * contar_itens_por_vistoria(conexao)


def test_medir_cdc_sem_consumidor_avisa_em_vez_de_esperar_para_sempre(configuracao, conexao):
    with pytest.raises(comparacao_latencia.ConsumidorParado, match="serviço cdc"):
        comparacao_latencia.medir_cdc(configuracao, REPETICOES, espera_maxima_s=ESPERA_CURTA_S)


def test_recarga_grava_cada_ciclo_e_mede_o_wal_do_slot_parado(configuracao, conexao):
    medida = comparacao_latencia.medir_recarga(configuracao, REPETICOES, slot=SLOT_DE_TESTE)

    assert medida.execucao.amostras == REPETICOES
    assert medida.wal.bytes_por_vistoria > 0
    assert eventos.contar(configuracao) == REPETICOES * contar_itens_por_vistoria(conexao)


def test_recarga_com_consumidor_em_execucao_invalida_a_medicao(configuracao, conexao, consumidor):
    with pytest.raises(comparacao_latencia.ConsumidorEmExecucao):
        comparacao_latencia.medir_recarga(configuracao, REPETICOES, slot=SLOT_DE_TESTE)
