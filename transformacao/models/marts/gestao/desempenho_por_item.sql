{{ config(enabled=var('com_enem', false), indexes=[{'columns': ['ano', 'uf', 'rede', 'area']}]) }}

-- Média das médias escolares por situação do item. A supressão faz parte da definição da métrica,
-- então nenhum consumidor recebe a média de um grupo pequeno.
with notas as (
    select b.ano, b.sg_uf, b.rede, b.item_codigo, b.situacao, n.area, n.nota
    from {{ ref('int_desempenho_escola_item') }} b
    cross join lateral (values
        ('media_geral', b.media_geral), ('media_cn', b.media_cn), ('media_ch', b.media_ch),
        ('media_lc', b.media_lc), ('media_mt', b.media_mt), ('media_redacao', b.media_redacao)
    ) as n(area, nota)
),
agregado as (
    select
        ano,
        case when grouping(sg_uf) = 1 then 'BR' else sg_uf end as uf,
        case when grouping(rede) = 1 then 'todas' else rede end as rede,
        item_codigo, situacao, area,
        count(*) as n_escolas,
        count(nota) as n_escolas_com_nota,
        avg(nota) as media_bruta,
        stddev_samp(nota) as desvio_bruto
    from notas
    group by grouping sets (
        (ano, item_codigo, situacao, area, sg_uf, rede),
        (ano, item_codigo, situacao, area, sg_uf),
        (ano, item_codigo, situacao, area, rede),
        (ano, item_codigo, situacao, area)
    )
)
select
    ano, uf, rede, item_codigo, situacao, area, n_escolas, n_escolas_com_nota,
    n_escolas_com_nota < {{ var('minimo_escolas') }} as suprimido,
    case when n_escolas_com_nota >= {{ var('minimo_escolas') }} then round(media_bruta::numeric, 1) end as media,
    case when n_escolas_com_nota >= {{ var('minimo_escolas') }} then round(desvio_bruto::numeric, 1) end as desvio_padrao
from agregado
