"""Demonstração do dado atrasado e do reprocessamento de janela (issue #27).

Insere duas vistorias presenciais registradas com atraso e mostra em que janela
cada uma cai: uma ocorrida há três dias, com a janela ainda aberta, e outra
ocorrida num mês já fechado. No fim, reprocessa a mesma janela duas vezes e
compara as impressões.

A origem é insert-only: as vistorias da demonstração ficam no banco, marcadas na
observação. Rode num ambiente de desenvolvimento.
"""

from datetime import datetime, timedelta

import psycopg

from eduinfra import janelas
from eduinfra.configuracao import Configuracao

MATRICULA_DEMONSTRACAO = "DEMO-E2-27"
OBSERVACAO = "Demonstração da E2 (issue #27): vistoria registrada com atraso"


def demonstrar(configuracao: Configuracao) -> str:
    saida = ["# Dado atrasado e reprocessamento de janela", ""]

    saida += ["## 1. Estado de partida", "", janelas.resumo_texto(janelas.processar(configuracao)), ""]

    with psycopg.connect(configuracao.dsn) as conexao:
        escola = _escola_de_exemplo(conexao)
        tecnico = _tecnico_da_demonstracao(conexao)
        agora = conexao.execute("select now()").fetchone()[0]

    tres_dias = agora - timedelta(days=3)
    saida += [f"## 2. Vistoria da escola {escola} ocorrida há 3 dias ({tres_dias:%Y-%m-%d}), registrada agora", ""]
    _inserir_vistoria(configuracao, escola, tecnico, tres_dias)
    saida += [janelas.resumo_texto(janelas.processar(configuracao)), ""]

    mes_fechado = _mes_anterior(_mes_anterior(janelas.mes_de(agora)))
    antiga = janelas.limites(mes_fechado)[0] + timedelta(days=14)
    saida += [
        f"## 3. Vistoria ocorrida em {antiga:%Y-%m-%d}, com a janela {mes_fechado} fechada desde "
        f"{janelas.fechamento(mes_fechado):%Y-%m-%d}, registrada agora",
        "",
    ]
    _inserir_vistoria(configuracao, escola, tecnico, antiga)
    saida += [janelas.resumo_texto(janelas.processar(configuracao)), ""]

    mes = janelas.mes_de(tres_dias)
    primeira = janelas.reprocessar_janela(configuracao, mes)
    segunda = janelas.reprocessar_janela(configuracao, mes)
    saida += [
        f"## 4. Reprocessar a janela {mes} duas vezes",
        "",
        f"1ª: {primeira.linhas} linhas, impressão {primeira.impressao}",
        f"2ª: {segunda.linhas} linhas, impressão {segunda.impressao}",
        f"idênticas: {'sim' if primeira == segunda else 'NÃO'}",
        "",
        "## 5. Janelas na camada analítica",
        "",
    ]
    saida += [f"- {j.mes}: {j.linhas} linhas" for j in janelas.ler_janelas(configuracao).values()]

    return "\n".join(saida)


def _escola_de_exemplo(conexao: psycopg.Connection) -> int:
    # CEM 01 do Gama quando a carga é a do Censo completo; senão, a primeira escola.
    linha = conexao.execute(
        "select co_entidade from escola order by co_entidade = 53002580 desc, co_entidade limit 1"
    ).fetchone()
    if linha is None:
        raise RuntimeError("banco sem escolas: rode a carga antes da demonstração")

    return linha[0]


def _tecnico_da_demonstracao(conexao: psycopg.Connection) -> int:
    conexao.execute(
        """
        insert into tecnico (matricula, nome, lotacao_uf) values (%s, 'Técnico da demonstração', 'DF')
        on conflict (matricula) do nothing
        """,
        (MATRICULA_DEMONSTRACAO,),
    )
    conexao.commit()

    return conexao.execute("select id from tecnico where matricula = %s", (MATRICULA_DEMONSTRACAO,)).fetchone()[0]


def _inserir_vistoria(configuracao: Configuracao, escola: int, tecnico: int, ocorrido_em: datetime) -> int:
    # Repete a situação atual dos itens, com o laboratório de ciências agora existente.
    with psycopg.connect(configuracao.dsn) as conexao:
        vistoria = conexao.execute(
            """
            insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em, observacao)
            values (%s, %s, 'vistoria_presencial', %s, %s)
            returning id
            """,
            (escola, tecnico, ocorrido_em, OBSERVACAO),
        ).fetchone()[0]
        conexao.execute(
            """
            insert into vistoria_item (vistoria_id, item_codigo, situacao, quantidade)
            select %s, item_codigo,
                   case when item_codigo = 'lab_ciencias' then 'existente'::situacao_item else situacao end,
                   quantidade
            from escola_estado_atual
            where co_entidade = %s
            """,
            (vistoria, escola),
        )

    return vistoria


def _mes_anterior(mes: str) -> str:
    inicio = janelas.limites(mes)[0]
    return janelas.mes_de(inicio - timedelta(days=1))

