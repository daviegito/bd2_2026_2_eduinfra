{{ config(indexes=[{'columns': ['vistoria_id', 'item_codigo'], 'unique': True}, {'columns': ['item_codigo', 'situacao']}, {'columns': ['escola_infra_sk']}]) }}

select e.vistoria_id,
       e.item_codigo,
       e.co_entidade,
       e.ocorrido_em,
       (e.ocorrido_em at time zone 'UTC')::date as data_ocorrencia,
       e.origem,
       d.escola_infra_sk,
       e.situacao,
       e.quantidade
from {{ ref('stg_eventos_vistoria') }} e
left join {{ ref('dim_escola_infraestrutura') }} d
  on d.co_entidade = e.co_entidade
 and d.valid_from <= e.ocorrido_em
 and e.ocorrido_em < d.valid_to
