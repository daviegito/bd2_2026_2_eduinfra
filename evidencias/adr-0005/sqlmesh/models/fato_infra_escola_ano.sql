MODEL (
  name gold_sqlmesh.fato_infra_escola_ano,
  kind FULL,
  grain (co_entidade, ano),
  audits (
    unique_combination_of_columns(columns := (co_entidade, ano)),
    accepted_range(column := pct_existentes, min_v := 0, max_v := 100)
  )
);

with anos as (
    select distinct co_entidade,
           extract(year from ocorrido_em at time zone 'UTC')::int as ano
    from public.vistoria
),
referencia as (
    select co_entidade, ano,
           least(make_timestamptz(ano, 12, 31, 23, 59, 59.999, 'UTC'), now()) as em
    from anos
)
select r.co_entidade,
       r.ano,
       d.escola_infra_sk,
       coalesce(d.n_itens_conhecidos, 0) as n_itens_conhecidos,
       coalesce(d.n_existentes, 0) as n_existentes,
       coalesce(d.n_inexistentes, 0) as n_inexistentes,
       coalesce(d.n_em_obra, 0) as n_em_obra,
       coalesce(d.n_inutilizaveis, 0) as n_inutilizaveis,
       round(100.0 * d.n_existentes / nullif(d.n_itens_conhecidos, 0), 2) as pct_existentes,
       @EACH(@itens, x -> jsonb_extract_path_text(d.itens, @x) = 'existente' as @x)
from referencia r
left join gold_sqlmesh.dim_escola_infraestrutura d
  on d.co_entidade = r.co_entidade
 and d.valid_from <= r.em
 and r.em < d.valid_to;

@IF(@runtime_stage = 'evaluating', @criar_indice(co_entidade, ano, unico := true));

@IF(@runtime_stage = 'evaluating', @criar_indice(ano));

@IF(@runtime_stage = 'evaluating', analyze @this_model);
