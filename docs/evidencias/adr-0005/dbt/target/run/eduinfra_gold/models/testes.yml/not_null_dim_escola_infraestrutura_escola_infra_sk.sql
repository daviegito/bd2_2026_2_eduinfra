
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select escola_infra_sk
from "eduinfra_adr0005"."gold_dbt"."dim_escola_infraestrutura"
where escola_infra_sk is null



  
  
      
    ) dbt_internal_test