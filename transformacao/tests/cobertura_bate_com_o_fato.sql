{{ config(enabled=var('com_enem', false)) }}

-- A cobertura conta as mesmas escolas que entraram no fato de desempenho, e as partes somam o total.
select c.ano from {{ ref('cobertura_enem') }} c
left join (select ano, count(*) as n from {{ ref('fato_desempenho_escola_ano') }} group by ano) f using (ano)
where c.n_identificadas_no_censo <> coalesce(f.n, 0) or c.n_identificadas_no_censo + c.n_sem_juncao <> c.n_codigos_enem
