{{ config(enabled=var('com_enem', false)) }}

-- Quantos códigos de escola do ENEM chegam à análise. Abaixo de dez participantes o INEP mascara o código,
-- e essas escolas ficam de fora sem que dê para redistribuí-las por UF ou rede.
select
    e.ano,
    count(*) as n_codigos_enem,
    count(d.co_entidade) as n_identificadas_no_censo,
    count(*) - count(d.co_entidade) as n_sem_juncao,
    count(*) filter (where d.co_entidade is null and e.poucos_participantes) as n_sem_juncao_poucos_participantes,
    round(100.0 * count(d.co_entidade) / count(*), 1) as pct_identificadas
from {{ ref('stg_enem_escola_ano') }} e
left join {{ ref('dim_escola') }} d on d.co_entidade = e.co_entidade
group by e.ano
