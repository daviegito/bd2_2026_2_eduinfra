"""Contrato do recibo: identificação sem texto livre e identidade por ocorrência."""

from datetime import datetime, timedelta, timezone

import pytest

from eduinfra import recuperacao_cdc as recuperacao


@pytest.mark.parametrize("responsavel", [None, "", "Pedro", "pedro@email.com", "operador-1", "operador-01\nnome"])
def test_recusa_identificacao_pessoal_ou_fora_do_padrao(responsavel):
    with pytest.raises(ValueError, match="identificador operacional"):
        recuperacao.validar_operador(responsavel, "desvio_revisado")


@pytest.mark.parametrize("motivo", [None, "", "corrigido por Pedro", "nome@email.com"])
def test_recusa_motivo_livre(motivo):
    with pytest.raises(ValueError, match="motivo padronizado"):
        recuperacao.validar_operador("operador-01", motivo)


def test_identidade_inclui_lsn_para_nao_tratar_ocorrencias_futuras():
    assert recuperacao.identidade({"lsn_origem": 1, "chave": "mesmo-item"}) != recuperacao.identidade({"lsn_origem": 2, "chave": "mesmo-item"})


def test_impressao_independe_da_ordem_dos_campos_e_normaliza_fuso():
    data = datetime(2024, 5, 29, tzinfo=timezone.utc)
    assert recuperacao._resumo([{"data": data, "id": 1}]) == recuperacao._resumo([{"id": 1, "data": data.astimezone(timezone(timedelta(hours=-3)))}])
    assert recuperacao._resumo([{"id": 1}]) != recuperacao._resumo([{"id": 2}])


@pytest.mark.parametrize("tabela,id,item", [("desconhecida", 1, "internet"), ("vistoria", None, None), ("vistoria_item", 1, None)])
def test_aceite_seletivo_exige_escopo_conhecido(tabela, id, item):
    with pytest.raises(ValueError):
        recuperacao._filtro({"tabela": tabela, "operacao": "update", "vistoria_id": id, "item_codigo": item})
