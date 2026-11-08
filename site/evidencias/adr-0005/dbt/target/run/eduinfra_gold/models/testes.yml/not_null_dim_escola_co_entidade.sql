
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select co_entidade
from "eduinfra_adr0005"."gold_dbt"."dim_escola"
where co_entidade is null



  
  
      
    ) dbt_internal_test