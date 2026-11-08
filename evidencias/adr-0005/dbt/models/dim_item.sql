{{ config(indexes=[{'columns': ['item_codigo'], 'unique': True}]) }}

select codigo as item_codigo, descricao, categoria
from {{ source('origem', 'item_infraestrutura') }}
