#!/usr/bin/env bash
# Cria o banco eduinfra_adr0005 com um recorte real da origem: as escolas de SP e do
# Centro-Oeste (41.380 de 181.065), com todas as vistorias e itens delas. As três
# alternativas do ADR 0005 constroem a Gold sobre este mesmo banco.
#
# Rode da raiz do repositório, com o serviço banco no ar e a origem já carregada:
#   bash docs/evidencias/adr-0005/preparar_amostra.sh
set -euo pipefail

set -a && source .env && set +a
DESTINO=eduinfra_adr0005
UFS="'SP','DF','GO','MT','MS'"
DA_AMOSTRA="join escola e using (co_entidade) join municipio m using (co_municipio) where m.sg_uf in ($UFS)"

origem() { docker compose exec -T banco psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"; }
destino() { docker compose exec -T banco psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$DESTINO" "$@"; }
copiar() { origem -c "copy ($2) to stdout" | destino -c "copy $1 from stdin"; }

origem -c "drop database if exists $DESTINO with (force)" -c "create database $DESTINO"
destino -c "drop schema public"  # o pg_dump recria o schema com o dono e os comentários da origem
docker compose exec -T banco pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --schema-only --schema=public | destino >/dev/null

copiar municipio "select * from municipio"
copiar item_infraestrutura "select * from item_infraestrutura"
copiar tecnico "select * from tecnico"
copiar escola "select e.* from escola e join municipio m using (co_municipio) where m.sg_uf in ($UFS)"
copiar vistoria "select v.* from vistoria v $DA_AMOSTRA"
copiar vistoria_item "select vi.* from vistoria_item vi join vistoria v on v.id = vi.vistoria_id join escola e using (co_entidade) join municipio m using (co_municipio) where m.sg_uf in ($UFS)"
destino -c "analyze"

destino -At -c "select 'escolas ' || count(*) from escola" -c "select 'vistorias ' || count(*) from vistoria" -c "select 'itens ' || count(*) from vistoria_item"
