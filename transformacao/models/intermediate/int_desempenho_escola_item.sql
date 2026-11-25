{{ config(enabled=var('com_enem', false), indexes=[{'columns': ['ano', 'item_codigo']}]) }}

-- Uma linha por escola e item: "nao_informado" fica explícito para não ser lido como "não possui".
select
    e.co_entidade, e.ano, e.rede, e.sg_uf, e.faixa_na_rede,
    e.media_geral, e.media_cn, e.media_ch, e.media_lc, e.media_mt, e.media_redacao,
    i.item_codigo,
    coalesce(e.itens ->> i.item_codigo, 'nao_informado') as situacao
from {{ ref('int_desempenho_escola') }} e
cross join {{ ref('dim_item') }} i
