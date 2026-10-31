#!/usr/bin/env bash
# Demonstração da E2 de ponta a ponta: ingestão em lote com manifesto, origem
# insert-only, CDC contínuo capturando uma vistoria ao vivo, semântica de
# entrega e dado atrasado.
#
# Uso: scripts/demonstrar_e2.sh [--do-zero] [--sem-pausa]
#   --do-zero    apaga os volumes do projeto antes de subir (banco e Delta vazios)
#   --sem-pausa  não espera ENTER entre as etapas
#
# As etapas de CDC e atraso gravam vistorias de teste na origem, que é
# insert-only: rode em ambiente de desenvolvimento.

set -euo pipefail

cd "$(dirname "$0")/.."

ESCOLA_AO_VIVO=53002580
MATRICULA_AO_VIVO="DEMO-E2-AO-VIVO"
ESPERA_MAXIMA_CDC_SEGUNDOS=180
ESPERA_MAXIMA_CAPTURA_SEGUNDOS=60
INTERVALO_DE_CONSULTA_SEGUNDOS=3

do_zero=false
pausar=true
for argumento in "$@"; do
  case "$argumento" in
    --do-zero) do_zero=true ;;
    --sem-pausa) pausar=false ;;
    *) echo "argumento desconhecido: $argumento" >&2; exit 2 ;;
  esac
done

if [[ ! -f .env ]]; then
  echo "Arquivo .env não encontrado. Copie .env.example para .env e defina a senha." >&2
  exit 1
fi

# O .env pode ter sido salvo com CRLF no Windows.
set -a
source <(tr -d '\r' < .env)
set +a

etapa() {
  echo
  echo "=================================================================="
  echo "  $1"
  echo "=================================================================="
  if $pausar; then
    read -r -p "ENTER para executar..." _
  fi
}

psql_origem() {
  docker compose exec -T banco psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 "$@"
}

contar_delta() {
  docker compose exec -T cdc uv run --no-sync python -c \
    "from eduinfra import eventos; from eduinfra.configuracao import carregar_configuracao; print(eventos.contar(carregar_configuracao()))" \
    2>/dev/null | tail -n 1
}

saude_do_cdc() {
  docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q cdc)"
}

aguardar_cdc_saudavel() {
  local decorrido=0
  until [[ "$(saude_do_cdc)" == "healthy" ]]; do
    if (( decorrido >= ESPERA_MAXIMA_CDC_SEGUNDOS )); then
      echo "o consumidor CDC não ficou saudável em ${ESPERA_MAXIMA_CDC_SEGUNDOS}s" >&2
      docker compose logs --tail=30 cdc >&2
      exit 1
    fi
    sleep "$INTERVALO_DE_CONSULTA_SEGUNDOS"
    decorrido=$(( decorrido + INTERVALO_DE_CONSULTA_SEGUNDOS ))
    echo "  aguardando o CDC ficar saudável (${decorrido}s, estado: $(saude_do_cdc))"
  done
}

aguardar_captura() {
  local esperado=$1 decorrido=0 atual
  atual=$(contar_delta)
  until (( atual >= esperado )); do
    if (( decorrido >= ESPERA_MAXIMA_CAPTURA_SEGUNDOS )); then
      echo "o Delta não chegou a $esperado linhas em ${ESPERA_MAXIMA_CAPTURA_SEGUNDOS}s (está em $atual)" >&2
      exit 1
    fi
    sleep "$INTERVALO_DE_CONSULTA_SEGUNDOS"
    decorrido=$(( decorrido + INTERVALO_DE_CONSULTA_SEGUNDOS ))
    atual=$(contar_delta)
    echo "  Delta com $atual linhas (${decorrido}s)"
  done
}

if $do_zero; then
  etapa "0. Recomeçar do zero: removendo containers e volumes do projeto"
  docker compose down -v
fi

etapa "1. Construir as imagens e subir o PostgreSQL com wal_level=logical"
# `run` e `up --no-deps` reaproveitam a imagem existente; sem este build, uma
# imagem antiga roda sem as migrações mais recentes.
docker compose build carga cdc
docker compose up -d --wait banco
psql_origem -c "show wal_level;"

etapa "2. Ingestão em lote: migrar, ingerir o Censo 2024, publicar linha de base e Silver"
docker compose run --rm carga tudo

etapa "3. Manifesto: a mesma ingestão de novo é ignorada pelo hash do arquivo"
docker compose run --rm carga ingerir
psql_origem -c "
  select ano_censo, left(hash_sha256, 12) as hash, forcada, escolas_inseridas,
         itens_inseridos, concluido_em
  from ingestao_execucao order by id;"

etapa "4. Origem insert-only: UPDATE em vistoria é bloqueado pelo gatilho"
psql_origem -c "update vistoria set observacao = 'tentativa' where id = 1;" \
  || echo "-> bloqueado, como esperado"

etapa "5. Subir o consumidor CDC contínuo e esperar o healthcheck"
docker compose up -d --no-deps cdc
aguardar_cdc_saudavel
docker compose ps
psql_origem -c "
  select slot_name, plugin, active, wal_status,
         pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)) as atraso
  from pg_replication_slots;"

etapa "6. CDC ao vivo: nova vistoria da escola $ESCOLA_AO_VIVO capturada pelo consumidor"
itens_por_vistoria=$(psql_origem -tA -c "select count(*) from item_infraestrutura;")
antes=$(contar_delta)
echo "Delta antes: $antes linhas"
psql_origem -c "
  insert into tecnico (matricula, nome, lotacao_uf)
  values ('$MATRICULA_AO_VIVO', 'Técnico de demonstração', 'DF')
  on conflict (matricula) do nothing;

  with nova as (
    insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em, observacao)
    select $ESCOLA_AO_VIVO, id, 'vistoria_presencial', now() - interval '10 minutes',
           'Demonstração da E2: CDC ao vivo'
    from tecnico where matricula = '$MATRICULA_AO_VIVO'
    returning id
  )
  insert into vistoria_item (vistoria_id, item_codigo, situacao)
  select nova.id, item.codigo, 'existente' from nova cross join item_infraestrutura item;"
aguardar_captura $(( antes + itens_por_vistoria ))
echo "Delta depois: $(contar_delta) linhas (esperado $(( antes + itens_por_vistoria )))"
docker compose logs --tail=5 cdc
psql_origem -c "select * from escola_estado_atual where co_entidade = $ESCOLA_AO_VIVO limit 5;"

etapa "7. Semântica de entrega: parada, falha antes de confirmar e reprocessamento"
docker compose stop cdc
docker compose run --rm carga demonstrar-cdc
docker compose up -d --no-deps cdc

etapa "8. Dado atrasado e reprocessamento de janela"
docker compose run --rm carga demonstrar-atraso

etapa "9. Estado final"
aguardar_cdc_saudavel
docker compose ps
echo "Delta: $(contar_delta) linhas"
psql_origem -c "select count(*) as itens_na_origem from vistoria_item;"

echo
echo "Demonstração concluída. banco e cdc continuam no ar; 'docker compose down' para parar."
