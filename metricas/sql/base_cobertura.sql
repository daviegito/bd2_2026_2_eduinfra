select f.ano, f.co_entidade,
       e.co_entidade is not null as encontrada,
       e.co_entidade is null and f.poucos_participantes as mascarada
from entrada_transformacao.enem_escola_ano f
left join gold.dim_escola e
  on e.co_entidade = f.co_entidade and e.ano_censo_referencia = f.ano
