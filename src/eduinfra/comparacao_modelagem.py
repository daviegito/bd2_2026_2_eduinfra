"""Passo 4 do Método de Decisão para o ADR 0001: CRUD contra insert-only, com a carga real."""

import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import psycopg

DATAS_DE_REVISITA = (date(2024, 8, 27), date(2024, 11, 25), date(2025, 2, 23))
DATA_DA_VISTORIA_MEDIDA = date(2025, 5, 24)
AMOSTRA_DE_LEITURA = 500
AMOSTRA_DE_ESCRITA = 200
AQUECIMENTO = 20

PREPARO_INSERT_ONLY = [
    "drop schema if exists comparacao cascade",
    "create schema comparacao",
    "create table comparacao.vistoria (like public.vistoria including all)",
    "create table comparacao.vistoria_item (like public.vistoria_item including all)",
    "insert into comparacao.vistoria overriding system value select * from public.vistoria",
    "insert into comparacao.vistoria_item select * from public.vistoria_item",
    """
    select setval(pg_get_serial_sequence('comparacao.vistoria', 'id'),
                  (select max(id) from comparacao.vistoria))
    """,
    """
    create view comparacao.estado_atual as
    select distinct on (vistoria.co_entidade, item.item_codigo)
        vistoria.co_entidade, item.item_codigo, item.situacao, item.quantidade
    from comparacao.vistoria vistoria
    join comparacao.vistoria_item item on item.vistoria_id = vistoria.id
    where item.situacao <> 'nao_informado'
    order by vistoria.co_entidade, item.item_codigo, vistoria.ocorrido_em desc, vistoria.id desc
    """,
]

PREPARO_CRUD = [
    """
    create table comparacao.estado_crud (
        co_entidade integer not null,
        item_codigo text not null,
        situacao situacao_item not null,
        quantidade integer,
        atualizado_em timestamptz not null,
        primary key (co_entidade, item_codigo)
    )
    """,
    """
    insert into comparacao.estado_crud
    select co_entidade, item_codigo, situacao, quantidade, vigente_desde
    from public.escola_estado_atual
    """,
]

# A revisita troca a situação de um item só; o resto da vistoria repete o que o Censo disse.
REVISITA_INSERT_ONLY = [
    """
    insert into comparacao.vistoria (co_entidade, tecnico_id, origem, ocorrido_em)
    select co_entidade, %(tecnico)s, 'vistoria_presencial', %(data)s
    from public.escola where {filtro}
    """,
    """
    insert into comparacao.vistoria_item (vistoria_id, item_codigo, situacao, quantidade)
    select nova.id, base.item_codigo,
           case when base.item_codigo = 'lab_informatica' then 'existente' else base.situacao end,
           base.quantidade
    from comparacao.vistoria nova
    join comparacao.vistoria censo
      on censo.co_entidade = nova.co_entidade and censo.origem = 'censo_escolar'
    join comparacao.vistoria_item base on base.vistoria_id = censo.id
    where nova.origem = 'vistoria_presencial' and nova.ocorrido_em = %(data)s and {filtro_nova}
    """,
]

REVISITA_CRUD = """
update comparacao.estado_crud
set situacao = case when item_codigo = 'lab_informatica' then 'existente' else situacao end,
    atualizado_em = %(data)s
where {filtro}
"""

TAMANHO = "select sum(pg_total_relation_size(tabela::regclass)) from unnest(%s::text[]) tabela"

TABELAS_INSERT_ONLY = ["comparacao.vistoria", "comparacao.vistoria_item"]
TABELAS_CRUD = ["comparacao.estado_crud"]


@dataclass(frozen=True)
class Latencia:
    mediana_ms: float
    p95_ms: float


@dataclass(frozen=True)
class Modelagem:
    tamanho_base: int
    tamanho_apos_revisitas: int
    escrita: Latencia
    leitura: Latencia


@dataclass(frozen=True)
class Comparacao:
    insert_only: Modelagem
    crud: Modelagem
    segundos_para_colapsar_em_crud: float


def comparar(dsn: str) -> Comparacao:
    with psycopg.connect(dsn, autocommit=True) as conexao:
        try:
            return _executar(conexao)
        finally:
            conexao.execute("drop schema if exists comparacao cascade")


def _executar(conexao: psycopg.Connection) -> Comparacao:
    _rodar(conexao, PREPARO_INSERT_ONLY)
    inicio = time.perf_counter()
    _rodar(conexao, PREPARO_CRUD)
    segundos_para_colapsar = time.perf_counter() - inicio
    conexao.execute("vacuum analyze comparacao.vistoria, comparacao.vistoria_item, comparacao.estado_crud")

    tamanho_base_io = _tamanho(conexao, TABELAS_INSERT_ONLY)
    tamanho_base_crud = _tamanho(conexao, TABELAS_CRUD)
    tecnico = conexao.execute("select min(id) from public.tecnico").fetchone()[0]

    for ciclo, data in enumerate(DATAS_DE_REVISITA):
        _aplicar_revisita(conexao, ciclo, data, tecnico)
    conexao.execute("checkpoint")

    return Comparacao(
        insert_only=Modelagem(
            tamanho_base_io,
            _tamanho(conexao, TABELAS_INSERT_ONLY),
            _medir_escrita(conexao, _escrever_insert_only, tecnico),
            _medir_leitura(conexao, "select * from comparacao.estado_atual where co_entidade = %s"),
        ),
        crud=Modelagem(
            tamanho_base_crud,
            _tamanho(conexao, TABELAS_CRUD),
            _medir_escrita(conexao, _escrever_crud, tecnico),
            _medir_leitura(conexao, "select * from comparacao.estado_crud where co_entidade = %s"),
        ),
        segundos_para_colapsar_em_crud=segundos_para_colapsar,
    )


def _aplicar_revisita(conexao: psycopg.Connection, ciclo: int, data: date, tecnico: int) -> None:
    parametros = {"tecnico": tecnico, "data": data}
    filtro = f"co_entidade %% 10 = {ciclo}"

    conexao.execute(REVISITA_INSERT_ONLY[0].format(filtro=filtro), parametros)
    conexao.execute(
        REVISITA_INSERT_ONLY[1].format(filtro_nova=f"nova.co_entidade %% 10 = {ciclo}"), parametros
    )
    conexao.execute(REVISITA_CRUD.format(filtro=filtro), parametros)
    # Autovacuum faria isso entre ciclos reais; sem ele o CRUD pareceria maior do que fica.
    conexao.execute("vacuum analyze comparacao.vistoria, comparacao.vistoria_item, comparacao.estado_crud")


def _escrever_insert_only(conexao: psycopg.Connection, escola: int, tecnico: int) -> None:
    parametros = {"tecnico": tecnico, "data": DATA_DA_VISTORIA_MEDIDA}
    with conexao.transaction():
        conexao.execute(REVISITA_INSERT_ONLY[0].format(filtro=f"co_entidade = {escola}"), parametros)
        conexao.execute(
            REVISITA_INSERT_ONLY[1].format(filtro_nova=f"nova.co_entidade = {escola}"), parametros
        )


def _escrever_crud(conexao: psycopg.Connection, escola: int, tecnico: int) -> None:
    with conexao.transaction():
        conexao.execute(REVISITA_CRUD.format(filtro=f"co_entidade = {escola}"), {"data": DATA_DA_VISTORIA_MEDIDA})


def _medir_escrita(
    conexao: psycopg.Connection,
    escrever: Callable[[psycopg.Connection, int, int], None],
    tecnico: int,
) -> Latencia:
    # Escolas fora dos ciclos de revisita, para que as duas modelagens partam do mesmo estado.
    escolas = _amostra(conexao, AMOSTRA_DE_ESCRITA + AQUECIMENTO, "co_entidade %% 10 = 9")
    for escola in escolas[:AQUECIMENTO]:
        escrever(conexao, escola, tecnico)

    duracoes = []
    for escola in escolas[AQUECIMENTO:]:
        inicio = time.perf_counter()
        escrever(conexao, escola, tecnico)
        duracoes.append(time.perf_counter() - inicio)
    return _resumir(duracoes)


def _medir_leitura(conexao: psycopg.Connection, consulta: str) -> Latencia:
    # Tempo de execução no servidor, mesmo método da caracterização: a ida e volta
    # entre host e container não é custo da modelagem.
    escolas = _amostra(conexao, AMOSTRA_DE_LEITURA, "true")
    for escola in escolas:
        conexao.execute(consulta, (escola,)).fetchall()

    duracoes = []
    for escola in escolas:
        plano = conexao.execute(f"explain (analyze, format json) {consulta}", (escola,)).fetchone()[0]
        duracoes.append(plano[0]["Execution Time"] / 1000)
    return _resumir(duracoes)


def _amostra(conexao: psycopg.Connection, tamanho: int, filtro: str) -> list[int]:
    consulta = f"select co_entidade from public.escola where {filtro} order by md5(co_entidade::text) limit %s"
    return [linha[0] for linha in conexao.execute(consulta, (tamanho,)).fetchall()]


def _resumir(duracoes: list[float]) -> Latencia:
    em_ms = sorted(duracao * 1000 for duracao in duracoes)
    return Latencia(statistics.median(em_ms), em_ms[int(len(em_ms) * 0.95) - 1])


def _tamanho(conexao: psycopg.Connection, tabelas: list[str]) -> int:
    return conexao.execute(TAMANHO, (tabelas,)).fetchone()[0]


def _rodar(conexao: psycopg.Connection, comandos: list[str]) -> None:
    for comando in comandos:
        conexao.execute(comando)


def formatar_markdown(comparacao: Comparacao) -> str:
    io, crud = comparacao.insert_only, comparacao.crud
    linhas = [
        "| Métrica | A. CRUD (opção nula) | C. Insert-only com carimbo do evento |",
        "| --- | --- | --- |",
        f"| Tamanho após a carga de linha de base | {_mb(crud.tamanho_base)} | {_mb(io.tamanho_base)} |",
        f"| Tamanho após {len(DATAS_DE_REVISITA)} ciclos de revisita (10% das escolas cada) "
        f"| {_mb(crud.tamanho_apos_revisitas)} | {_mb(io.tamanho_apos_revisitas)} |",
        f"| Registrar uma vistoria, mediana / p95 | {_ms(crud.escrita)} | {_ms(io.escrita)} |",
        f"| Estado atual de uma escola, mediana / p95 | {_ms(crud.leitura)} | {_ms(io.leitura)} |",
        "| Responde qual era a situação em 29/05/2024 depois das revisitas | não | sim |",
    ]
    rodape = f"\nColapsar o insert-only em CRUD: {comparacao.segundos_para_colapsar_em_crud:.1f} s."
    return "\n".join(linhas) + rodape


def _mb(tamanho_em_bytes: int) -> str:
    return f"{tamanho_em_bytes / 1024 / 1024:.0f} MB"


def _ms(latencia: Latencia) -> str:
    return f"{latencia.mediana_ms:.2f} ms / {latencia.p95_ms:.2f} ms".replace(".", ",")
