{{ config(enabled=var('com_enem', false), indexes=[{'columns': ['ano', 'uf', 'rede', 'area']}]) }}

-- Média das médias escolares de cada recorte de UF e rede, sem ponderar por participantes, como no painel da E3.
with notas as (
    select e.ano, e.sg_uf, e.rede, n.area, n.nota
    from {{ ref('int_desempenho_escola') }} e
    cross join lateral (values
        ('media_geral', e.media_geral), ('media_cn', e.media_cn), ('media_ch', e.media_ch),
        ('media_lc', e.media_lc), ('media_mt', e.media_mt), ('media_redacao', e.media_redacao)
    ) as n(area, nota)
),
agregado as (
    select
        ano,
        case when grouping(sg_uf) = 1 then 'BR' else sg_uf end as uf,
        case when grouping(rede) = 1 then 'todas' else rede end as rede,
        area,
        count(*) as n_escolas,
        count(nota) as n_escolas_com_nota,
        avg(nota) as media_bruta,
        stddev_samp(nota) as desvio_bruto
    from notas
    group by grouping sets ((ano, area, sg_uf, rede), (ano, area, sg_uf), (ano, area, rede), (ano, area))
)
select
    ano, uf, rede, area, n_escolas, n_escolas_com_nota,
    n_escolas_com_nota < {{ var('minimo_escolas') }} as suprimido,
    case when n_escolas_com_nota >= {{ var('minimo_escolas') }} then round(media_bruta::numeric, 1) end as media,
    case when n_escolas_com_nota >= {{ var('minimo_escolas') }} then round(desvio_bruto::numeric, 1) end as desvio_padrao
from agregado
