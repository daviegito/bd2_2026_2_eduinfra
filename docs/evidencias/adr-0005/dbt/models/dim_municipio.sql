{{ config(indexes=[{'columns': ['co_municipio'], 'unique': True}]) }}

select co_municipio, no_municipio, sg_uf, co_uf
from {{ source('origem', 'municipio') }}
