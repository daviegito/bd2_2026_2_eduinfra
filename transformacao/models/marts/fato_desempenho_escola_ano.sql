{{ config(enabled=var('com_enem', false), indexes=[{'columns': ['co_entidade', 'ano'], 'unique': True}]) }}

select e.*, d.escola_infra_sk
from {{ ref('stg_enem_escola_ano') }} e
join {{ ref('dim_escola') }} escola using (co_entidade)
left join {{ ref('dim_escola_infraestrutura') }} d
  on d.co_entidade = e.co_entidade
 and d.valid_from <= e.data_referencia_infraestrutura::timestamp at time zone 'UTC'
 and e.data_referencia_infraestrutura::timestamp at time zone 'UTC' < d.valid_to
