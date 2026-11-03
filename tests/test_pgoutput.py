import struct
from datetime import datetime, timezone

import pytest

from eduinfra.pgoutput import (
    OPERACAO_DELETE,
    OPERACAO_TRUNCATE,
    OPERACAO_UPDATE,
    Alteracao,
    Insercao,
    Relacao,
    decodificar,
    formatar_lsn,
    interpretar_lsn,
)

MICROSSEGUNDOS_2024 = int((datetime(2024, 5, 29, tzinfo=timezone.utc) - datetime(2000, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1_000_000)
OID_DA_TABELA = 16400
OID_DE_TEXTO = 25
OID_DE_INTEIRO = 23


def _begin(xid: int, lsn_final: int = 100) -> bytes:
    return b"B" + struct.pack(">QqI", lsn_final, MICROSSEGUNDOS_2024, xid)


def _commit(lsn_fim: int) -> bytes:
    return b"C" + struct.pack(">BQQq", 0, lsn_fim - 8, lsn_fim, MICROSSEGUNDOS_2024)


def _relation(oid: int, nome: str, colunas: list[str], tipos: list[int] | None = None) -> bytes:
    corpo = struct.pack(">I", oid) + b"public\x00" + nome.encode() + b"\x00" + struct.pack(">BH", ord("d"), len(colunas))
    for coluna, tipo in zip(colunas, tipos or [OID_DE_TEXTO] * len(colunas)):
        corpo += struct.pack(">B", 0) + coluna.encode() + b"\x00" + struct.pack(">Ii", tipo, -1)
    return b"R" + corpo


def _tupla(valores: list[str | None]) -> bytes:
    corpo = struct.pack(">H", len(valores))
    for valor in valores:
        if valor is None:
            corpo += b"n"
        elif valor == "toast":
            corpo += b"u"
        else:
            dados = valor.encode()
            corpo += b"t" + struct.pack(">I", len(dados)) + dados
    return corpo


def _insert(oid: int, valores: list[str | None]) -> bytes:
    return b"I" + struct.pack(">I", oid) + b"N" + _tupla(valores)


def test_decodifica_transacao_com_duas_insercoes():
    mensagens = [
        _begin(xid=742),
        _relation(16400, "vistoria_item", ["vistoria_id", "item_codigo", "situacao", "quantidade"]),
        _insert(16400, ["9", "biblioteca", "existente", None]),
        _insert(16400, ["9", "quadra", "inexistente", "0"]),
        _commit(lsn_fim=5000),
    ]

    (transacao,) = decodificar(mensagens)

    assert transacao.xid == 742
    assert transacao.lsn_fim == 5000
    assert transacao.confirmada_em == datetime(2024, 5, 29, tzinfo=timezone.utc)
    assert transacao.insercoes == (
        Insercao("vistoria_item", {"vistoria_id": "9", "item_codigo": "biblioteca", "situacao": "existente", "quantidade": None}),
        Insercao("vistoria_item", {"vistoria_id": "9", "item_codigo": "quadra", "situacao": "inexistente", "quantidade": "0"}),
    )


def test_relacao_anunciada_numa_transacao_vale_para_as_seguintes():
    mensagens = [
        _begin(1),
        _relation(16400, "vistoria", ["id"]),
        _insert(16400, ["1"]),
        _commit(10),
        _begin(2),
        _insert(16400, ["2"]),
        _commit(20),
    ]

    primeira, segunda = decodificar(mensagens)

    assert [i.valores["id"] for i in primeira.insercoes] == ["1"]
    assert [i.valores["id"] for i in segunda.insercoes] == ["2"]


def test_definicao_de_tipo_antes_da_relacao_e_ignorada():
    definicao_do_enum = b"Y" + struct.pack(">I", 16390) + b"public" + bytes(1) + b"situacao_item" + bytes(1)
    mensagens = [
        _begin(5),
        definicao_do_enum,
        _relation(16400, "vistoria_item", ["situacao"]),
        _insert(16400, ["existente"]),
        _commit(40),
    ]

    (transacao,) = decodificar(mensagens)

    assert transacao.insercoes == (Insercao("vistoria_item", {"situacao": "existente"}),)


def test_transacao_sem_insercoes_e_valida():
    (transacao,) = decodificar([_begin(3), _commit(30)])

    assert transacao.insercoes == ()


def _decodificar_alteracao(mensagem: bytes) -> tuple[Alteracao, ...]:
    colunas = ["vistoria_id", "item_codigo", "quantidade"]
    (transacao,) = decodificar([_begin(4), _relation(OID_DA_TABELA, "vistoria_item", colunas), mensagem, _commit(40)])
    return transacao.alteracoes


@pytest.mark.parametrize(
    ("mensagem", "esperada"),
    [
        (
            b"U" + struct.pack(">I", OID_DA_TABELA) + b"N" + _tupla(["9", "quadra", "2"]),
            Alteracao(OPERACAO_UPDATE, "vistoria_item", {"vistoria_id": "9", "item_codigo": "quadra", "quantidade": "2"}),
        ),
        (
            b"U" + struct.pack(">I", OID_DA_TABELA) + b"K" + _tupla(["8", "quadra", None]) + b"N" + _tupla(["9", "quadra", "toast"]),
            Alteracao(OPERACAO_UPDATE, "vistoria_item", {"vistoria_id": "9", "item_codigo": "quadra", "quantidade": None}),
        ),
        (
            b"D" + struct.pack(">I", OID_DA_TABELA) + b"K" + _tupla(["9", "quadra", None]),
            Alteracao(OPERACAO_DELETE, "vistoria_item", {"vistoria_id": "9", "item_codigo": "quadra", "quantidade": None}),
        ),
        (
            b"T" + struct.pack(">IB", 1, 0) + struct.pack(">I", OID_DA_TABELA),
            Alteracao(OPERACAO_TRUNCATE, "vistoria_item", {}),
        ),
    ],
    ids=["update", "update-com-chave-antiga", "delete", "truncate"],
)
def test_alteracao_que_desvia_do_insert_only_e_contada_sem_derrubar_a_leitura(mensagem, esperada):
    assert _decodificar_alteracao(mensagem) == (esperada,)


def test_update_sem_tupla_nova_e_erro_de_protocolo():
    with pytest.raises(ValueError, match="tupla nova"):
        _decodificar_alteracao(b"U" + struct.pack(">I", OID_DA_TABELA) + b"X")


def test_mensagem_desconhecida_continua_sendo_erro():
    with pytest.raises(ValueError, match="inesperada"):
        decodificar([_begin(4), b"Z" + b"\x00" * 8])


def test_relacao_expoe_colunas_e_tipos_na_transacao_que_a_anunciou():
    mensagens = [
        _begin(1),
        _relation(OID_DA_TABELA, "vistoria_item", ["vistoria_id", "quantidade"], [OID_DE_TEXTO, OID_DE_INTEIRO]),
        _insert(OID_DA_TABELA, ["1", "2"]),
        _commit(10),
        _begin(2),
        _insert(OID_DA_TABELA, ["3", "4"]),
        _commit(20),
    ]

    primeira, segunda = decodificar(mensagens)

    assert primeira.relacoes == (Relacao("vistoria_item", {"vistoria_id": OID_DE_TEXTO, "quantidade": OID_DE_INTEIRO}),)
    assert segunda.relacoes == ()


@pytest.mark.parametrize("lsn", [0, 1, 0x16B3748, 0x1_0000_00FF, 0xFFFF_FFFF_FFFF])
def test_lsn_ida_e_volta(lsn):
    assert interpretar_lsn(formatar_lsn(lsn)) == lsn


def test_lsn_no_formato_do_postgres():
    assert formatar_lsn(0x16B3748) == "0/16B3748"
    assert interpretar_lsn("1/FF") == 0x1_0000_00FF
