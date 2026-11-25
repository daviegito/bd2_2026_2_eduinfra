{{ config(enabled=var('com_enem', false), indexes=[{'columns': ['ano', 'uf', 'rede', 'item_codigo']}]) }}

-- Todas as escolas do Censo, com ou sem ENEM, no perfil vigente na mesma data de referência do fato de desempenho.
with referencia as (
    select distinct ano, data_referencia_infraestrutura from {{ ref('stg_enem_escola_ano') }}
),
escolas as (
    select r.ano, e.co_entidade, e.rede, coalesce(m.sg_uf, 'NI') as sg_uf, d.itens
    from referencia r
    join {{ ref('dim_escola') }} e on e.ano_censo_referencia = r.ano
    left join {{ ref('dim_municipio') }} m on m.co_municipio = e.co_municipio
    left join {{ ref('dim_escola_infraestrutura') }} d
      on d.co_entidade = e.co_entidade
     and d.valid_from <= r.data_referencia_infraestrutura::timestamp at time zone 'UTC'
     and r.data_referencia_infraestrutura::timestamp at time zone 'UTC' < d.valid_to
),
situacoes as (
    select e.ano, e.sg_uf, e.rede, i.item_codigo, coalesce(e.itens ->> i.item_codigo, 'nao_informado') as situacao
    from escolas e
    cross join {{ ref('dim_item') }} i
),
contagens as (
    select
        ano,
        case when grouping(sg_uf) = 1 then 'BR' else sg_uf end as uf,
        case when grouping(rede) = 1 then 'todas' else rede end as rede,
        item_codigo, situacao,
        count(*) as n_escolas
    from situacoes
    group by grouping sets (
        (ano, item_codigo, situacao, sg_uf, rede),
        (ano, item_codigo, situacao, sg_uf),
        (ano, item_codigo, situacao, rede),
        (ano, item_codigo, situacao)
    )
)
select
    ano, uf, rede, item_codigo, situacao, n_escolas,
    round(100.0 * n_escolas / sum(n_escolas) over (partition by ano, uf, rede, item_codigo), 1) as pct_das_escolas
from contagens
