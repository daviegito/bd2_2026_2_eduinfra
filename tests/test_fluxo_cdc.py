from datetime import datetime, timezone

import psycopg
import pytest

from eduinfra import eventos, fluxo_cdc
from eduinfra.pgoutput import OPERACAO_DELETE, OPERACAO_INSERT, OPERACAO_TRUNCATE, OPERACAO_UPDATE, Alteracao, Insercao, Transacao

INSTANTE = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
LSN = 500
CABECALHO = (1, 53002580, "DF", "vistoria_presencial", INSTANTE, INSTANTE)


def _item(**ajustes) -> Insercao:
    valores = {"vistoria_id": "1", "item_codigo": "quadra", "situacao": "existente", "quantidade": None} | ajustes
    return Insercao("vistoria_item", valores)


def _transacao(*insercoes, alteracoes=()) -> Transacao:
    return Transacao(1, LSN, INSTANTE, tuple(insercoes), tuple(alteracoes))


def _separar(*itens, cabecalhos=None, falhas=None):
    transacao = _transacao(*itens)
    pares = [(transacao, item) for item in itens]
    return fluxo_cdc.separar_itens(pares, {1: CABECALHO} if cabecalhos is None else cabecalhos, falhas or {})


def test_item_valido_vira_evento_sem_rejeicao():
    aprovados, rejeicoes = _separar(_item(quantidade="3"))

    assert rejeicoes == []
    assert aprovados[0][2]["quantidade"] == 3
    assert aprovados[0][2]["mes_ocorrencia"] == "2026-10"
    assert aprovados[0][2]["lsn_origem"] == LSN


@pytest.mark.parametrize(
    ("item", "trecho_do_motivo"),
    [
        (Insercao("vistoria_item", {"vistoria_id": "1", "item_codigo": "quadra", "situacao": "existente"}), "KeyError"),
        (_item(quantidade="muitos"), "ValueError"),
        (_item(situacao=None), "obrigatória"),
    ],
    ids=["coluna-renomeada", "tipo-incompativel", "nulo-em-obrigatoria"],
)
def test_item_que_nao_converte_vai_para_a_fila_e_o_vizinho_segue(item, trecho_do_motivo):
    aprovados, rejeicoes = _separar(_item(item_codigo="biblioteca"), item)

    assert [par[2]["item_codigo"] for par in aprovados] == ["biblioteca"]
    (rejeicao,) = rejeicoes
    assert trecho_do_motivo in rejeicao["motivo"]
    assert rejeicao["operacao"] == OPERACAO_INSERT
    assert rejeicao["lsn_origem"] == LSN


def test_item_sem_vistoria_legivel_e_rejeitado_com_a_chave_que_der():
    item = Insercao("vistoria_item", {"id_vistoria": "1", "item_codigo": "quadra"})

    _, (rejeicao,) = _separar(item)

    assert rejeicao["vistoria_id"] is None
    assert rejeicao["item_codigo"] == "quadra"


def test_item_de_vistoria_com_cabecalho_ilegivel_guarda_o_erro_do_banco():
    falha = psycopg.DataError("timestamp too small (before year 1)")

    aprovados, (rejeicao,) = _separar(_item(), cabecalhos={}, falhas={1: falha})

    assert aprovados == []
    assert "DataError" in rejeicao["motivo"]
    assert "too small" in rejeicao["motivo"]
    assert rejeicao["vistoria_id"] == 1


def test_item_sem_cabecalho_e_rejeitado():
    aprovados, (rejeicao,) = _separar(_item(), cabecalhos={})

    assert aprovados == []
    assert "KeyError" in rejeicao["motivo"]


def test_lote_que_estoura_o_tipo_do_arrow_isola_so_a_linha_ruim():
    aprovados, _ = _separar(_item(item_codigo="biblioteca"), _item(item_codigo="quadra", quantidade=str(2**40)))

    tabela, rejeicoes = fluxo_cdc.montar_tabela(aprovados)

    assert tabela.column("item_codigo").to_pylist() == ["biblioteca"]
    assert tabela.schema.equals(eventos.ESQUEMA)
    (rejeicao,) = rejeicoes
    assert rejeicao["item_codigo"] == "quadra"


def test_lote_sem_linha_ruim_vira_uma_tabela_so():
    aprovados, _ = _separar(_item(item_codigo="biblioteca"), _item(item_codigo="quadra"))

    tabela, rejeicoes = fluxo_cdc.montar_tabela(aprovados)

    assert tabela.num_rows == 2
    assert rejeicoes == []


@pytest.mark.parametrize(
    ("alteracao", "vistoria_id", "item_codigo"),
    [
        (Alteracao(OPERACAO_UPDATE, "vistoria_item", {"vistoria_id": "9", "item_codigo": "quadra"}), 9, "quadra"),
        (Alteracao(OPERACAO_DELETE, "vistoria", {"id": "7", "observacao": "texto livre"}), 7, None),
        (Alteracao(OPERACAO_TRUNCATE, "vistoria_item", {}), None, None),
    ],
    ids=["update-de-item", "delete-de-vistoria", "truncate"],
)
def test_desvio_do_insert_only_vai_para_a_fila_so_com_a_chave(alteracao, vistoria_id, item_codigo):
    (linha,) = fluxo_cdc.violacoes_do_insert_only([_transacao(alteracoes=[alteracao])])

    assert (linha["operacao"], linha["vistoria_id"], linha["item_codigo"]) == (alteracao.operacao, vistoria_id, item_codigo)
    assert "texto livre" not in str(linha)


def test_itens_repetidos_descontam_os_que_foram_para_a_fila():
    resultado = fluxo_cdc.ResultadoDaPassada(1, 1, itens_lidos=17, itens_inseridos=10, lsn_confirmado=LSN, itens_rejeitados=2)

    assert resultado.itens_repetidos == 5
