

with anos as (
    select distinct co_entidade,
           extract(year from ocorrido_em at time zone 'UTC')::int as ano
    from "eduinfra_adr0005"."public"."vistoria"
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
       (d.itens ->> 'acessibilidade_rampas') = 'existente' as acessibilidade_rampas,
       (d.itens ->> 'agua_potavel') = 'existente' as agua_potavel,
       (d.itens ->> 'almoxarifado') = 'existente' as almoxarifado,
       (d.itens ->> 'banheiro_pne') = 'existente' as banheiro_pne,
       (d.itens ->> 'biblioteca') = 'existente' as biblioteca,
       (d.itens ->> 'cozinha') = 'existente' as cozinha,
       (d.itens ->> 'energia_rede_publica') = 'existente' as energia_rede_publica,
       (d.itens ->> 'esgoto_rede_publica') = 'existente' as esgoto_rede_publica,
       (d.itens ->> 'internet') = 'existente' as internet,
       (d.itens ->> 'internet_alunos') = 'existente' as internet_alunos,
       (d.itens ->> 'lab_ciencias') = 'existente' as lab_ciencias,
       (d.itens ->> 'lab_informatica') = 'existente' as lab_informatica,
       (d.itens ->> 'parque_infantil') = 'existente' as parque_infantil,
       (d.itens ->> 'patio_coberto') = 'existente' as patio_coberto,
       (d.itens ->> 'quadra_esportes') = 'existente' as quadra_esportes,
       (d.itens ->> 'refeitorio') = 'existente' as refeitorio,
       (d.itens ->> 'sala_leitura') = 'existente' as sala_leitura
from referencia r
left join "eduinfra_adr0005"."gold_dbt"."dim_escola_infraestrutura" d
  on d.co_entidade = r.co_entidade
 and d.valid_from <= r.em
 and r.em < d.valid_to