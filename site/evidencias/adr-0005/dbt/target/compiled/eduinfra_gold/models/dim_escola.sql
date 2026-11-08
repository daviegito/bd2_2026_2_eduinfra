

select
    e.co_entidade,
    e.no_entidade,
    e.co_municipio,
    e.tp_dependencia,
    case e.tp_dependencia
        when 1 then 'federal' when 2 then 'estadual'
        when 3 then 'municipal' else 'privada'
    end as rede,
    case e.tp_localizacao when 1 then 'urbana' else 'rural' end as localizacao,
    e.ano_censo_referencia
from "eduinfra_adr0005"."public"."escola" e