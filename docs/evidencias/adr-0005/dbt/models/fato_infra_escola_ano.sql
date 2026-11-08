{{ config(indexes=[{'columns': ['co_entidade', 'ano'], 'unique': True}, {'columns': ['ano']}]) }}

{#- Um item por coluna: o catálogo é lido do banco na hora de rodar, como na opção nula. -#}
{%- set consulta_de_itens -%}
select codigo from {{ source('origem', 'item_infraestrutura') }} order by codigo
{%- endset -%}
{%- set itens = run_query(consulta_de_itens).columns[0].values() if execute else [] %}

with anos as (
    select distinct co_entidade,
           extract(year from ocorrido_em at time zone 'UTC')::int as ano
    from {{ source('origem', 'vistoria') }}
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
       {%- for codigo in itens %}
       (d.itens ->> '{{ codigo }}') = 'existente' as {{ codigo }}{{ "," if not loop.last }}
       {%- endfor %}
from referencia r
left join {{ ref('dim_escola_infraestrutura') }} d
  on d.co_entidade = r.co_entidade
 and d.valid_from <= r.em
 and r.em < d.valid_to
