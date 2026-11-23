{{ config(enabled=var('com_enem', false), indexes=[{'columns': ['ano', 'rede', 'area']}]) }}

-- O que as escolas da faixa alta têm e as da baixa não (#94), em cada área da prova. A faixa é calculada
-- dentro de cada rede, inclusive na linha "todas", que junta as faixas de cada rede. Cada característica
-- tem o seu denominador: nos itens, só entram as escolas com a situação informada, para "não informado"
-- não ser lido como "não possui".
with extremos as (
    select *, case faixa when 1 then 'baixo' else 'alto' end as grupo
    from {{ ref('int_faixa_por_area') }}
    where faixa in (1, {{ var('faixas_de_desempenho') }})
),
ufs as (
    select distinct sg_uf from {{ ref('int_desempenho_escola') }} where sg_uf <> 'NI'
),
caracteristicas as (
    select
        x.ano, x.rede, x.area, x.grupo, 'item' as tipo, i.item_codigo as caracteristica, i.descricao,
        coalesce(x.itens ->> i.item_codigo, 'nao_informado') <> 'nao_informado' as conhecida,
        coalesce(x.itens ->> i.item_codigo = 'existente', false) as presente
    from extremos x
    cross join {{ ref('dim_item') }} i
    union all
    select ano, rede, area, grupo, 'localizacao', 'urbana', 'Localização urbana', true, localizacao = 'urbana'
    from extremos
    union all
    select x.ano, x.rede, x.area, x.grupo, 'uf', u.sg_uf, 'Escola em ' || u.sg_uf, x.sg_uf <> 'NI', x.sg_uf = u.sg_uf
    from extremos x
    cross join ufs u
    union all
    -- Metade dos itens com situação informada; a escola sem nenhum item informado fica fora do denominador.
    select
        ano, rede, area, grupo, 'conjunto_de_itens', 'metade_dos_itens', 'Pelo menos metade dos itens',
        n_itens_conhecidos > 0, n_itens_conhecidos > 0 and 2 * n_existentes >= n_itens_conhecidos
    from extremos
),
contagens as (
    select
        ano,
        case when grouping(rede) = 1 then 'todas' else rede end as rede,
        area, tipo, caracteristica, descricao,
        count(*) filter (where grupo = 'alto') as n_alto,
        count(*) filter (where grupo = 'alto' and conhecida) as n_alto_conhecido,
        count(*) filter (where grupo = 'alto' and conhecida and presente) as n_alto_com,
        count(*) filter (where grupo = 'baixo') as n_baixo,
        count(*) filter (where grupo = 'baixo' and conhecida) as n_baixo_conhecido,
        count(*) filter (where grupo = 'baixo' and conhecida and presente) as n_baixo_com
    from caracteristicas
    group by grouping sets (
        (ano, area, tipo, caracteristica, descricao, rede),
        (ano, area, tipo, caracteristica, descricao)
    )
),
resultado as (
    select
        c.*,
        n_alto_conhecido >= {{ var('minimo_escolas') }} and n_baixo_conhecido >= {{ var('minimo_escolas') }} as comparavel
    from contagens c
),
percentuais as (
    select
        r.*,
        case when comparavel then round(100.0 * n_alto_com / n_alto_conhecido, 1) end as pct_alto,
        case when comparavel then round(100.0 * n_baixo_com / n_baixo_conhecido, 1) end as pct_baixo
    from resultado r
)
select
    ano, rede, area, tipo, caracteristica, descricao,
    n_alto, n_alto_conhecido, n_alto_com, pct_alto,
    n_baixo, n_baixo_conhecido, n_baixo_com, pct_baixo,
    pct_alto - pct_baixo as diferenca_pp,
    not comparavel as suprimido,
    row_number() over (
        partition by ano, rede, area
        order by abs(pct_alto - pct_baixo) desc nulls last, tipo, caracteristica
    ) as ordem
from percentuais
