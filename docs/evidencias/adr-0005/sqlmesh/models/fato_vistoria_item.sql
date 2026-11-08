MODEL (
  name gold_sqlmesh.fato_vistoria_item,
  kind FULL,
  depends_on (gold_sqlmesh.dim_escola_infraestrutura),  -- o audit referencia esta tabela; sem isso, falha na primeira construção
  grain (vistoria_id, item_codigo),
  audits (unique_combination_of_columns(columns := (vistoria_id, item_codigo)), accepted_values(column := situacao, is_in := ('existente', 'inexistente', 'em_obra', 'inutilizavel', 'nao_informado')),
    referencia_existe(coluna := escola_infra_sk, destino := gold_sqlmesh.dim_escola_infraestrutura, campo := escola_infra_sk)
  )
);

select v.id as vistoria_id,
       vi.item_codigo,
       v.co_entidade,
       v.ocorrido_em,
       (v.ocorrido_em at time zone 'UTC')::date as data_ocorrencia,
       v.origem,
       d.escola_infra_sk,
       vi.situacao::text as situacao,
       vi.quantidade
from public.vistoria v
join public.vistoria_item vi on vi.vistoria_id = v.id
left join gold_sqlmesh.dim_escola_infraestrutura d
  on d.co_entidade = v.co_entidade
 and d.valid_from <= v.ocorrido_em
 and v.ocorrido_em < d.valid_to;

@IF(@runtime_stage = 'evaluating', @criar_indice(vistoria_id, item_codigo, unico := true));

@IF(@runtime_stage = 'evaluating', @criar_indice(item_codigo, situacao));

@IF(@runtime_stage = 'evaluating', @criar_indice(escola_infra_sk));

@IF(@runtime_stage = 'evaluating', analyze @this_model);
