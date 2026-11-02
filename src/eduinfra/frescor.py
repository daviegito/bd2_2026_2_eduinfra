"""Frescor da Gold e SLA da tabela que alimenta o painel (issue #52).

A Gold é reconstruída inteira por `construir-gold`, num retrato único da origem. O que
a origem recebe depois desse retrato só aparece na Gold na próxima construção. O frescor
mede há quanto tempo a vistoria pendente mais antiga espera: zero quando a Gold tem tudo
o que a origem tem, e não "agora menos a última construção", que confundiria um dia sem
vistorias com uma Gold parada.

Cada medição fica em `monitoramento.frescor_gold`, fora do schema `gold`, que é apagado a
cada construção. Detalhes e SLA em docs/frescor-e-sla.md.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg

from eduinfra import vistorias_de_teste

log = logging.getLogger(__name__)

TABELA_MONITORADA = "gold.fato_infra_escola_ano"

# Uma construção por dia, mais uma hora para a construção (60 s medidos) e um retry.
SLA_PADRAO = timedelta(hours=25)

CRIAR_REGISTRO = [
    "create schema if not exists monitoramento",
    """
    create table if not exists monitoramento.frescor_gold (
        id bigint generated always as identity primary key,
        medido_em timestamptz not null,
        comando text not null,
        tabela text not null,
        gold_construida boolean not null,
        vistorias_pendentes integer,
        pendente_desde timestamptz,
        frescor_s double precision,
        sla_s double precision not null,
        dentro_do_sla boolean not null
    )
    """,
]

# Os dois fatos saem do mesmo retrato da origem, na mesma transação: a vistoria que falta
# no fato fino também falta no perfil anual que o painel lê. Vistoria sem item não entra
# na Gold e por isso não conta como pendente.
CONSULTA_PENDENTES = """
    select count(*), min(v.registrado_em)
    from public.vistoria v
    where exists (select 1 from public.vistoria_item vi where vi.vistoria_id = v.id)
      and not exists (select 1 from gold.fato_vistoria_item f where f.vistoria_id = v.id)
"""


@dataclass(frozen=True)
class Medicao:
    medido_em: datetime
    gold_construida: bool
    vistorias_pendentes: int | None
    pendente_desde: datetime | None
    frescor: timedelta | None
    sla: timedelta
    dentro_do_sla: bool


def avaliar(
    agora: datetime,
    gold_construida: bool,
    pendentes: int | None,
    pendente_desde: datetime | None,
    sla: timedelta,
) -> Medicao:
    if not gold_construida:
        return Medicao(agora, False, None, None, None, sla, False)

    if not pendentes:
        return Medicao(agora, True, 0, None, timedelta(0), sla, True)

    # registrado_em é o início da transação que gravou a vistoria, nunca depois do commit:
    # o frescor sai igual ou maior que o real, nunca menor.
    frescor = max(agora - pendente_desde, timedelta(0))

    return Medicao(agora, True, pendentes, pendente_desde, frescor, sla, frescor <= sla)


def medir(dsn: str, sla: timedelta = SLA_PADRAO, comando: str = "medir-frescor") -> Medicao:
    """Mede o frescor da Gold e grava a medição. O relógio é o do banco, o mesmo de registrado_em."""
    with psycopg.connect(dsn) as conexao:
        with conexao.transaction():
            for ddl in CRIAR_REGISTRO:
                conexao.execute(ddl)

            agora = conexao.execute("select now()").fetchone()[0]
            gold_construida = conexao.execute(
                "select to_regclass(%s) is not null and to_regclass('gold.fato_vistoria_item') is not null",
                (TABELA_MONITORADA,),
            ).fetchone()[0]
            pendentes = pendente_desde = None
            if gold_construida:
                pendentes, pendente_desde = conexao.execute(CONSULTA_PENDENTES).fetchone()

            medicao = avaliar(agora, gold_construida, pendentes, pendente_desde, sla)
            _registrar(conexao, medicao, comando)

    if medicao.dentro_do_sla:
        log.info("frescor da Gold: %s", resumo(medicao))
    else:
        log.error("frescor da Gold fora do SLA: %s", resumo(medicao))

    return medicao


def _registrar(conexao: psycopg.Connection, medicao: Medicao, comando: str) -> None:
    conexao.execute(
        """
        insert into monitoramento.frescor_gold (
            medido_em, comando, tabela, gold_construida, vistorias_pendentes,
            pendente_desde, frescor_s, sla_s, dentro_do_sla
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            medicao.medido_em,
            comando,
            TABELA_MONITORADA,
            medicao.gold_construida,
            medicao.vistorias_pendentes,
            medicao.pendente_desde,
            None if medicao.frescor is None else medicao.frescor.total_seconds(),
            medicao.sla.total_seconds(),
            medicao.dentro_do_sla,
        ),
    )


def resumo(medicao: Medicao) -> str:
    if not medicao.gold_construida:
        return f"a Gold não foi construída; rode construir-gold (SLA de {duracao(medicao.sla)})"

    if not medicao.vistorias_pendentes:
        return f"em dia, nenhuma vistoria da origem fora da Gold (SLA de {duracao(medicao.sla)})"

    situacao = "dentro do SLA" if medicao.dentro_do_sla else "FORA DO SLA"
    return (
        f"{medicao.vistorias_pendentes} vistoria(s) fora da Gold, a mais antiga esperando há "
        f"{duracao(medicao.frescor)}: {situacao} de {duracao(medicao.sla)}"
    )


def duracao(intervalo: timedelta) -> str:
    segundos = intervalo.total_seconds()
    if segundos < 60:
        texto = f"{segundos:.1f} s"
    elif segundos < 3600:
        texto = f"{segundos / 60:.1f} min"
    else:
        texto = f"{segundos / 3600:.1f} h"

    return texto.replace(".", ",")


# --- Demonstração (rode em banco de desenvolvimento: a origem é insert-only) ------------

MATRICULA_DEMONSTRACAO = "DEMO-E3-52"
OBSERVACAO_DEMONSTRACAO = "Demonstração da E3 (issue #52): vistoria fora da Gold"
SLA_DA_DEMONSTRACAO = timedelta(seconds=2)
ESPERA_DA_DEMONSTRACAO_S = 5


def demonstrar(dsn: str) -> str:
    saida = ["# Frescor da Gold e violação do SLA", ""]

    inicial = medir(dsn, comando="demonstrar-frescor")
    saida += [f"## 1. Estado de partida, SLA de {duracao(SLA_PADRAO)}", "", resumo(inicial), ""]
    if not inicial.gold_construida:
        return "\n".join(saida + ["Rode `construir-gold` antes da demonstração."])

    with psycopg.connect(dsn) as conexao:
        vistorias_de_teste.inserir_vistorias(conexao, 1, MATRICULA_DEMONSTRACAO, OBSERVACAO_DEMONSTRACAO)
    depois = medir(dsn, comando="demonstrar-frescor")
    saida += ["## 2. Uma vistoria nova na origem, sem reconstruir a Gold", "", resumo(depois), ""]

    time.sleep(ESPERA_DA_DEMONSTRACAO_S)
    violacao = medir(dsn, SLA_DA_DEMONSTRACAO, comando="demonstrar-frescor")
    saida += [
        f"## 3. {ESPERA_DA_DEMONSTRACAO_S} s depois, com o SLA reduzido a {duracao(SLA_DA_DEMONSTRACAO)} para a demonstração",
        "",
        resumo(violacao),
        f"`medir-frescor` com esse SLA sai com código {0 if violacao.dentro_do_sla else 1}.",
        "",
        "## 4. Medições registradas em monitoramento.frescor_gold",
        "",
        *_ultimas_medicoes(dsn, 3),
        "",
        "Para voltar ao normal: `construir-gold` e depois `medir-frescor`.",
    ]

    return "\n".join(saida)


def _ultimas_medicoes(dsn: str, quantidade: int) -> list[str]:
    with psycopg.connect(dsn) as conexao:
        linhas = conexao.execute(
            """
            select medido_em at time zone 'UTC', vistorias_pendentes, frescor_s, sla_s, dentro_do_sla
            from monitoramento.frescor_gold
            order by id desc
            limit %s
            """,
            (quantidade,),
        ).fetchall()

    tabela = ["| Medido em (UTC) | Pendentes | Frescor | SLA | Dentro do SLA |", "| --- | ---: | ---: | ---: | :---: |"]
    for medido_em, pendentes, frescor_s, sla_s, dentro in reversed(linhas):
        frescor = "—" if frescor_s is None else duracao(timedelta(seconds=frescor_s))
        tabela.append(
            f"| {medido_em:%Y-%m-%d %H:%M:%S} | {'—' if pendentes is None else pendentes} | {frescor} "
            f"| {duracao(timedelta(seconds=sla_s))} | {'sim' if dentro else 'não'} |"
        )

    return tabela
