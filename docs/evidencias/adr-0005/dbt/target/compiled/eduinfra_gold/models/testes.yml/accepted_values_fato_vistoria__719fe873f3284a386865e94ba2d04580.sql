
    
    

with all_values as (

    select
        situacao as value_field,
        count(*) as n_records

    from "eduinfra_adr0005"."gold_dbt"."fato_vistoria_item"
    group by situacao

)

select *
from all_values
where value_field not in (
    'existente','inexistente','em_obra','inutilizavel','nao_informado'
)


