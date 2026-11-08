import pytest

from eduinfra.contrato_cdc import (
    COLUNA_AUSENTE,
    COLUNA_NOVA,
    COLUNAS_ESPERADAS,
    OID_BIGINT,
    OID_TEXTO,
    TABELA_DE_ITENS,
    TIPO_ALTERADO,
    TIPO_LIVRE,
    Desvio,
    comparar,
)

OID_DO_ENUM = 16390


def _colunas_do_contrato() -> dict[str, int]:
    return {coluna: OID_DO_ENUM if oid is TIPO_LIVRE else oid for coluna, oid in COLUNAS_ESPERADAS[TABELA_DE_ITENS].items()}


def test_tabela_igual_ao_contrato_nao_tem_desvio():
    assert comparar(TABELA_DE_ITENS, _colunas_do_contrato()) == []


@pytest.mark.parametrize("oid_do_enum", [1, 16390, 99999])
def test_enum_pode_ter_qualquer_oid(oid_do_enum):
    assert comparar(TABELA_DE_ITENS, _colunas_do_contrato() | {"situacao": oid_do_enum}) == []


def test_coluna_nova_e_acusada():
    assert comparar(TABELA_DE_ITENS, _colunas_do_contrato() | {"nova": OID_TEXTO}) == [Desvio(TABELA_DE_ITENS, "nova", COLUNA_NOVA)]


def test_coluna_que_sumiu_e_acusada():
    colunas = _colunas_do_contrato()
    del colunas["quantidade"]

    assert comparar(TABELA_DE_ITENS, colunas) == [Desvio(TABELA_DE_ITENS, "quantidade", COLUNA_AUSENTE)]


def test_renomear_aparece_como_uma_coluna_nova_e_uma_ausente():
    colunas = _colunas_do_contrato()
    colunas["qtd"] = colunas.pop("quantidade")

    assert comparar(TABELA_DE_ITENS, colunas) == [
        Desvio(TABELA_DE_ITENS, "qtd", COLUNA_NOVA),
        Desvio(TABELA_DE_ITENS, "quantidade", COLUNA_AUSENTE),
    ]


def test_tipo_trocado_e_acusado():
    assert comparar(TABELA_DE_ITENS, _colunas_do_contrato() | {"quantidade": OID_BIGINT}) == [
        Desvio(TABELA_DE_ITENS, "quantidade", TIPO_ALTERADO)
    ]


def test_tabela_fora_do_contrato_e_ignorada():
    assert comparar("outra_tabela", {"qualquer": OID_TEXTO}) == []


def test_desvio_se_descreve_com_tabela_coluna_e_tipo():
    assert str(Desvio(TABELA_DE_ITENS, "nova", COLUNA_NOVA)) == "vistoria_item.nova: coluna_nova"
