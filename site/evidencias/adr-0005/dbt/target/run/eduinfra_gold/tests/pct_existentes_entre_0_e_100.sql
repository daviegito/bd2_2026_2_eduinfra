
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  select *
from "eduinfra_adr0005"."gold_dbt"."fato_infra_escola_ano"
where pct_existentes < 0 or pct_existentes > 100
  
  
      
    ) dbt_internal_test