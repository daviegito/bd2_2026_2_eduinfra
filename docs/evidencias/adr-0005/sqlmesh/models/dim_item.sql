MODEL (
  name gold_sqlmesh.dim_item,
  kind FULL,
  grain item_codigo,
  audits (not_null(columns := (item_codigo)), unique_values(columns := (item_codigo)))
);

select codigo as item_codigo, descricao, categoria
from public.item_infraestrutura;

@IF(@runtime_stage = 'evaluating', @criar_indice(item_codigo, unico := true));

@IF(@runtime_stage = 'evaluating', analyze @this_model);
