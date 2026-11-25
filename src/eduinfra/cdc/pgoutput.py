"""Decodificação das mensagens do plugin pgoutput (protocolo de replicação lógica, versão 1).

A origem é insert-only, mas a publicação entrega as quatro operações (migração 0007):
um Update, Delete ou Truncate só chega se alguém desviou dos gatilhos, e esse desvio
precisa ser contado. Por isso eles viram Alteracao em vez de erro. A definição de tipo
(Type), que o servidor envia antes da Relation quando a tabela usa um enum, é lida e
ignorada.
Formato: https://www.postgresql.org/docs/16/protocol-logicalrep-message-formats.html
"""

import struct
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

EPOCA_POSTGRES = datetime(2000, 1, 1, tzinfo=timezone.utc)

OPERACAO_INSERT = "insert"
OPERACAO_UPDATE = "update"
OPERACAO_DELETE = "delete"
OPERACAO_TRUNCATE = "truncate"


@dataclass(frozen=True)
class Insercao:
    tabela: str
    valores: dict[str, str | None]


@dataclass(frozen=True)
class Alteracao:
    operacao: str
    tabela: str
    valores: dict[str, str | None]


@dataclass(frozen=True)
class Relacao:
    nome: str
    colunas: dict[str, int]


@dataclass(frozen=True)
class Transacao:
    xid: int
    lsn_fim: int
    confirmada_em: datetime
    insercoes: tuple[Insercao, ...]
    alteracoes: tuple[Alteracao, ...] = ()
    relacoes: tuple[Relacao, ...] = ()


@dataclass
class _Decodificador:
    relacoes: dict[int, Relacao] = field(default_factory=dict)
    xid: int = 0
    insercoes: list[Insercao] = field(default_factory=list)
    alteracoes: list[Alteracao] = field(default_factory=list)
    relacoes_anunciadas: list[Relacao] = field(default_factory=list)


class _Leitor:
    def __init__(self, dados: bytes) -> None:
        self._dados = dados
        self._posicao = 0

    def inteiro(self, formato: str) -> int:
        (valor,) = struct.unpack_from(">" + formato, self._dados, self._posicao)
        self._posicao += struct.calcsize(formato)
        return valor

    def texto_terminado(self) -> str:
        fim = self._dados.index(b"\x00", self._posicao)
        valor = self._dados[self._posicao : fim].decode("utf-8")
        self._posicao = fim + 1
        return valor

    def bytes_(self, quantidade: int) -> bytes:
        valor = self._dados[self._posicao : self._posicao + quantidade]
        self._posicao += quantidade
        return valor


def decodificar(mensagens: Iterable[bytes]) -> list[Transacao]:
    estado = _Decodificador()
    transacoes: list[Transacao] = []

    for mensagem in mensagens:
        tipo = chr(mensagem[0])
        leitor = _Leitor(mensagem[1:])
        if tipo == "B":
            _iniciar(estado, leitor)
        elif tipo == "Y":
            continue
        elif tipo == "R":
            _registrar_relacao(estado, leitor)
        elif tipo == "I":
            _registrar_insercao(estado, leitor)
        elif tipo == "U":
            _registrar_atualizacao(estado, leitor)
        elif tipo == "D":
            _registrar_exclusao(estado, leitor)
        elif tipo == "T":
            _registrar_truncamento(estado, leitor)
        elif tipo == "C":
            transacoes.append(_encerrar(estado, leitor))
        else:
            raise ValueError(f"mensagem pgoutput inesperada: {tipo!r}")

    return transacoes


def _instante(microssegundos: int) -> datetime:
    return EPOCA_POSTGRES + timedelta(microseconds=microssegundos)


def _iniciar(estado: _Decodificador, leitor: _Leitor) -> None:
    leitor.inteiro("Q")
    leitor.inteiro("q")
    estado.xid = leitor.inteiro("I")
    estado.insercoes = []
    estado.alteracoes = []
    estado.relacoes_anunciadas = []


def _registrar_relacao(estado: _Decodificador, leitor: _Leitor) -> None:
    identificador = leitor.inteiro("I")
    leitor.texto_terminado()
    nome = leitor.texto_terminado()
    leitor.inteiro("B")
    quantidade = leitor.inteiro("H")
    colunas = {}
    for _ in range(quantidade):
        leitor.inteiro("B")
        coluna = leitor.texto_terminado()
        colunas[coluna] = leitor.inteiro("I")
        leitor.inteiro("i")
    relacao = Relacao(nome, colunas)
    estado.relacoes[identificador] = relacao
    estado.relacoes_anunciadas.append(relacao)


def _registrar_insercao(estado: _Decodificador, leitor: _Leitor) -> None:
    relacao = estado.relacoes[leitor.inteiro("I")]
    if chr(leitor.inteiro("B")) != "N":
        raise ValueError("insert sem tupla nova")

    estado.insercoes.append(Insercao(relacao.nome, _ler_tupla(relacao, leitor)))


def _registrar_atualizacao(estado: _Decodificador, leitor: _Leitor) -> None:
    relacao = estado.relacoes[leitor.inteiro("I")]
    marcador = chr(leitor.inteiro("B"))
    if marcador in ("K", "O"):
        _ler_tupla(relacao, leitor)
        marcador = chr(leitor.inteiro("B"))
    if marcador != "N":
        raise ValueError("update sem tupla nova")

    estado.alteracoes.append(Alteracao(OPERACAO_UPDATE, relacao.nome, _ler_tupla(relacao, leitor)))


def _registrar_exclusao(estado: _Decodificador, leitor: _Leitor) -> None:
    relacao = estado.relacoes[leitor.inteiro("I")]
    leitor.inteiro("B")

    estado.alteracoes.append(Alteracao(OPERACAO_DELETE, relacao.nome, _ler_tupla(relacao, leitor)))


def _registrar_truncamento(estado: _Decodificador, leitor: _Leitor) -> None:
    quantidade = leitor.inteiro("I")
    leitor.inteiro("B")
    for _ in range(quantidade):
        relacao = estado.relacoes[leitor.inteiro("I")]
        estado.alteracoes.append(Alteracao(OPERACAO_TRUNCATE, relacao.nome, {}))


def _ler_tupla(relacao: Relacao, leitor: _Leitor) -> dict[str, str | None]:
    return {nome: _valor_da_coluna(leitor) for nome in list(relacao.colunas)[: leitor.inteiro("H")]}


def _valor_da_coluna(leitor: _Leitor) -> str | None:
    tipo = chr(leitor.inteiro("B"))
    # "u" é valor TOAST não alterado: um update não reenvia o conteúdo.
    if tipo in ("n", "u"):
        return None
    if tipo != "t":
        raise ValueError(f"valor de coluna em formato inesperado: {tipo!r}")

    return leitor.bytes_(leitor.inteiro("I")).decode("utf-8")


def _encerrar(estado: _Decodificador, leitor: _Leitor) -> Transacao:
    leitor.inteiro("B")
    leitor.inteiro("Q")
    lsn_fim = leitor.inteiro("Q")
    confirmada_em = _instante(leitor.inteiro("q"))

    return Transacao(
        estado.xid,
        lsn_fim,
        confirmada_em,
        tuple(estado.insercoes),
        tuple(estado.alteracoes),
        tuple(estado.relacoes_anunciadas),
    )


def formatar_lsn(lsn: int) -> str:
    return f"{lsn >> 32:X}/{lsn & 0xFFFFFFFF:X}"


def interpretar_lsn(texto: str) -> int:
    alto, baixo = texto.split("/")
    return (int(alto, 16) << 32) | int(baixo, 16)
