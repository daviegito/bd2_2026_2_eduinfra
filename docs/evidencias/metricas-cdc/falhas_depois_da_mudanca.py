"""Falhas provocadas com o consumidor atual, em banco descartável removido ao final (ADR 0007)."""

import os
import shutil
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from alembic import command
from alembic.config import Config

from eduinfra import auditoria_cdc, fluxo_cdc
from eduinfra import cdc
from eduinfra.configuracao import carregar_configuracao
from eduinfra.vistorias_de_teste import inserir_vistoria

BANCO = "eduinfra_demonstracao_falhas"
SLOT = "eduinfra_demonstracao_falhas"
MATRICULA = "DEMO-FALHAS"


def inserir_envenenada(c, tecnico):
    with c.transaction():
        vistoria = c.execute(
            "insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em) values (100001, %s, 'vistoria_presencial', '-infinity') returning id",
            (tecnico,),
        ).fetchone()[0]
        c.execute("insert into vistoria_item (vistoria_id, item_codigo, situacao) select %s, codigo, 'existente' from item_infraestrutura", (vistoria,))


def main() -> int:
    base = carregar_configuracao()
    with psycopg.connect(base.dsn, autocommit=True) as admin:
        admin.execute(f"drop database if exists {BANCO} with (force)")
        admin.execute(f"create database {BANCO}")
    dsn = urlunsplit(urlsplit(base.dsn)._replace(path=f"/{BANCO}"))
    os.environ["DATABASE_URL"] = dsn
    diretorio = Path(tempfile.mkdtemp(prefix="demonstracao_"))
    configuracao = replace(carregar_configuracao(), diretorio_dados=diretorio)
    command.upgrade(Config(str(configuracao.arquivo_alembic)), "head")

    try:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("insert into municipio (co_municipio, no_municipio, sg_uf, co_uf) values (1, 'Município', 'DF', 53)")
            c.execute(
                "insert into escola (co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia) "
                "values (100001, 'Escola', 1, 1, 1, 2024)"
            )
            cdc.garantir_slot(c, SLOT)
            evento = datetime.now(timezone.utc) - timedelta(minutes=1)

            vistoria_boa = inserir_vistoria(c, MATRICULA, "Demonstração", evento)
            tecnico = c.execute("select id from tecnico where matricula = %s", (MATRICULA,)).fetchone()[0]
            passar = lambda: fluxo_cdc.passar_uma_vez(configuracao, slot=SLOT)
            print("vistoria correta:", passar())

            inserir_envenenada(c, tecnico)
            inserir_vistoria(c, MATRICULA, "Demonstração", evento)
            print("com -infinity e uma vizinha correta:", passar())

            c.execute("alter table vistoria_item rename column quantidade to qtd")
            vistoria_sem_quantidade = c.execute(
                "insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em) values (100001, %s, 'vistoria_presencial', %s) returning id",
                (tecnico, evento),
            ).fetchone()[0]
            c.execute(
                "insert into vistoria_item (vistoria_id, item_codigo, situacao) select %s, codigo, 'existente' from item_infraestrutura",
                (vistoria_sem_quantidade,),
            )
            print("com a coluna renomeada:", passar())
            c.execute("alter table vistoria_item rename column qtd to quantidade")

            c.execute("alter table vistoria_item add column coluna_nova text")
            inserir_vistoria(c, MATRICULA, "Demonstração", evento)
            print("com coluna nova:", passar())

            c.execute("set session_replication_role = replica")
            c.execute("update vistoria_item set situacao = 'inexistente' where vistoria_id = %s", (vistoria_boa,))
            c.execute("set session_replication_role = origin")
            print("com update por fora dos gatilhos:", passar())
            passar()

        auditoria = auditoria_cdc.auditar(configuracao, slot=SLOT)
        print()
        print(auditoria_cdc.formatar(auditoria))
    finally:
        with psycopg.connect(base.dsn, autocommit=True) as admin:
            admin.execute("select pg_drop_replication_slot(slot_name) from pg_replication_slots where slot_name = %s", (SLOT,))
            admin.execute(f"drop database if exists {BANCO} with (force)")
        shutil.rmtree(diretorio, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
