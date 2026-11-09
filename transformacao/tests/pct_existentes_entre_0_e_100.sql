select *
from {{ ref('fato_infra_escola_ano') }}
where pct_existentes < 0 or pct_existentes > 100
