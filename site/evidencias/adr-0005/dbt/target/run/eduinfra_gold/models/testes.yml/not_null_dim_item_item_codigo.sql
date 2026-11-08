
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select item_codigo
from "eduinfra_adr0005"."gold_dbt"."dim_item"
where item_codigo is null



  
  
      
    ) dbt_internal_test