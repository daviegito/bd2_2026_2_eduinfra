
  
    

  create  table "eduinfra_adr0005"."gold_dbt"."dim_item__dbt_tmp"
  
  
    as
  
  (
    

select codigo as item_codigo, descricao, categoria
from "eduinfra_adr0005"."public"."item_infraestrutura"
  );
  