{{ config(enabled=var('com_enem', false), indexes=[{'columns': ['ano', 'uf', 'rede', 'area']}]) }}

-- A pergunta de gestão em uma linha: média das escolas que têm o item contra a das que declararam não ter.
-- Em obra, inutilizável e não informado ficam fora dos dois lados da comparação.
with lados as (
    select
        ano, uf, rede, item_codigo, area,
        max(media) filter (where situacao = 'existente') as media_com_item,
        coalesce(max(n_escolas_com_nota) filter (where situacao = 'existente'), 0) as n_com_item,
        max(media) filter (where situacao = 'inexistente') as media_sem_item,
        coalesce(max(n_escolas_com_nota) filter (where situacao = 'inexistente'), 0) as n_sem_item
    from {{ ref('desempenho_por_item') }}
    where situacao in ('existente', 'inexistente')
    group by ano, uf, rede, item_codigo, area
)
select
    l.ano, l.uf, l.rede, l.item_codigo, i.descricao as item_descricao, l.area,
    l.media_com_item, l.n_com_item, l.media_sem_item, l.n_sem_item,
    l.media_com_item - l.media_sem_item as diferenca
from lados l
join {{ ref('dim_item') }} i on i.item_codigo = l.item_codigo
