WITH sma_10001_cte AS (
  SELECT
    ano AS cobertura__ano
    , 1 AS __escolas_enem
    , case when encontrada then 1 else 0 end AS __encontradas
    , case when encontrada then 0 else 1 end AS __nao_encontradas
    , case when mascarada then 1 else 0 end AS __mascaradas
  FROM "eduinfra83"."bancada_mf"."cobertura" cobertura_src_10000
)

SELECT
  MAX(subq_7.cobertura_enem_censo) AS cobertura_enem_censo
  , MAX(subq_13.escolas_enem) AS escolas_enem
  , MAX(subq_13.encontradas) AS encontradas
  , MAX(subq_13.nao_encontradas) AS nao_encontradas
  , MAX(subq_13.mascaradas) AS mascaradas
FROM (
  SELECT
    round(100.0*encontradas/nullif(escolas_enem,0),1) AS cobertura_enem_censo
  FROM (
    SELECT
      SUM(encontradas) AS encontradas
      , SUM(escolas_enem) AS escolas_enem
    FROM (
      SELECT
        cobertura__ano
        , __escolas_enem AS escolas_enem
        , __encontradas AS encontradas
      FROM sma_10001_cte
    ) subq_2
    WHERE cobertura__ano = 2024
  ) subq_6
) subq_7
CROSS JOIN (
  SELECT
    SUM(escolas_enem) AS escolas_enem
    , SUM(encontradas) AS encontradas
    , SUM(nao_encontradas) AS nao_encontradas
    , SUM(mascaradas) AS mascaradas
  FROM (
    SELECT
      cobertura__ano
      , __escolas_enem AS escolas_enem
      , __encontradas AS encontradas
      , __nao_encontradas AS nao_encontradas
      , __mascaradas AS mascaradas
    FROM sma_10001_cte
  ) subq_9
  WHERE cobertura__ano = 2024
) subq_13
