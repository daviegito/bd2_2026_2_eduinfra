#!/usr/bin/env bash
# Reconstrói a Gold do recorte (preparar_amostra.sh) com as três alternativas do ADR 0005,
# REPETICOES vezes cada, e imprime o tempo de parede de cada execução. As ferramentas rodam
# fora do contêiner, contra a porta publicada do banco.
#
#   bash docs/evidencias/adr-0005/medir.sh
#
# Comandos padrão com uv; troque por outro executável com NULA, DBT e SQLMESH.
# O SQLMesh precisa de um primeiro `sqlmesh plan --auto-apply` antes da medição.
set -euo pipefail

set -a && source .env && set +a
export PGHOST=localhost PGPORT="${PORTA_POSTGRES:-5432}" PGDATABASE=eduinfra_adr0005 DO_NOT_TRACK=1
REPETICOES=${REPETICOES:-3}
export LC_NUMERIC=C  # printf com ponto decimal em qualquer locale
AQUI=docs/evidencias/adr-0005
DSN="postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@$PGHOST:$PGPORT/$PGDATABASE"

NULA=${NULA:-"uv run python -c"}
DBT=${DBT:-"uv run --with dbt-core==1.12.6 --with dbt-postgres==1.11.0 dbt"}
SQLMESH=${SQLMESH:-"uv run --with sqlmesh==0.236.4 --with psycopg2-binary sqlmesh"}

medir() {
    local nome=$1; shift
    for i in $(seq "$REPETICOES"); do
        local inicio fim
        inicio=$(date +%s.%N)
        "$@" >/dev/null 2>&1
        fim=$(date +%s.%N)
        printf '%s, execução %d: %.1f s\n' "$nome" "$i" "$(echo "$fim - $inicio" | bc)"
    done
}

nula() { $NULA "import sys; from eduinfra import modelo_analitico; modelo_analitico.construir(sys.argv[1])" "$DSN"; }
dbt_run() { $DBT run --project-dir "$AQUI/dbt" --profiles-dir "$AQUI/dbt"; }
dbt_build() { $DBT build --project-dir "$AQUI/dbt" --profiles-dir "$AQUI/dbt"; }
sqlmesh_restate() { (cd "$AQUI/sqlmesh" && $SQLMESH plan --restate-model 'gold_sqlmesh.*' --auto-apply --no-prompts); }

# ALTERNATIVAS="C" mede só o SQLMesh, por exemplo.
for alternativa in ${ALTERNATIVAS:-A B C}; do
    case $alternativa in
        A) medir "A. opção nula (construir-gold, sem testes)" nula ;;
        B) medir "B. dbt run (sem testes)" dbt_run
           medir "B. dbt build (modelos e 12 testes)" dbt_build ;;
        C) medir "C. SQLMesh, reconstrução com 12 audits" sqlmesh_restate ;;
    esac
done
