

with inicios as (
    select co_entidade, desde as valid_from, max(vistoria_id) as vistoria_origem_id
    from "eduinfra_adr0005"."gold_dbt"."stg_mudanca_item"
    group by co_entidade, desde
),
perfis as (
    select i.co_entidade, i.valid_from, i.vistoria_origem_id,
           count(*) as n_itens_conhecidos,
           count(*) filter (where m.situacao = 'existente') as n_existentes,
           count(*) filter (where m.situacao = 'inexistente') as n_inexistentes,
           count(*) filter (where m.situacao = 'em_obra') as n_em_obra,
           count(*) filter (where m.situacao = 'inutilizavel') as n_inutilizaveis,
           jsonb_object_agg(m.item_codigo, m.situacao) as itens,
           coalesce(jsonb_object_agg(m.item_codigo, m.quantidade)
                    filter (where m.quantidade is not null), '{}'::jsonb) as quantidades
    from inicios i
    join "eduinfra_adr0005"."gold_dbt"."stg_mudanca_item" m
      on m.co_entidade = i.co_entidade
     and m.desde <= i.valid_from
     and i.valid_from < m.ate
    group by i.co_entidade, i.valid_from, i.vistoria_origem_id
)
select
    hashtextextended(
        co_entidade::text || '|' || to_char(valid_from at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'), 0
    ) as escola_infra_sk,
    co_entidade,
    valid_from,
    coalesce(lead(valid_from) over proxima, 'infinity'::timestamptz) as valid_to,
    lead(valid_from) over proxima is null as is_current,
    vistoria_origem_id,
    n_itens_conhecidos, n_existentes, n_inexistentes, n_em_obra, n_inutilizaveis,
    itens, quantidades
from perfis
window proxima as (partition by co_entidade order by valid_from)