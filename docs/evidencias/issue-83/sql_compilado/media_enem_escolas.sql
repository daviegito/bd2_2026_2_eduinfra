WITH sma_10002_cte AS (
  SELECT
    ano AS escola__ano
    , area AS escola__area
    , item AS escola__item
    , case when nota is not null then 1 else 0 end AS __n_validas
    , coalesce(nota,0) AS __soma_notas
    , coalesce(nota*nota,0) AS __soma_quadrados
  FROM "eduinfra83"."bancada_mf"."escolas" escolas_src_10000
)

SELECT
  MAX(subq_7.media_enem_escolas) AS media_enem_escolas
  , MAX(subq_7.desvio_padrao) AS desvio_padrao
  , MAX(subq_13.n_validas) AS n_validas
FROM (
  SELECT
    case when n_validas >= 10 then soma_notas / nullif(n_validas,0) end AS media_enem_escolas
    , case when n_validas >= 10 then sqrt(greatest(0,(soma_quadrados-soma_notas*soma_notas/nullif(n_validas,0))/nullif(n_validas-1,0))) end AS desvio_padrao
  FROM (
    SELECT
      SUM(n_validas) AS n_validas
      , SUM(soma_notas) AS soma_notas
      , SUM(soma_quadrados) AS soma_quadrados
    FROM (
      SELECT
        escola__ano
        , escola__area
        , escola__item
        , __n_validas AS n_validas
        , __soma_notas AS soma_notas
        , __soma_quadrados AS soma_quadrados
      FROM sma_10002_cte
    ) subq_2
    WHERE escola__ano = 2024 and escola__area = 'media_geral' and escola__item = 'biblioteca'
  ) subq_6
) subq_7
CROSS JOIN (
  SELECT
    SUM(n_validas) AS n_validas
  FROM (
    SELECT
      escola__ano
      , escola__area
      , escola__item
      , __n_validas AS n_validas
    FROM sma_10002_cte
  ) subq_9
  WHERE escola__ano = 2024 and escola__area = 'media_geral' and escola__item = 'biblioteca'
) subq_13
