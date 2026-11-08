
    
    

with child as (
    select co_municipio as from_field
    from "eduinfra_adr0005"."gold_dbt"."dim_escola"
    where co_municipio is not null
),

parent as (
    select co_municipio as to_field
    from "eduinfra_adr0005"."gold_dbt"."dim_municipio"
)

select
    from_field

from child
left join parent
    on child.from_field = parent.to_field

where parent.to_field is null


