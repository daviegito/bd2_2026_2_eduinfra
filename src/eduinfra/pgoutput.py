"""Decodificação das mensagens do plugin pgoutput (protocolo de replicação lógica, versão 1).

Só as mensagens que as tabelas insert-only da origem produzem são interpretadas:
Begin, Commit, Relation e Insert. A definição de tipo (Type), que o servidor
envia antes da Relation quando a tabela usa um enum, é lida e ignorada. Update, Delete e Truncate nunca são publicados
(ver a migração 0005), então chegar um deles é erro, não dado.
Formato: https://www.postgresql.org/docs/16/protocol-logicalrep-message-formats.html
"""

import struct
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

EPOCA_POSTGRES = datetime(2000, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Insercao:
    tabela: str
    valores: dict[str, str | None]


@dataclass(frozen=True)
class Transacao:
    xid: int
    lsn_fim: int
    confirmada_em: datetime
    insercoes: tuple[Insercao, ...]


@dataclass
class _Relacao:
    nome: str
    colunas: list[str]


@dataclass
class _Decodificador:
    relacoes: dict[int, _Relacao] = field(default_factory=dict)
    xid: int = 0
    insercoes: list[Insercao] = field(default_factory=list)


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
        elif tipo == "C":
            transacoes.append(_encerrar(estado, leitor))
        else:
            raise ValueError(f"mensagem pgoutput inesperada na origem insert-only: {tipo!r}")

    return transacoes


def _instante(microssegundos: int) -> datetime:
    return EPOCA_POSTGRES + timedelta(microseconds=microssegundos)


def _iniciar(estado: _Decodificador, leitor: _Leitor) -> None:
    leitor.inteiro("Q")
    leitor.inteiro("q")
    estado.xid = leitor.inteiro("I")
    estado.insercoes = []


def _registrar_relacao(estado: _Decodificador, leitor: _Leitor) -> None:
    identificador = leitor.inteiro("I")
    leitor.texto_terminado()
    nome = leitor.texto_terminado()
    leitor.inteiro("B")
    quantidade = leitor.inteiro("H")
    colunas = []
    for _ in range(quantidade):
        leitor.inteiro("B")
        colunas.append(leitor.texto_terminado())
        leitor.inteiro("I")
        leitor.inteiro("i")
    estado.relacoes[identificador] = _Relacao(nome, colunas)


def _registrar_insercao(estado: _Decodificador, leitor: _Leitor) -> None:
    relacao = estado.relacoes[leitor.inteiro("I")]
    if chr(leitor.inteiro("B")) != "N":
        raise ValueError("insert sem tupla nova")

    valores: dict[str, str | None] = {}
    for nome in relacao.colunas[: leitor.inteiro("H")]:
        valores[nome] = _valor_da_coluna(leitor)
    estado.insercoes.append(Insercao(relacao.nome, valores))


def _valor_da_coluna(leitor: _Leitor) -> str | None:
    tipo = chr(leitor.inteiro("B"))
    if tipo == "n":
        return None
    if tipo != "t":
        raise ValueError(f"valor de coluna em formato inesperado: {tipo!r}")

    return leitor.bytes_(leitor.inteiro("I")).decode("utf-8")


def _encerrar(estado: _Decodificador, leitor: _Leitor) -> Transacao:
    leitor.inteiro("B")
    leitor.inteiro("Q")
    lsn_fim = leitor.inteiro("Q")
    confirmada_em = _instante(leitor.inteiro("q"))

    return Transacao(estado.xid, lsn_fim, confirmada_em, tuple(estado.insercoes))


def formatar_lsn(lsn: int) -> str:
    return f"{lsn >> 32:X}/{lsn & 0xFFFFFFFF:X}"


def interpretar_lsn(texto: str) -> int:
    alto, baixo = texto.split("/")
    return (int(alto, 16) << 32) | int(baixo, 16)
