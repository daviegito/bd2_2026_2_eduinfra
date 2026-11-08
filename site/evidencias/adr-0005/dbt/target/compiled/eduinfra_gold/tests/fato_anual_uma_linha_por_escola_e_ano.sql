select co_entidade, ano
from "eduinfra_adr0005"."gold_dbt"."fato_infra_escola_ano"
group by co_entidade, ano
having count(*) > 1