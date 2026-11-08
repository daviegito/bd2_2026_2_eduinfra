
    
    

select
    escola_infra_sk as unique_field,
    count(*) as n_records

from "eduinfra_adr0005"."gold_dbt"."dim_escola_infraestrutura"
where escola_infra_sk is not null
group by escola_infra_sk
having count(*) > 1


