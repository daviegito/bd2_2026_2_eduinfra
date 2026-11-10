"""Prepara o retrato das fontes, executa os modelos/testes e gera o grafo dbt."""

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import psycopg
from psycopg import sql

from eduinfra import cdc, frescor, ingestao_enem, telemetria_cdc
from eduinfra.configuracao import carregar_configuracao
from eduinfra.fontes_transformacao import Entradas, bloquear, preparar

PROJETO = Path(__file__).resolve().parents[1]


def verificar_slot(conexao: psycopg.Connection, configuracao=None) -> None:
    slots = conexao.execute(
        "select slot_name, database = current_database(), wal_status, safe_wal_size "
        "from pg_replication_slots where slot_type = 'logical'"
    ).fetchall()
    if not slots:
        return
    if configuracao is None or not hasattr(cdc, "posicao_persistida"):
        raise RuntimeError("construção bloqueada: integre a correção #60 e mantenha o consumidor ativo")
    # O teste nacional usa um slot da origem. Outros consumidores precisam de
    # dimensionamento próprio; não os avançamos nem os removemos pelo job dbt.
    if len(slots) != 1 or slots[0][0] != cdc.SLOT_PADRAO or not slots[0][1]:
        raise RuntimeError("há slots de outros consumidores no cluster; valide a retenção antes da construção")
    _, _, estado, margem = slots[0]
    limite = conexao.execute("select pg_size_bytes(current_setting('max_slot_wal_keep_size'))").fetchone()[0]
    # O pico medido do fluxo dbt foi 1,20 GB; exigir 1,5 GiB livres antes do job.
    if estado not in {"reserved", "extended"} or limite < 2 * 1024**3 or margem is None or margem < 1536 * 1024**2:
        raise RuntimeError("slot sem margem segura: use retenção de pelo menos 2 GB e drene o CDC até liberar 1,5 GiB")
    ultimo_dreno = telemetria_cdc.ler_frescor(configuracao)
    agora = telemetria_cdc.agora(conexao)
    if ultimo_dreno is None or not 0 <= (agora - ultimo_dreno).total_seconds() <= 60:
        raise RuntimeError("consumidor sem dreno recente: inicie o CDC corrigido antes da transformação")


def ambiente_dbt(dsn: str) -> dict[str, str]:
    parametros = psycopg.conninfo.conninfo_to_dict(dsn)
    ambiente = dict(os.environ)
    for origem, destino in {
        "host": "PGHOST", "port": "PGPORT", "user": "POSTGRES_USER",
        "password": "POSTGRES_PASSWORD", "dbname": "POSTGRES_DB",
    }.items():
        if origem in parametros:
            ambiente[destino] = parametros[origem]
    ambiente["DBT_SEND_ANONYMOUS_USAGE_STATS"] = "false"
    ambiente.setdefault("DBT_LOG_PATH", str(PROJETO / "logs"))
    return ambiente


def dbt(comando: str, dsn: str, com_enem: bool, schema: str | None = None) -> None:
    ambiente = ambiente_dbt(dsn)
    if schema:
        ambiente["DBT_SCHEMA"] = schema
    subprocess.run(
        [str(Path(sys.executable).with_name("dbt")), *comando.split(),
         "--project-dir", str(PROJETO), "--profiles-dir", str(PROJETO),
         "--vars", json.dumps({"com_enem": com_enem})],
        env=ambiente, check=True,
    )


def executar() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exigir-enem", action="store_true", help="compatibilidade: o fluxo completo já exige o ENEM")
    parser.add_argument("--somente-infraestrutura", action="store_true", help="execução explícita sem desempenho; não baixa o ENEM")
    parser.add_argument("--somente-documentacao", action="store_true", help="regenerar docs do retrato já publicado")
    opcoes = parser.parse_args()
    configuracao = carregar_configuracao()
    if opcoes.exigir_enem and opcoes.somente_infraestrutura:
        parser.error("--exigir-enem não pode ser combinado com --somente-infraestrutura")
    with bloquear(configuracao.dsn) as conexao:
        schema = os.environ.get("DBT_SCHEMA", "gold")
        desempenho = sql.Identifier(schema, "fato_desempenho_escola_ano").as_string(conexao)
        tem_desempenho = conexao.execute("select to_regclass(%s)", (desempenho,)).fetchone()[0]
        com_enem = bool(tem_desempenho) if opcoes.somente_documentacao else not opcoes.somente_infraestrutura
        if not opcoes.somente_documentacao:
            verificar_slot(conexao, configuracao)
            if com_enem and not os.environ.get("FONTE_ENEM"):
                # Usa o protocolo de manifesto e recuperação da Silver já entregue pela #44.
                ingestao_enem.ingerir_enem(configuracao)
            entradas = Entradas.do_ambiente(configuracao)
            if not com_enem:
                if tem_desempenho:
                    raise RuntimeError("esta Gold já tem ENEM: execute o fluxo completo para preservar o desempenho")
                entradas = replace(entradas, enem=None)
            verificar_slot(conexao, configuracao)
            resumo = preparar(conexao, entradas)
            print(json.dumps(resumo, ensure_ascii=False), flush=True)
            dbt("build", configuracao.dsn, resumo["com_enem"])
            if schema == "gold":
                print(frescor.resumo(frescor.medir(configuracao.dsn, comando="transformar-dbt")), flush=True)
        dbt("docs generate", configuracao.dsn, com_enem)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        executar()
    except (RuntimeError, subprocess.CalledProcessError) as erro:
        print(f"transformação falhou: {erro}", file=sys.stderr)
        sys.exit(1)
