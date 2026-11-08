
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    

select
    item_codigo as unique_field,
    count(*) as n_records

from "eduinfra_adr0005"."gold_dbt"."dim_item"
where item_codigo is not null
group by item_codigo
having count(*) > 1



  
  
      
    ) dbt_internal_test