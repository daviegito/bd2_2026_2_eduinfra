"""Falhas provocadas com o consumidor do commit fb8c926, em banco descartável removido ao final (ADR 0007).

Rode a partir de uma cópia desse commit, com DATABASE_URL apontando para o banco de desenvolvimento.
"""

import os
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from alembic import command
from alembic.config import Config

from eduinfra import cdc, eventos, fluxo_cdc
from eduinfra.configuracao import carregar_configuracao

BANCO = "eduinfra_falhas_antes"
SLOT = "eduinfra_falhas_antes"
TAMANHO_DA_MENSAGEM = 150
UMA_HORA_ATRAS = "now() - interval '1 hour'"


def tentar(rotulo, acao):
    try:
        print(f"  [{rotulo}] ok: {acao()}")
    except Exception as erro:
        print(f"  [{rotulo}] FALHOU: {type(erro).__name__}: {str(erro).splitlines()[0][:TAMANHO_DA_MENSAGEM]}")


def contagens(conexao, configuracao):
    origem = conexao.execute("select count(*) from vistoria_item").fetchone()[0]
    return f"origem={origem} delta={eventos.contar(configuracao)}"


def inserir(conexao, ocorrido_em=UMA_HORA_ATRAS, coluna_de_quantidade="quantidade", coluna_extra=None):
    tecnico = conexao.execute("select id from tecnico limit 1").fetchone()[0]
    vistoria = conexao.execute(
        f"insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em) values (100001, %s, 'vistoria_presencial', {ocorrido_em}) returning id",
        (tecnico,),
    ).fetchone()[0]
    extra_coluna, extra_valor = (f", {coluna_extra}", ", 'valor importante'") if coluna_extra else ("", "")
    conexao.execute(
        f"insert into vistoria_item (vistoria_id, item_codigo, situacao, {coluna_de_quantidade}{extra_coluna}) "
        f"select %s, codigo, 'existente', 1{extra_valor} from item_infraestrutura order by codigo limit 2",
        (vistoria,),
    )
    return vistoria


def destravar(conexao):
    conexao.execute("select pg_replication_slot_advance(%s, pg_current_wal_lsn())", (SLOT,))


def provocar(conexao, configuracao):
    passar = lambda: fluxo_cdc.passar_uma_vez(configuracao, slot=SLOT)

    print("== Publicação num banco criado do zero (insert, update, delete, truncate)")
    print(" ", conexao.execute("select pubinsert, pubupdate, pubdelete, pubtruncate from pg_publication").fetchone())

    print("\n== Caminho normal")
    vistoria = inserir(conexao)
    tentar("passada", passar)
    print(" ", contagens(conexao, configuracao))

    print("\n== ADD COLUMN com valor preenchido")
    conexao.execute("alter table vistoria_item add column coluna_nova text")
    inserir(conexao, coluna_extra="coluna_nova")
    tentar("passada", passar)
    print("  coluna_nova no esquema do Delta?", "coluna_nova" in eventos.ESQUEMA.names)
    print(" ", contagens(conexao, configuracao))
    conexao.execute("alter table vistoria_item drop column coluna_nova")
    destravar(conexao)

    print("\n== RENAME COLUMN")
    conexao.execute("alter table vistoria_item rename column quantidade to qtd")
    inserir(conexao, coluna_de_quantidade="qtd")
    tentar("1a passada", passar)
    tentar("2a passada", passar)
    print("  desfazendo o rename e inserindo uma vistoria correta depois")
    conexao.execute("alter table vistoria_item rename column qtd to quantidade")
    inserir(conexao)
    tentar("3a passada, com o esquema já corrigido", passar)
    print(" ", contagens(conexao, configuracao))
    destravar(conexao)

    for rotulo, ocorrido_em in (("-infinity", "'-infinity'::timestamptz"), ("ano 4000 a.C.", "'4000-01-01 BC'::timestamptz")):
        print(f"\n== ocorrido_em = {rotulo}, com uma vizinha correta depois")
        inserir(conexao, ocorrido_em=ocorrido_em)
        print("  a origem aceitou o insert")
        inserir(conexao)
        tentar("1a passada", passar)
        tentar("2a passada", passar)
        print(" ", contagens(conexao, configuracao))
        destravar(conexao)

    print("\n== UPDATE por fora dos gatilhos")
    conexao.execute("set session_replication_role = replica")
    conexao.execute("update vistoria_item set situacao = 'inexistente' where vistoria_id = %s", (vistoria,))
    conexao.execute("set session_replication_role = origin")
    tentar("1a passada", passar)
    tentar("2a passada", passar)
    print(" ", contagens(conexao, configuracao))


def main() -> int:
    base = carregar_configuracao()
    with psycopg.connect(base.dsn, autocommit=True) as admin:
        admin.execute(f"drop database if exists {BANCO} with (force)")
        admin.execute(f"create database {BANCO}")

    dsn = urlunsplit(urlsplit(base.dsn)._replace(path=f"/{BANCO}"))
    os.environ["DATABASE_URL"] = dsn
    diretorio = Path(tempfile.mkdtemp(prefix="falhas_antes_"))
    configuracao = replace(carregar_configuracao(), diretorio_dados=diretorio)
    command.upgrade(Config(str(configuracao.arquivo_alembic)), "head")

    try:
        with psycopg.connect(dsn, autocommit=True) as conexao:
            conexao.execute("insert into municipio (co_municipio, no_municipio, sg_uf, co_uf) values (1, 'Município', 'DF', 53)")
            conexao.execute(
                "insert into escola (co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia) "
                "values (100001, 'Escola', 1, 1, 1, 2024)"
            )
            conexao.execute("insert into tecnico (matricula, nome, lotacao_uf) values ('FALHAS', 'Falhas', 'DF')")
            cdc.garantir_slot(conexao, SLOT)
            provocar(conexao, configuracao)
    finally:
        with psycopg.connect(base.dsn, autocommit=True) as admin:
            admin.execute("select pg_drop_replication_slot(slot_name) from pg_replication_slots where slot_name = %s", (SLOT,))
            admin.execute(f"drop database if exists {BANCO} with (force)")
        shutil.rmtree(diretorio, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
