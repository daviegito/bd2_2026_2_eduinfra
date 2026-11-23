{{ config(enabled=var('com_enem', false), indexes=[{'columns': ['co_entidade', 'ano'], 'unique': True}, {'columns': ['ano', 'sg_uf']}]) }}

{#- Tabela larga para consulta por escola: o ETL reverso e as ferramentas MCP filtram sem JOIN nem leitura de JSON. -#}
{%- set consulta_de_itens -%}
select item_codigo from {{ ref('dim_item') }} order by item_codigo
{%- endset -%}
{%- set itens = run_query(consulta_de_itens).columns[0].values() if execute else [] %}

select
    co_entidade, ano, no_entidade, rede, localizacao, co_municipio, no_municipio, sg_uf,
    n_participantes, poucos_participantes,
    media_cn, media_ch, media_lc, media_mt, media_redacao, media_geral,
    faixa_na_rede,
    case
        when faixa_na_rede is null then 'sem_nota'
        when faixa_na_rede = {{ var('faixas_de_desempenho') }} then 'alto'
        when faixa_na_rede = 1 then 'baixo'
        else 'intermediario'
    end as faixa_desempenho,
    data_referencia_infraestrutura, escola_infra_sk,
    n_itens_conhecidos, n_existentes,
    round(100.0 * n_existentes / nullif(n_itens_conhecidos, 0), 1) as pct_itens_existentes,
    -- Situação em texto, e não booleano, para não confundir "não possui" com "não informado".
    {%- for codigo in itens %}
    coalesce(itens ->> '{{ codigo }}', 'nao_informado') as {{ codigo }}{{ "," if not loop.last }}
    {%- endfor %}
from {{ ref('int_desempenho_escola') }}
