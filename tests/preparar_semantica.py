"""Prepara somente a bancada sintética da #83, incluindo o contrato do #110."""

import argparse
import importlib.util
import os
import shutil
import tempfile
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

RAIZ = Path(__file__).resolve().parents[1]


def preparar(dsn: str, migracao: Path) -> None:
    parametros = conninfo_to_dict(dsn)
    if parametros.get('host') not in ('localhost', '127.0.0.1') or parametros.get('dbname') != 'eduinfra83':
        raise ValueError('Use somente o banco descartável eduinfra83 em loopback')
    os.environ['DATABASE_URL'] = dsn
    # O snapshot externo fica só na bancada; após o merge usa-se a migração da base.
    with tempfile.TemporaryDirectory(prefix='eduinfra103-migracoes-') as pasta:
        scripts = Path(pasta) / 'alembic'
        shutil.copytree(RAIZ / 'alembic', scripts)
        if not list((scripts / 'versions').glob('0010_*.py')):
            shutil.copy2(migracao, scripts / 'versions' / '0010_controle_de_acesso.py')
        config = Config(str(RAIZ / 'alembic.ini'))
        config.set_main_option('script_location', str(scripts))
        iniciais = {r.revision for r in ScriptDirectory.from_config(config).walk_revisions(head='0009')}
        with psycopg.connect(dsn) as conexao:
            tem_versao = conexao.execute("select to_regclass('public.alembic_version')").fetchone()[0]
            atual = conexao.execute('select version_num from public.alembic_version').fetchone()[0] if tem_versao else None
        anterior_ao_acesso = atual is None or atual in iniciais
        if anterior_ao_acesso:
            command.upgrade(config, '0009')
        with psycopg.connect(dsn) as conexao:
            if conexao.execute("select to_regclass('gold.dim_escola')").fetchone()[0] is None:
                conexao.execute((RAIZ / 'docs/evidencias/issue-83/fixture.sql').read_text())
        legado = os.environ.get('SEMANTICA_PUBLICADOR_LEGADO')
        if legado and anterior_ao_acesso:
            spec = importlib.util.spec_from_file_location('semantica_legada', legado)
            publicador = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(publicador)
            publicador.publicar(dsn, RAIZ / 'metricas')
        command.upgrade(config, 'head')
    from eduinfra import semantica
    semantica.preparar_acesso(dsn)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsn', required=True)
    parser.add_argument('--migracao-acesso', type=Path, required=True)
    opcoes = parser.parse_args()
    preparar(opcoes.dsn, opcoes.migracao_acesso)
