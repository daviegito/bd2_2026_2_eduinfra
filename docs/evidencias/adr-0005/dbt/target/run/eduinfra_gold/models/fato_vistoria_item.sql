
  
    

  create  table "eduinfra_adr0005"."gold_dbt"."fato_vistoria_item__dbt_tmp"
  
  
    as
  
  (
    

select v.id as vistoria_id,
       vi.item_codigo,
       v.co_entidade,
       v.ocorrido_em,
       (v.ocorrido_em at time zone 'UTC')::date as data_ocorrencia,
       v.origem,
       d.escola_infra_sk,
       vi.situacao::text as situacao,
       vi.quantidade
from "eduinfra_adr0005"."public"."vistoria" v
join "eduinfra_adr0005"."public"."vistoria_item" vi on vi.vistoria_id = v.id
left join "eduinfra_adr0005"."gold_dbt"."dim_escola_infraestrutura" d
  on d.co_entidade = v.co_entidade
 and d.valid_from <= v.ocorrido_em
 and v.ocorrido_em < d.valid_to
  );
  