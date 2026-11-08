MODEL (
  name gold_sqlmesh.dim_escola,
  kind FULL,
  depends_on (gold_sqlmesh.dim_municipio),  -- o audit referencia esta tabela; sem isso, falha na primeira construção
  grain co_entidade,
  audits (
    not_null(columns := (co_entidade)),
    unique_values(columns := (co_entidade)),
    referencia_existe(coluna := co_municipio, destino := gold_sqlmesh.dim_municipio, campo := co_municipio)
  )
);

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
from public.escola e;

@IF(@runtime_stage = 'evaluating', @criar_indice(co_entidade, unico := true));

@IF(@runtime_stage = 'evaluating', @criar_indice(co_municipio));

@IF(@runtime_stage = 'evaluating', analyze @this_model);
