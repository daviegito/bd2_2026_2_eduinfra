
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    

select
    co_entidade as unique_field,
    count(*) as n_records

from "eduinfra_adr0005"."gold_dbt"."dim_escola"
where co_entidade is not null
group by co_entidade
having count(*) > 1



  
  
      
    ) dbt_internal_test