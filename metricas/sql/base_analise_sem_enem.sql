select e.co_entidade, e.ano_censo_referencia as ano, e.rede, e.localizacao,
       e.co_municipio, m.sg_uf as uf,
       make_date(e.ano_censo_referencia, 12, 31) as referencia,
       dc.itens as itens_censo, null::jsonb as itens_enem,
       '{}'::jsonb as notas
from gold.dim_escola e
left join gold.dim_municipio m using (co_municipio)
left join gold.dim_escola_infraestrutura dc
  on dc.co_entidade = e.co_entidade
 and dc.valid_from <= least(make_timestamptz(e.ano_censo_referencia,12,31,23,59,59.999,'UTC'),now())
 and least(make_timestamptz(e.ano_censo_referencia,12,31,23,59,59.999,'UTC'),now()) < dc.valid_to
