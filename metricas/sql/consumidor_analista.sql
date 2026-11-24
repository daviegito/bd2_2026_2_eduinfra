-- Segundo consumidor da camada semântica (issue #84): consulta analítica para
-- conferência direta das mesmas métricas exibidas no painel, sem duplicar fórmulas.

select * from metricas.definicoes order by nome;

select 'media_enem_escolas' as metrica,
       metricas.media_enem_escolas('{"ano":2024,"uf":"DF","area":"media_geral"}'::jsonb) as resultado;

select 'diferenca_media_com_sem_item' as metrica,
       metricas.diferenca_media_com_sem_item('{"ano":2024,"uf":"DF","item":"biblioteca"}'::jsonb) as resultado;

select 'pct_escolas_por_situacao_do_item' as metrica,
       metricas.pct_escolas_por_situacao_do_item('{"ano":2024,"uf":"DF","item":"biblioteca"}'::jsonb) as resultado;

select 'cobertura_enem_censo' as metrica,
       metricas.cobertura_enem_censo('{"ano":2024}'::jsonb) as resultado;
