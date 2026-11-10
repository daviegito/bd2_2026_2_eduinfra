select vistoria_id, item_codigo
from {{ ref('fato_vistoria_item') }}
group by vistoria_id, item_codigo
having count(*) > 1
