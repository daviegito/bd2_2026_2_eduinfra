{{ config(enabled=var('com_enem', false)) }}

-- Percentuais do perfil entre 0 e 100, diferença igual a alto menos baixo e uma linha por característica.
select ano, rede, area, tipo, caracteristica from {{ ref('perfil_alto_baixo_desempenho') }}
where pct_alto not between 0 and 100 or pct_baixo not between 0 and 100
   or diferenca_pp <> pct_alto - pct_baixo
   or n_alto_com > n_alto_conhecido or n_alto_conhecido > n_alto
   or n_baixo_com > n_baixo_conhecido or n_baixo_conhecido > n_baixo
union all
select ano, rede, area, tipo, caracteristica from {{ ref('perfil_alto_baixo_desempenho') }}
group by ano, rede, area, tipo, caracteristica
having count(*) > 1
