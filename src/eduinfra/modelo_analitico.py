"""Modelo dimensional da camada Gold (E3): dimensões, SCD tipo 2 e fatos de duas granularidades.

Tudo é SQL sobre a origem OLTP, executado numa única transação: quem consulta a
Gold vê o modelo anterior inteiro ou o novo inteiro. O SQL fica em constantes de
propósito, para poder ser movido sem mudanças para dbt ou SQLMesh quando o ADR
0005 escolher a ferramenta. As tabelas `stg_*` são o único ponto que lê a
origem; trocar a fonte de eventos (issue de unificação) é mudar só elas.
"""

import logging
import re
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass

import psycopg

log = logging.getLogger(__name__)

PADRAO_DE_CODIGO_DE_ITEM = re.compile(r"^[a-z_]+$")

# Itens que aparecem como coluna booleana na tabela larga. Todos os itens do catálogo
# viram coluna; a lista é lida do banco para não divergir da migração 0002.
CONSULTAS_DA_DIMENSAO = [
    "drop schema if exists gold cascade",
    "create schema gold",
    # --- dimensões de atributo estável (tipo 1) ---
    """
    create table gold.dim_item as
    select codigo as item_codigo, descricao, categoria
    from public.item_infraestrutura
    """,
    "alter table gold.dim_item add primary key (item_codigo)",
    """
    create table gold.dim_municipio as
    select co_municipio, no_municipio, sg_uf, co_uf
    from public.municipio
    """,
    "alter table gold.dim_municipio add primary key (co_municipio)",
    # Atributos vêm de um único ano do Censo; enquanto só um ano for ingerido não há
    # histórico a preservar, e a variação lenta mora no perfil de infraestrutura.
    """
    create table gold.dim_escola as
    select
        e.co_entidade,
        e.no_entidade,
        e.co_municipio,
        e.tp_dependencia,
        case e.tp_dependencia
            when 1 then 'federal' when 2 then 'estadual'
            when 3 then 'municipal' else 'privada'
        end as rede,
        case e.tp_localizacao when 1 then 'urbana' else 'rural' end as localizacao,
        e.ano_censo_referencia
    from public.escola e
    """,
    "alter table gold.dim_escola add primary key (co_entidade)",
    "create index dim_escola_por_municipio on gold.dim_escola (co_municipio)",
]

# Staging: a única leitura da origem. Cada linha é uma MUDANÇA no estado de um item de
# uma escola, com o intervalo [desde, ate) em que aquele estado valeu. 'nao_informado'
# não é estado: não cria versão nem apaga o que se sabia (mesma regra de escola_estado_atual).
CONSULTAS_DE_STAGING = [
    """
    create temp table stg_mudanca_item on commit drop as
    with eventos as (
        select v.co_entidade, vi.item_codigo, vi.situacao::text as situacao, vi.quantidade,
               v.ocorrido_em as desde, v.id as vistoria_id
        from public.vistoria v
        join public.vistoria_item vi on vi.vistoria_id = v.id
        where vi.situacao <> 'nao_informado'
    ),
    comparados as (
        select *,
               lag(situacao) over janela as situacao_anterior,
               lag(quantidade) over janela as quantidade_anterior,
               row_number() over janela as ordem
        from eventos
        window janela as (partition by co_entidade, item_codigo order by desde, vistoria_id)
    ),
    mudancas as (
        select co_entidade, item_codigo, situacao, quantidade, desde, vistoria_id
        from comparados
        where ordem = 1
           or situacao is distinct from situacao_anterior
           or quantidade is distinct from quantidade_anterior
    )
    select *,
           coalesce(
               lead(desde) over (partition by co_entidade, item_codigo order by desde, vistoria_id),
               'infinity'::timestamptz
           ) as ate
    from mudancas
    """,
    "create index on stg_mudanca_item (co_entidade, desde)",
    "analyze stg_mudanca_item",
]

# SCD tipo 2: uma versão do perfil de infraestrutura da escola a cada instante em que ALGUM
# item mudou. A chave substituta é função de (escola, início): reconstruir a dimensão depois
# de um evento atrasado não troca a chave das versões que já existiam.
CONSULTAS_DA_SCD = [
    """
    create table gold.dim_escola_infraestrutura as
    with inicios as (
        select co_entidade, desde as valid_from, max(vistoria_id) as vistoria_origem_id
        from stg_mudanca_item
        group by co_entidade, desde
    ),
    perfis as (
        select i.co_entidade, i.valid_from, i.vistoria_origem_id,
               count(*) as n_itens_conhecidos,
               count(*) filter (where m.situacao = 'existente') as n_existentes,
               count(*) filter (where m.situacao = 'inexistente') as n_inexistentes,
               count(*) filter (where m.situacao = 'em_obra') as n_em_obra,
               count(*) filter (where m.situacao = 'inutilizavel') as n_inutilizaveis,
               jsonb_object_agg(m.item_codigo, m.situacao) as itens,
               coalesce(jsonb_object_agg(m.item_codigo, m.quantidade)
                        filter (where m.quantidade is not null), '{}'::jsonb) as quantidades
        from inicios i
        join stg_mudanca_item m
          on m.co_entidade = i.co_entidade
         and m.desde <= i.valid_from
         and i.valid_from < m.ate
        group by i.co_entidade, i.valid_from, i.vistoria_origem_id
    )
    select
        hashtextextended(co_entidade::text || '|' || valid_from::text, 0) as escola_infra_sk,
        co_entidade,
        valid_from,
        coalesce(lead(valid_from) over proxima, 'infinity'::timestamptz) as valid_to,
        lead(valid_from) over proxima is null as is_current,
        vistoria_origem_id,
        n_itens_conhecidos, n_existentes, n_inexistentes, n_em_obra, n_inutilizaveis,
        itens, quantidades
    from perfis
    window proxima as (partition by co_entidade order by valid_from)
    """,
    "alter table gold.dim_escola_infraestrutura add primary key (escola_infra_sk)",
    "alter table gold.dim_escola_infraestrutura add constraint escola_infra_unica_por_inicio unique (co_entidade, valid_from)",
    "create index dim_escola_infra_vigencia on gold.dim_escola_infraestrutura (co_entidade, valid_from, valid_to)",
]

# Fato fino. Grão: uma linha por escola, por item de infraestrutura, por vistoria.
CONSULTAS_DO_FATO_FINO = [
    """
    create table gold.fato_vistoria_item as
    select v.id as vistoria_id,
           vi.item_codigo,
           v.co_entidade,
           (v.ocorrido_em at time zone 'UTC')::date as data_ocorrencia,
           v.origem,
           vi.situacao::text as situacao,
           vi.quantidade
    from public.vistoria v
    join public.vistoria_item vi on vi.vistoria_id = v.id
    """,
    "alter table gold.fato_vistoria_item add primary key (vistoria_id, item_codigo)",
    "create index fato_vistoria_item_por_item on gold.fato_vistoria_item (item_codigo, situacao) include (co_entidade, data_ocorrencia)",
]


def _consultas_do_fato_largo(itens: list[str]) -> list[str]:
    """Fato grosso. Grão: uma linha por escola, por ano civil em que houve vistoria, com o
    perfil vigente ao fim desse ano (ou agora, no ano corrente). Um item por coluna."""
    for codigo in itens:
        if not PADRAO_DE_CODIGO_DE_ITEM.fullmatch(codigo):
            raise ValueError(f"código de item inesperado no catálogo: {codigo!r}")

    colunas = ",\n           ".join(
        f"(d.itens ->> '{codigo}') = 'existente' as {codigo}" for codigo in itens
    )

    return [
        f"""
        create table gold.fato_infra_escola_ano as
        with anos as (
            select distinct co_entidade,
                   extract(year from ocorrido_em at time zone 'UTC')::int as ano
            from public.vistoria
        ),
        referencia as (
            select co_entidade, ano,
                   least(make_timestamptz(ano, 12, 31, 23, 59, 59.999, 'UTC'), now()) as em
            from anos
        )
        select r.co_entidade,
               r.ano,
               d.escola_infra_sk,
               coalesce(d.n_itens_conhecidos, 0) as n_itens_conhecidos,
               coalesce(d.n_existentes, 0) as n_existentes,
               coalesce(d.n_inexistentes, 0) as n_inexistentes,
               coalesce(d.n_em_obra, 0) as n_em_obra,
               coalesce(d.n_inutilizaveis, 0) as n_inutilizaveis,
               round(100.0 * d.n_existentes / nullif(d.n_itens_conhecidos, 0), 2) as pct_existentes,
               {colunas}
        from referencia r
        left join gold.dim_escola_infraestrutura d
          on d.co_entidade = r.co_entidade
         and d.valid_from <= r.em
         and r.em < d.valid_to
        """,
        "alter table gold.fato_infra_escola_ano add primary key (co_entidade, ano)",
        "create index fato_infra_escola_ano_por_ano on gold.fato_infra_escola_ano (ano)",
    ]


@dataclass(frozen=True)
class ResumoGold:
    linhas: dict[str, int]
    segundos: float


TABELAS_DA_GOLD = (
    "dim_item",
    "dim_municipio",
    "dim_escola",
    "dim_escola_infraestrutura",
    "fato_vistoria_item",
    "fato_infra_escola_ano",
)


def construir(dsn: str) -> ResumoGold:
    inicio = time.perf_counter()

    with psycopg.connect(dsn) as conexao:
        with conexao.transaction():
            itens = [
                linha[0]
                for linha in conexao.execute("select codigo from public.item_infraestrutura order by codigo")
            ]
            for etapa in _etapas(itens):
                etapa(conexao)

        linhas = {
            tabela: conexao.execute(f"select count(*) from gold.{tabela}").fetchone()[0]
            for tabela in TABELAS_DA_GOLD
        }

    resumo = ResumoGold(linhas=linhas, segundos=time.perf_counter() - inicio)
    log.info("gold construída em %.1f s: %s", resumo.segundos, linhas)

    return resumo


def _etapas(itens: list[str]) -> list[Callable[[psycopg.Connection], None]]:
    return [
        lambda c: _rodar(c, CONSULTAS_DA_DIMENSAO),
        lambda c: _rodar(c, CONSULTAS_DE_STAGING),
        lambda c: _rodar(c, CONSULTAS_DA_SCD),
        lambda c: _rodar(c, CONSULTAS_DO_FATO_FINO),
        lambda c: _rodar(c, _consultas_do_fato_largo(itens)),
        lambda c: _rodar(c, [f"analyze gold.{tabela}" for tabela in TABELAS_DA_GOLD]),
    ]


def _rodar(conexao: psycopg.Connection, comandos: list[str]) -> None:
    for comando in comandos:
        conexao.execute(comando)


# --- Medição das duas granularidades (insumo do ADR 0005) -------------------------------

# A mesma pergunta em cada grão: % de escolas com laboratório de ciências por UF e rede,
# no ano de maior cobertura. O grão fino precisa escolher a última vistoria de cada escola; o largo
# já guarda o perfil do ano.
CONSULTA_NO_GRAO_FINO = """
    with ultima as (
        select distinct on (f.co_entidade) f.co_entidade, f.situacao
        from gold.fato_vistoria_item f
        where f.item_codigo = 'lab_ciencias'
          and f.situacao <> 'nao_informado'
          and extract(year from f.data_ocorrencia) = %(ano)s
        order by f.co_entidade, f.data_ocorrencia desc, f.vistoria_id desc
    )
    select m.sg_uf, e.rede,
           count(*) as escolas,
           round(100.0 * count(*) filter (where u.situacao = 'existente') / count(*), 1) as pct_com_lab
    from ultima u
    join gold.dim_escola e using (co_entidade)
    join gold.dim_municipio m using (co_municipio)
    group by m.sg_uf, e.rede
    order by m.sg_uf, e.rede
"""

CONSULTA_NO_GRAO_LARGO = """
    select m.sg_uf, e.rede,
           count(*) as escolas,
           round(100.0 * count(*) filter (where f.lab_ciencias) / count(*), 1) as pct_com_lab
    from gold.fato_infra_escola_ano f
    join gold.dim_escola e using (co_entidade)
    join gold.dim_municipio m using (co_municipio)
    where f.ano = %(ano)s and f.lab_ciencias is not null
    group by m.sg_uf, e.rede
    order by m.sg_uf, e.rede
"""


@dataclass(frozen=True)
class MedidaDeGranularidade:
    nome: str
    grao: str
    linhas: int
    tamanho_bytes: int
    mediana_ms: float
    minimo_ms: float
    maximo_ms: float


@dataclass(frozen=True)
class ComparacaoDeGranularidades:
    ano: int
    escolas: int
    fino: MedidaDeGranularidade
    largo: MedidaDeGranularidade
    mesmo_resultado: bool


def comparar(dsn: str, repeticoes: int = 5) -> ComparacaoDeGranularidades:
    with psycopg.connect(dsn, autocommit=True) as conexao:
        # O ano de maior cobertura: num ano com poucas revisitas o grão fino não tem o estado
        # herdado de anos anteriores e a comparação deixaria de ser entre respostas equivalentes.
        ano = conexao.execute(
            "select ano from gold.fato_infra_escola_ano group by ano order by count(*) desc, ano desc limit 1"
        ).fetchone()[0]
        escolas = conexao.execute("select count(*) from gold.dim_escola").fetchone()[0]
        parametros = {"ano": ano}

        resultado_fino, tempos_fino = _medir(conexao, CONSULTA_NO_GRAO_FINO, parametros, repeticoes)
        resultado_largo, tempos_largo = _medir(conexao, CONSULTA_NO_GRAO_LARGO, parametros, repeticoes)

        fino = _medida(
            conexao, "Fino", "uma linha por escola, por item, por vistoria",
            "fato_vistoria_item", tempos_fino,
        )
        largo = _medida(
            conexao, "Largo", "uma linha por escola por ano, um item por coluna",
            "fato_infra_escola_ano", tempos_largo,
        )

    return ComparacaoDeGranularidades(
        ano=ano, escolas=escolas, fino=fino, largo=largo, mesmo_resultado=resultado_fino == resultado_largo
    )


def _medir(
    conexao: psycopg.Connection, consulta: str, parametros: dict, repeticoes: int
) -> tuple[list, list[float]]:
    conexao.execute(consulta, parametros).fetchall()  # aquecimento: cache e plano

    tempos = []
    resultado = []
    for _ in range(repeticoes):
        inicio = time.perf_counter()
        resultado = conexao.execute(consulta, parametros).fetchall()
        tempos.append((time.perf_counter() - inicio) * 1000)

    return resultado, tempos


def _medida(
    conexao: psycopg.Connection, nome: str, grao: str, tabela: str, tempos: list[float]
) -> MedidaDeGranularidade:
    linhas = conexao.execute(f"select count(*) from gold.{tabela}").fetchone()[0]
    tamanho = conexao.execute("select pg_total_relation_size(%s::regclass)", (f"gold.{tabela}",)).fetchone()[0]

    return MedidaDeGranularidade(
        nome=nome,
        grao=grao,
        linhas=linhas,
        tamanho_bytes=tamanho,
        mediana_ms=statistics.median(tempos),
        minimo_ms=min(tempos),
        maximo_ms=max(tempos),
    )


def formatar_markdown(comparacao: ComparacaoDeGranularidades) -> str:
    fino, largo = comparacao.fino, comparacao.largo
    razao_linhas = fino.linhas / largo.linhas if largo.linhas else float("nan")
    razao_tempo = fino.mediana_ms / largo.mediana_ms if largo.mediana_ms else float("nan")

    return "\n".join(
        [
            "### Granularidade do fato",
            "",
            f"Ano medido: {comparacao.ano}. Escolas na dimensão: {_milhar(comparacao.escolas)}.",
            "",
            "| Fato | Grão | Linhas | Tamanho com índices | Mediana | Mínimo | Máximo |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
            _linha(fino),
            _linha(largo),
            "",
            f"O grão fino tem {_decimal(razao_linhas)}x mais linhas e a consulta leva {_decimal(razao_tempo)}x o tempo do largo.",
            "As duas consultas respondem à mesma pergunta (% de escolas com laboratório de ciências, por UF e rede).",
            "Os resultados são idênticos."
            if comparacao.mesmo_resultado
            else "ATENÇÃO: os resultados diferem; o grão fino não herda o estado de anos anteriores.",
        ]
    )


def _milhar(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")


def _decimal(valor: float) -> str:
    return f"{valor:.1f}".replace(".", ",")


def _linha(medida: MedidaDeGranularidade) -> str:
    return (
        f"| {medida.nome} | {medida.grao} | {_milhar(medida.linhas)} | {_decimal(medida.tamanho_bytes / 1_048_576)} MB "
        f"| {_decimal(medida.mediana_ms)} ms | {_decimal(medida.minimo_ms)} ms | {_decimal(medida.maximo_ms)} ms |"
    )
