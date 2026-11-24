with referencias as (
    select ano, max(data_referencia_infraestrutura) as referencia
    from gold.fato_desempenho_escola_ano group by ano
)
select e.co_entidade, e.ano_censo_referencia as ano, e.rede, e.localizacao,
       e.co_municipio, m.sg_uf as uf, r.referencia,
       dc.itens as itens_censo, de.itens as itens_enem,
       jsonb_build_object(
           'media_geral', f.media_geral, 'media_cn', f.media_cn,
           'media_ch', f.media_ch, 'media_lc', f.media_lc,
           'media_mt', f.media_mt, 'media_redacao', f.media_redacao
       ) as notas
from gold.dim_escola e
left join gold.dim_municipio m using (co_municipio)
left join referencias r on r.ano = e.ano_censo_referencia
left join gold.fato_desempenho_escola_ano f
  on f.co_entidade = e.co_entidade and f.ano = e.ano_censo_referencia
left join gold.dim_escola_infraestrutura dc
  on dc.co_entidade = e.co_entidade
 and dc.valid_from <= r.referencia::timestamp at time zone 'UTC'
 and r.referencia::timestamp at time zone 'UTC' < dc.valid_to
left join gold.dim_escola_infraestrutura de
  on de.co_entidade = f.co_entidade and de.escola_infra_sk = f.escola_infra_sk
