{{ config(enabled=var('com_enem', false)) }}

-- Média publicada só com escolas suficientes, e grupo suprimido nunca carrega média nem desvio.
select 'desempenho_por_item' as modelo, uf, rede, area from {{ ref('desempenho_por_item') }}
where suprimido <> (n_escolas_com_nota < {{ var('minimo_escolas') }}) or (suprimido and (media is not null or desvio_padrao is not null)) or (not suprimido and media is null)
union all
select 'desempenho_por_recorte', uf, rede, area from {{ ref('desempenho_por_recorte') }}
where suprimido <> (n_escolas_com_nota < {{ var('minimo_escolas') }}) or (suprimido and (media is not null or desvio_padrao is not null)) or (not suprimido and media is null)
