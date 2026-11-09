#!/usr/bin/env bash
# Compara, tabela a tabela, a Gold de dois schemas do banco eduinfra_adr0005: contagem de
# linhas e soma dos hashes de cada linha inteira. Iguais = mesmo conteúdo, em qualquer ordem.
#   bash docs/evidencias/adr-0005/comparar_resultados.sh gold gold_dbt
set -euo pipefail

set -a && source .env && set +a
A=$1
B=$2
TABELAS="dim_item dim_municipio dim_escola dim_escola_infraestrutura fato_vistoria_item fato_infra_escola_ano"

impressao() {
    docker compose exec -T banco psql -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d eduinfra_adr0005 \
        -c "select count(*) || ' linhas, hash ' || coalesce(sum(hashtext(t::text)::bigint), 0) from $1.$2 t"
}

diferentes=0
for tabela in $TABELAS; do
    a=$(impressao "$A" "$tabela")
    b=$(impressao "$B" "$tabela")
    if [ "$a" = "$b" ]; then situacao="iguais"; else situacao="DIFERENTES ($B: $b)"; diferentes=1; fi
    echo "$tabela: $a -> $situacao"
done

exit $diferentes
