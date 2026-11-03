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
def processar_janelas() -> None:
    rodar("processar-janelas")


@asset(partitions_def=JANELAS_MENSAIS, retry_policy=RETRY)
def reprocessar_janela(context: AssetExecutionContext) -> None:
    rodar("reprocessar-janela", "--mes", context.partition_key[:7])


pipeline = define_asset_job(
    "pipeline",
    selection=AssetSelection.assets(migrar, ingerir, publicar_analitica, processar_janelas),
)

agenda = ScheduleDefinition(
    job=pipeline,
    cron_schedule=os.environ.get("AGENDA_ORQUESTRADOR", "0 3 * * *"),
    execution_timezone="America/Sao_Paulo",
    default_status=DefaultScheduleStatus.RUNNING,
)

defs = Definitions(
    assets=[migrar, ingerir, publicar_analitica, processar_janelas, reprocessar_janela],
    jobs=[pipeline],
    schedules=[agenda],
)
