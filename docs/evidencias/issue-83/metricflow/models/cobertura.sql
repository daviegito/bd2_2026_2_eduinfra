select *, co_entidade::text as id, make_date(ano,1,1)::timestamp as data from metricas.base_cobertura
