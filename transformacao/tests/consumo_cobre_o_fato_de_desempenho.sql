{{ config(enabled=var('com_enem', false)) }}

-- A tabela larga só reorganiza o fato: nenhuma escola pode sumir nem se repetir na junção com as dimensões.
select co_entidade, ano
from (
    select co_entidade, ano from {{ ref('fato_desempenho_escola_ano') }}
    union all
    select co_entidade, ano from {{ ref('escola_desempenho_infra') }}
) ambos
group by co_entidade, ano
having count(*) <> 2
