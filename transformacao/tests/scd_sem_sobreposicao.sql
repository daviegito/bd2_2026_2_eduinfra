with intervalos as (
    select *, lag(valid_to) over (partition by co_entidade order by valid_from) as fim_anterior
    from {{ ref('dim_escola_infraestrutura') }}
)
select co_entidade, valid_from, valid_to
from intervalos
where valid_from >= valid_to or fim_anterior > valid_from
