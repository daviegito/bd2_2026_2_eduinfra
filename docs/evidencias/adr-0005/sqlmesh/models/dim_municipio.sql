MODEL (
  name gold_sqlmesh.dim_municipio,
  kind FULL,
  grain co_municipio
);

select co_municipio, no_municipio, sg_uf, co_uf
from public.municipio;

@IF(@runtime_stage = 'evaluating', @criar_indice(co_municipio, unico := true));

@IF(@runtime_stage = 'evaluating', analyze @this_model);
