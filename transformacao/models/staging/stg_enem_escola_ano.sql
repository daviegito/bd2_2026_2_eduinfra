{{ config(enabled=var('com_enem', false)) }}
select * from {{ source('analitico', 'enem_escola_ano') }}
