
  
    

  create  table "eduinfra_adr0005"."gold_dbt"."dim_municipio__dbt_tmp"
  
  
    as
  
  (
    

select co_municipio, no_municipio, sg_uf, co_uf
from "eduinfra_adr0005"."public"."municipio"
  );
  