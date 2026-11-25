{{ config(enabled=var('com_enem', false), indexes=[{'columns': ['ano', 'rede', 'area']}]) }}

-- Faixa de desempenho em cada área da prova (#94): o topo em Matemática não é o mesmo topo da Redação.
-- Como em int_desempenho_escola, a faixa é calculada dentro da rede e o desempate é pelo código da escola.
with notas as (
    select
        e.co_entidade, e.ano, e.rede, e.sg_uf, e.localizacao,
        e.n_itens_conhecidos, e.n_existentes, e.itens,
        n.area, n.nota
    from {{ ref('int_desempenho_escola') }} e
    cross join lateral (values
        ('media_geral', e.media_geral), ('media_cn', e.media_cn), ('media_ch', e.media_ch),
        ('media_lc', e.media_lc), ('media_mt', e.media_mt), ('media_redacao', e.media_redacao)
    ) as n(area, nota)
    where n.nota is not null
)
select
    *,
    ntile({{ var('faixas_de_desempenho') }}) over (partition by ano, rede, area order by nota, co_entidade) as faixa
from notas
