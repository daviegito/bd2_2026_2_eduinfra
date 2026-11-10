select co_entidade, ano
from {{ ref('fato_infra_escola_ano') }}
group by co_entidade, ano
having count(*) > 1
