"""O contrato CLI é exercitado contra a bancada sintética e com argumentos inválidos."""
import os
import json

import pytest

from eduinfra import cli


def test_cli_rejeita_filtros_que_nao_sao_json(monkeypatch, caplog):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://nao-utilizado')
    assert cli.main(['consultar-metrica', '--metrica', 'media_enem_escolas', '--filtros', 'invalido']) == 1
    assert caplog.records


def test_cli_exige_nome_da_metrica(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://nao-utilizado')
    with pytest.raises(SystemExit) as erro:
        cli.main(['consultar-metrica'])
    assert erro.value.code == 2


@pytest.mark.integracao
def test_cli_publica_e_consulta_no_mesmo_contrato(monkeypatch, capsys):
    dsn = os.environ.get('SEMANTICA_BANCADA_DSN')
    if not dsn:
        pytest.skip('defina SEMANTICA_BANCADA_DSN para a bancada isolada')
    from psycopg.conninfo import make_conninfo
    monkeypatch.setenv('DATABASE_URL', make_conninfo(dsn, user='eduinfra_painel', password='senha_local_painel'))
    monkeypatch.setenv('DATABASE_URL_TRANSFORMACAO', make_conninfo(dsn, user='eduinfra_dbt', password='senha_local_dbt'))
    assert cli.main(['publicar-metricas']) == 0
    publicadas = json.loads(capsys.readouterr().out)['metricas']
    assert 'media_enem_escolas' in publicadas
    assert cli.main(['consultar-metrica', '--metrica', 'media_enem_escolas', '--filtros', '{"ano":2024}']) == 0
    resposta = json.loads(capsys.readouterr().out)
    assert resposta['dados'][0]['n_escolas'] == 4615
    assert resposta['metrica'] == 'media_enem_escolas'
