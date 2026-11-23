{{ config(enabled=var('com_enem', false), indexes=[{'columns': ['co_entidade', 'ano'], 'unique': True}]) }}

-- Faixa de desempenho dentro da rede (#94): comparar escolas de redes diferentes confundiria rede com infraestrutura.
with escolas as (
    select
        f.co_entidade, f.ano, e.no_entidade, e.rede, e.localizacao, e.co_municipio,
        m.no_municipio, coalesce(m.sg_uf, 'NI') as sg_uf,
        f.n_participantes, f.poucos_participantes,
        f.media_cn, f.media_ch, f.media_lc, f.media_mt, f.media_redacao, f.media_geral,
        f.data_referencia_infraestrutura, f.escola_infra_sk,
        coalesce(d.n_itens_conhecidos, 0) as n_itens_conhecidos,
        coalesce(d.n_existentes, 0) as n_existentes,
        coalesce(d.itens, '{}'::jsonb) as itens
    from {{ ref('fato_desempenho_escola_ano') }} f
    join {{ ref('dim_escola') }} e on e.co_entidade = f.co_entidade
    left join {{ ref('dim_municipio') }} m on m.co_municipio = e.co_municipio
    left join {{ ref('dim_escola_infraestrutura') }} d on d.escola_infra_sk = f.escola_infra_sk
)
select
    *,
    case when media_geral is not null then
        ntile({{ var('faixas_de_desempenho') }}) over (partition by ano, rede, media_geral is null order by media_geral)
    end as faixa_na_rede
from escolas
