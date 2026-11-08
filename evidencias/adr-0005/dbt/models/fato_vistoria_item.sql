{{ config(indexes=[{'columns': ['vistoria_id', 'item_codigo'], 'unique': True}, {'columns': ['item_codigo', 'situacao']}, {'columns': ['escola_infra_sk']}]) }}

select v.id as vistoria_id,
       vi.item_codigo,
       v.co_entidade,
       v.ocorrido_em,
       (v.ocorrido_em at time zone 'UTC')::date as data_ocorrencia,
       v.origem,
       d.escola_infra_sk,
       vi.situacao::text as situacao,
       vi.quantidade
from {{ source('origem', 'vistoria') }} v
join {{ source('origem', 'vistoria_item') }} vi on vi.vistoria_id = v.id
left join {{ ref('dim_escola_infraestrutura') }} d
  on d.co_entidade = v.co_entidade
 and d.valid_from <= v.ocorrido_em
 and v.ocorrido_em < d.valid_to
