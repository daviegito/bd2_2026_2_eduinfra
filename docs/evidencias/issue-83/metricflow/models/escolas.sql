select e.co_entidade::text || '|' || a.area || '|' || i.item_codigo as id,
 make_date(e.ano,1,1)::timestamp as data, e.ano,e.uf,e.co_municipio,e.rede,e.localizacao,
 a.area,i.item_codigo as item,
 coalesce(e.itens_censo->>i.item_codigo,'nao_informado') as situacao_censo,
 coalesce(e.itens_enem->>i.item_codigo,'nao_informado') as situacao_enem,
 (e.notas->>a.area)::numeric as nota
from metricas.base_analise e cross join gold.dim_item i
cross join (values ('media_geral'),('media_cn'),('media_ch'),('media_lc'),('media_mt'),('media_redacao')) a(area)
