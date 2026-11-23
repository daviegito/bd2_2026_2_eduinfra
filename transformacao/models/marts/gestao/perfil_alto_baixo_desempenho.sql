{{ config(enabled=var('com_enem', false), indexes=[{'columns': ['ano', 'rede']}]) }}

-- O que as escolas da faixa alta têm e as da baixa não (#94). A faixa é calculada dentro de cada rede,
-- inclusive na linha "todas", que junta as faixas de cada rede. O percentual usa só as escolas com a
-- situação do item conhecida, para "não informado" não ser lido como "não possui".
with faixas as (
    select
        ano, rede, item_codigo, situacao,
        case faixa_na_rede when 1 then 'baixo' else 'alto' end as faixa
    from {{ ref('int_desempenho_escola_item') }}
    where faixa_na_rede in (1, {{ var('faixas_de_desempenho') }})
),
contagens as (
    select
        ano,
        case when grouping(rede) = 1 then 'todas' else rede end as rede,
        item_codigo,
        count(*) filter (where faixa = 'alto') as n_alto,
        count(*) filter (where faixa = 'alto' and situacao <> 'nao_informado') as n_alto_conhecido,
        count(*) filter (where faixa = 'alto' and situacao = 'existente') as n_alto_com_item,
        count(*) filter (where faixa = 'baixo') as n_baixo,
        count(*) filter (where faixa = 'baixo' and situacao <> 'nao_informado') as n_baixo_conhecido,
        count(*) filter (where faixa = 'baixo' and situacao = 'existente') as n_baixo_com_item
    from faixas
    group by grouping sets ((ano, item_codigo, rede), (ano, item_codigo))
),
percentuais as (
    select
        c.*,
        n_alto_conhecido >= {{ var('minimo_escolas') }} and n_baixo_conhecido >= {{ var('minimo_escolas') }} as comparavel
    from contagens c
),
resultado as (
    select
        p.*,
        case when comparavel then round(100.0 * n_alto_com_item / n_alto_conhecido, 1) end as pct_alto_com_item,
        case when comparavel then round(100.0 * n_baixo_com_item / n_baixo_conhecido, 1) end as pct_baixo_com_item
    from percentuais p
)
select
    r.ano, r.rede, r.item_codigo, i.descricao as item_descricao,
    r.n_alto, r.n_alto_conhecido, r.n_alto_com_item, r.pct_alto_com_item,
    r.n_baixo, r.n_baixo_conhecido, r.n_baixo_com_item, r.pct_baixo_com_item,
    r.pct_alto_com_item - r.pct_baixo_com_item as diferenca_pp,
    not r.comparavel as suprimido,
    row_number() over (
        partition by r.ano, r.rede
        order by abs(r.pct_alto_com_item - r.pct_baixo_com_item) desc nulls last, r.item_codigo
    ) as ordem
from resultado r
join {{ ref('dim_item') }} i on i.item_codigo = r.item_codigo
