{{ config(enabled=var('com_enem', false)) }}

-- O total Brasil de todas as redes conta cada escola do Censo uma vez por item, com ou sem ENEM.
select i.ano, i.item_codigo from {{ ref('infra_por_uf') }} i
where i.uf = 'BR' and i.rede = 'todas'
group by i.ano, i.item_codigo
having sum(i.n_escolas) <> (select count(*) from {{ ref('dim_escola') }} e where e.ano_censo_referencia = i.ano)
