create schema if not exists gold; create schema if not exists entrada_transformacao;
 create table gold.dim_municipio as select 5300108 as co_municipio, 'DF'::text as sg_uf union all select 3550308,'SP';
 create table gold.dim_item as select 'biblioteca'::text as item_codigo union all select 'internet';
 create table gold.dim_escola as select i as co_entidade,2024 as ano_censo_referencia,
 case when i%4=0 then 'municipal' when i%4=1 then 'estadual' when i%4=2 then 'federal' else 'privada' end as rede,
 case when i%7=0 then 'rural' else 'urbana' end as localizacao,
 case when i%3=0 then 5300108 else 3550308 end as co_municipio from generate_series(1,6000) i;
 create table gold.dim_escola_infraestrutura as select co_entidade,co_entidade::bigint as escola_infra_sk,
 '2024-01-01'::timestamptz as valid_from,'infinity'::timestamptz as valid_to,
 case when co_entidade%11=0 then '{}'::jsonb else jsonb_build_object('biblioteca',
 (array['existente','inexistente','em_obra','inutilizavel','nao_informado'])[1+co_entidade%5], 'internet','existente') end as itens
 from gold.dim_escola;
 create table gold.fato_desempenho_escola_ano as select co_entidade,2024 as ano,co_entidade::bigint as escola_infra_sk,
 '2024-05-29'::date as data_referencia_infraestrutura,
 case when co_entidade%13=0 then null else 400+(co_entidade%300)::numeric end as media_geral,
 400+(co_entidade%301)::numeric as media_cn, 420+(co_entidade%290)::numeric as media_ch,
 430+(co_entidade%260)::numeric as media_lc, 440+(co_entidade%240)::numeric as media_mt,
 400+(co_entidade%350)::numeric as media_redacao
 from gold.dim_escola where co_entidade%6<>0;
 create table entrada_transformacao.enem_escola_ano as select co_entidade,ano,false as poucos_participantes from gold.fato_desempenho_escola_ano
 union all select i,2024,true from generate_series(7001,7100) i;
 create unique index on gold.dim_escola(co_entidade);
 create unique index on gold.dim_escola_infraestrutura(co_entidade);
 create unique index on gold.fato_desempenho_escola_ano(co_entidade,ano);
 analyze;