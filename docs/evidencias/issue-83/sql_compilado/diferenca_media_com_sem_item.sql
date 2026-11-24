WITH cm_11_cte AS (
  SELECT
    escola__rede
    , SUM(n_existente) AS n_existente
    , SUM(n_inexistente) AS n_inexistente
    , SUM(soma_existente) AS soma_existente
    , SUM(soma_inexistente) AS soma_inexistente
  FROM (
    SELECT
      ano AS escola__ano
      , rede AS escola__rede
      , area AS escola__area
      , item AS escola__item
      , case when nota is not null and situacao_enem='existente' then 1 else 0 end AS n_existente
      , case when situacao_enem='existente' then coalesce(nota,0) else 0 end AS soma_existente
      , case when nota is not null and situacao_enem='inexistente' then 1 else 0 end AS n_inexistente
      , case when situacao_enem='inexistente' then coalesce(nota,0) else 0 end AS soma_inexistente
    FROM "eduinfra83"."bancada_mf"."escolas" escolas_src_10000
  ) subq_2
  WHERE escola__ano = 2024 and escola__area = 'media_geral' and escola__item = 'biblioteca'
  GROUP BY
    escola__rede
)

SELECT
  COALESCE(subq_7.escola__rede, cm_11_cte.escola__rede) AS escola__rede
  , MAX(subq_7.diferenca_media_com_sem_item) AS diferenca_media_com_sem_item
  , MAX(cm_11_cte.n_existente) AS n_existente
  , MAX(cm_11_cte.n_inexistente) AS n_inexistente
  , MAX(cm_11_cte.soma_existente) AS soma_existente
  , MAX(cm_11_cte.soma_inexistente) AS soma_inexistente
FROM (
  SELECT
    escola__rede
    , case when n_existente >= 10 and n_inexistente >= 10 then soma_existente/nullif(n_existente,0)-soma_inexistente/nullif(n_inexistente,0) end AS diferenca_media_com_sem_item
  FROM (
    SELECT
      escola__rede
      , n_existente
      , n_inexistente
      , soma_existente
      , soma_inexistente
    FROM cm_11_cte
  ) subq_6
) subq_7
FULL OUTER JOIN
  cm_11_cte
ON
  subq_7.escola__rede = cm_11_cte.escola__rede
GROUP BY
  COALESCE(subq_7.escola__rede, cm_11_cte.escola__rede)
