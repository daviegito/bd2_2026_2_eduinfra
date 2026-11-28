WITH sma_10002_cte AS (
  SELECT
    ano AS escola__ano
    , area AS escola__area
    , item AS escola__item
    , 1 AS __n_censo
    , case when situacao_censo='existente' then 1 else 0 end AS __censo_existente
    , case when situacao_censo='inexistente' then 1 else 0 end AS __censo_inexistente
    , case when situacao_censo='em_obra' then 1 else 0 end AS __censo_em_obra
    , case when situacao_censo='inutilizavel' then 1 else 0 end AS __censo_inutilizavel
    , case when situacao_censo='nao_informado' then 1 else 0 end AS __censo_nao_informado
  FROM "eduinfra83"."bancada_mf"."escolas" escolas_src_10000
)

SELECT
  MAX(subq_6.n_censo) AS n_censo
  , MAX(subq_6.censo_existente) AS censo_existente
  , MAX(subq_6.censo_inexistente) AS censo_inexistente
  , MAX(subq_6.censo_em_obra) AS censo_em_obra
  , MAX(subq_6.censo_inutilizavel) AS censo_inutilizavel
  , MAX(subq_6.censo_nao_informado) AS censo_nao_informado
  , MAX(subq_13.pct_existente) AS pct_existente
  , MAX(subq_13.pct_inexistente) AS pct_inexistente
  , MAX(subq_13.pct_em_obra) AS pct_em_obra
  , MAX(subq_13.pct_inutilizavel) AS pct_inutilizavel
  , MAX(subq_13.pct_nao_informado) AS pct_nao_informado
FROM (
  SELECT
    SUM(n_censo) AS n_censo
    , SUM(censo_existente) AS censo_existente
    , SUM(censo_inexistente) AS censo_inexistente
    , SUM(censo_em_obra) AS censo_em_obra
    , SUM(censo_inutilizavel) AS censo_inutilizavel
    , SUM(censo_nao_informado) AS censo_nao_informado
  FROM (
    SELECT
      escola__ano
      , escola__area
      , escola__item
      , __n_censo AS n_censo
      , __censo_existente AS censo_existente
      , __censo_inexistente AS censo_inexistente
      , __censo_em_obra AS censo_em_obra
      , __censo_inutilizavel AS censo_inutilizavel
      , __censo_nao_informado AS censo_nao_informado
    FROM sma_10002_cte
  ) subq_2
  WHERE escola__ano = 2024 and escola__area = 'media_geral' and escola__item = 'biblioteca'
) subq_6
CROSS JOIN (
  SELECT
    round(100.0*censo_existente/nullif(n_censo,0),1) AS pct_existente
    , round(100.0*censo_inexistente/nullif(n_censo,0),1) AS pct_inexistente
    , round(100.0*censo_em_obra/nullif(n_censo,0),1) AS pct_em_obra
    , round(100.0*censo_inutilizavel/nullif(n_censo,0),1) AS pct_inutilizavel
    , round(100.0*censo_nao_informado/nullif(n_censo,0),1) AS pct_nao_informado
  FROM (
    SELECT
      SUM(censo_existente) AS censo_existente
      , SUM(n_censo) AS n_censo
      , SUM(censo_inexistente) AS censo_inexistente
      , SUM(censo_em_obra) AS censo_em_obra
      , SUM(censo_inutilizavel) AS censo_inutilizavel
      , SUM(censo_nao_informado) AS censo_nao_informado
    FROM (
      SELECT
        escola__ano
        , escola__area
        , escola__item
        , __n_censo AS n_censo
        , __censo_existente AS censo_existente
        , __censo_inexistente AS censo_inexistente
        , __censo_em_obra AS censo_em_obra
        , __censo_inutilizavel AS censo_inutilizavel
        , __censo_nao_informado AS censo_nao_informado
      FROM sma_10002_cte
    ) subq_8
    WHERE escola__ano = 2024 and escola__area = 'media_geral' and escola__item = 'biblioteca'
  ) subq_12
) subq_13
