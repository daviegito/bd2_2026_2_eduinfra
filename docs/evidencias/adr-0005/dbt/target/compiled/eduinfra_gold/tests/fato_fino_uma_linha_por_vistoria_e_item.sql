select vistoria_id, item_codigo
from "eduinfra_adr0005"."gold_dbt"."fato_vistoria_item"
group by vistoria_id, item_codigo
having count(*) > 1