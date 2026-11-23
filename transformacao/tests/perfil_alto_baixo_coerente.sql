{{ config(enabled=var('com_enem', false)) }}

-- Percentuais do perfil alto e baixo ficam entre 0 e 100, e a ordem 1 é sempre a maior diferença da rede.
select ano, rede, item_codigo from {{ ref('perfil_alto_baixo_desempenho') }}
where pct_alto_com_item not between 0 and 100 or pct_baixo_com_item not between 0 and 100
   or diferenca_pp <> pct_alto_com_item - pct_baixo_com_item
