"""Anonimização do técnico na origem, para o pedido de eliminação da LGPD (issue #87).

A vistoria é insert-only e aponta para tecnico.id, então a linha do técnico não
pode ser apagada. O comando troca nome e matrícula por valores sem significado e
desativa o cadastro; o id continua e as vistorias seguem válidas. A lotação (UF)
fica, porque é uma informação de grão grosso usada no recorte de acesso.

A tabela tecnico não entra na publicação do CDC: as camadas analíticas recebem só
o tecnico_id, então basta anonimizar aqui.
"""

from dataclasses import dataclass

import psycopg

PREFIXO = "anonimizado-"
NOME_ANONIMO = "Técnico anonimizado"


@dataclass(frozen=True)
class Anonimizacao:
    tecnico_id: int
    vistorias: int


def anonimizar_tecnico(dsn: str, matricula: str) -> Anonimizacao | None:
    """Anonimiza o técnico da matrícula; devolve None se ela não existir."""
    if not matricula or matricula.startswith(PREFIXO):
        raise ValueError("Informe a matrícula original do técnico com --matricula.")

    with psycopg.connect(dsn) as conexao:
        linha = conexao.execute(
            """
            update tecnico
               set nome = %s, matricula = %s::text || id::text, ativo = false
             where matricula = %s
            returning id
            """,
            (NOME_ANONIMO, PREFIXO, matricula),
        ).fetchone()
        if linha is None:
            return None
        vistorias = conexao.execute(
            "select count(*) from vistoria where tecnico_id = %s", (linha[0],)
        ).fetchone()[0]

    return Anonimizacao(tecnico_id=linha[0], vistorias=vistorias)
