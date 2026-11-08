
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  select co_entidade, ano
from "eduinfra_adr0005"."gold_dbt"."fato_infra_escola_ano"
group by co_entidade, ano
having count(*) > 1
  
  
      
    ) dbt_internal_test