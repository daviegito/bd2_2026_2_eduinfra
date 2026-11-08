

with eventos as (
    select v.co_entidade, vi.item_codigo, vi.situacao::text as situacao, vi.quantidade,
           v.ocorrido_em as desde, v.id as vistoria_id
    from "eduinfra_adr0005"."public"."vistoria" v
    join "eduinfra_adr0005"."public"."vistoria_item" vi on vi.vistoria_id = v.id
    where vi.situacao <> 'nao_informado'
),
com_inicio_de_trecho as (
    select *,
           case when situacao is distinct from lag(situacao) over janela then 1 else 0 end as inicia_trecho
    from eventos
    window janela as (partition by co_entidade, item_codigo order by desde, vistoria_id)
),
com_trecho as (
    select *,
           sum(inicia_trecho) over (
               partition by co_entidade, item_codigo order by desde, vistoria_id
           ) as trecho
    from com_inicio_de_trecho
),
-- Quantidade vigente: dentro de um trecho de mesma situação, uma revisita que não informa
-- a quantidade não apaga a última conhecida (mesma ideia do nao_informado). count() só
-- avança nas linhas com quantidade, então cada grupo tem exatamente uma linha informada.
com_grupo_de_quantidade as (
    select *,
           count(quantidade) over (
               partition by co_entidade, item_codigo, trecho order by desde, vistoria_id
           ) as grupo_quantidade
    from com_trecho
),
com_quantidade_vigente as (
    select *,
           max(quantidade) over (
               partition by co_entidade, item_codigo, trecho, grupo_quantidade
           ) as quantidade_vigente
    from com_grupo_de_quantidade
),
comparados as (
    select *,
           lag(situacao) over janela as situacao_anterior,
           lag(quantidade_vigente) over janela as quantidade_anterior,
           row_number() over janela as ordem
    from com_quantidade_vigente
    window janela as (partition by co_entidade, item_codigo order by desde, vistoria_id)
),
mudancas as (
    select co_entidade, item_codigo, situacao, quantidade_vigente as quantidade, desde, vistoria_id
    from comparados
    where ordem = 1
       or situacao is distinct from situacao_anterior
       or quantidade_vigente is distinct from quantidade_anterior
)
select *,
       coalesce(
           lead(desde) over (partition by co_entidade, item_codigo order by desde, vistoria_id),
           'infinity'::timestamptz
       ) as ate
from mudancas