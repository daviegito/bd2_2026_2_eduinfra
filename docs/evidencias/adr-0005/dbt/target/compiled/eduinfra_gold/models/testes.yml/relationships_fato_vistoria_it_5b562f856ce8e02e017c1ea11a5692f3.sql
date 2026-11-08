
    
    

with child as (
    select escola_infra_sk as from_field
    from "eduinfra_adr0005"."gold_dbt"."fato_vistoria_item"
    where escola_infra_sk is not null
),

parent as (
    select escola_infra_sk as to_field
    from "eduinfra_adr0005"."gold_dbt"."dim_escola_infraestrutura"
)

select
    from_field

from child
left join parent
    on child.from_field = parent.to_field

where parent.to_field is null


