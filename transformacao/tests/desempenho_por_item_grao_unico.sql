{{ config(enabled=var('com_enem', false)) }}

-- Cada recorte aparece uma vez; grouping sets mal escritos duplicariam o total Brasil ou o de todas as redes.
select ano, uf, rede, item_codigo, situacao, area from {{ ref('desempenho_por_item') }}
group by ano, uf, rede, item_codigo, situacao, area having count(*) > 1
