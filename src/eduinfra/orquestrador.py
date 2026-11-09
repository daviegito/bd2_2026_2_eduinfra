import os
import subprocess
import sys

from dagster import (
    AssetExecutionContext,
    AssetSelection,
    Backoff,
    DefaultScheduleStatus,
    Definitions,
    Failure,
    MonthlyPartitionsDefinition,
    RetryPolicy,
    ScheduleDefinition,
    asset,
    define_asset_job,
)

RETRY = RetryPolicy(max_retries=3, delay=30, backoff=Backoff.EXPONENTIAL)

JANELAS_MENSAIS = MonthlyPartitionsDefinition(start_date="2024-01-01")


def rodar(*argumentos: str) -> None:
    ambiente = {"PGCONNECT_TIMEOUT": "15", **os.environ}
    resultado = subprocess.run([sys.executable, "-m", "eduinfra.cli", *argumentos], env=ambiente)
    if resultado.returncode != 0:
        raise Failure(f"eduinfra {' '.join(argumentos)} saiu com código {resultado.returncode}")


@asset(retry_policy=RETRY)
def migrar() -> None:
    rodar("migrar")


@asset(deps=[migrar], retry_policy=RETRY)
def ingerir() -> None:
    rodar("ingerir")


@asset(deps=[ingerir], retry_policy=RETRY)
def publicar_analitica() -> None:
    rodar("publicar-analitica")


@asset(deps=[ingerir], retry_policy=RETRY)
def publicar_linha_de_base() -> None:
    # Carga inicial da fonte de eventos da Gold (ADR 0008). Quando a tabela já existe, não faz nada.
    rodar("publicar-analitico")


@asset(deps=[publicar_linha_de_base], retry_policy=RETRY)
def processar_janelas() -> None:
    rodar("processar-janelas")


@asset(deps=[processar_janelas], retry_policy=RETRY)
def construir_gold() -> None:
    rodar("construir-gold")


# Sem retry: medir-frescor sai com código 1 quando o SLA de 25 h foi violado, e repetir a medição
# minutos depois não muda isso. A falha deve aparecer na execução como alerta.
@asset(deps=[construir_gold])
def medir_frescor() -> None:
    rodar("medir-frescor")


@asset(partitions_def=JANELAS_MENSAIS, retry_policy=RETRY)
def reprocessar_janela(context: AssetExecutionContext) -> None:
    rodar("reprocessar-janela", "--mes", context.partition_key[:7])


ETAPAS_DO_PIPELINE = [
    migrar,
    ingerir,
    publicar_analitica,
    publicar_linha_de_base,
    processar_janelas,
    construir_gold,
    medir_frescor,
]

pipeline = define_asset_job("pipeline", selection=AssetSelection.assets(*ETAPAS_DO_PIPELINE))

agenda = ScheduleDefinition(
    job=pipeline,
    cron_schedule=os.environ.get("AGENDA_ORQUESTRADOR", "0 3 * * *"),
    execution_timezone="America/Sao_Paulo",
    default_status=DefaultScheduleStatus.RUNNING,
)

defs = Definitions(
    assets=[*ETAPAS_DO_PIPELINE, reprocessar_janela],
    jobs=[pipeline],
    schedules=[agenda],
)
