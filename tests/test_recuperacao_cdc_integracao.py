"""Recuperação de DLQ e aceite por ocorrência em PostgreSQL e Delta descartáveis."""

import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from deltalake import DeltaTable

from eduinfra import auditoria_cdc, cdc, cli, eventos, fila_de_erros, fluxo_cdc
from eduinfra import recuperacao_cdc as recuperacao
from eduinfra.configuracao import carregar_configuracao

pytestmark = pytest.mark.integracao
AUTOR = "operador-01"
MOTIVO = "esquema_corrigido"


@pytest.fixture
def config(monkeypatch, tmp_path):
    admin_dsn = os.environ.get("DATABASE_URL")
    if not admin_dsn:
        pytest.skip("DATABASE_URL não definida")
    nome = f"eduinfra_recuperacao_{uuid4().hex[:10]}"
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(f'create database "{nome}"')
    dsn = urlsplit(admin_dsn)._replace(path=f"/{nome}").geturl()
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("DIRETORIO_DADOS", str(tmp_path))
    try:
        command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("insert into municipio values (1, 'Município', 'DF', 53)")
            conn.execute("insert into escola (co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia) values (1, 'Escola', 1, 1, 1, 2024)")
            conn.execute("insert into tecnico (matricula, nome, lotacao_uf) values ('T1', 'Técnico', 'DF')")
            cdc.garantir_slot(conn)
        yield carregar_configuracao()
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute("select pg_drop_replication_slot(slot_name) from pg_replication_slots where database = %s", (nome,))
            admin.execute(f'drop database "{nome}" with (force)')


def inserir(config, data="2024-05-29", situacao_biblioteca="existente"):
    with psycopg.connect(config.dsn) as conn:
        id = conn.execute("insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em, observacao) values (1, (select id from tecnico limit 1), 'vistoria_presencial', %s::timestamptz, 'TEXTO PESSOAL QUE NAO DEVE IR AO RECIBO') returning id", (data,)).fetchone()[0]
        conn.execute("insert into vistoria_item (vistoria_id, item_codigo, situacao) values (%s, 'biblioteca', %s), (%s, 'internet', 'existente')", (id, situacao_biblioteca, id))
    return id


def executar(config, sql, parametros=None):
    with psycopg.connect(config.dsn, autocommit=True) as conn:
        conn.execute(sql, parametros)


def desviar(config, sql, parametros=None):
    with psycopg.connect(config.dsn) as conn:
        conn.execute("set local session_replication_role = replica")
        conn.execute(sql, parametros)
    fluxo_cdc.passar_uma_vez(config)


def fila_por_esquema(config):
    executar(config, "alter table vistoria_item rename column quantidade to qtd")
    id = inserir(config)
    assert fluxo_cdc.passar_uma_vez(config).itens_rejeitados == 2
    executar(config, "alter table vistoria_item rename column qtd to quantidade")
    return id


def fotografar(config):
    evento = DeltaTable(str(eventos.caminho_da_tabela(config)))
    recibo = DeltaTable(recuperacao.caminho(config))
    return evento.version(), evento.to_pyarrow_table().to_pylist(), recibo.version(), recibo.to_pyarrow_table().to_pylist()


def test_recupera_pela_chave_sem_releitura_total_e_repeticao_nao_muda_resultado(config, monkeypatch):
    fila_por_esquema(config)
    antes = fila_de_erros.ler(config).to_pylist()
    monkeypatch.setattr(eventos, "ler_da_origem", lambda *_: pytest.fail("Não deve ler a origem inteira"))
    monkeypatch.setattr(recuperacao, "_estado", lambda *_: pytest.fail("Reprocessar não deve reler a origem para o recibo"))
    resultado = recuperacao.reprocessar(config, AUTOR, MOTIVO)
    assert (resultado.recuperadas, resultado.linhas_lidas, resultado.pendentes) == (2, 2, 0)
    assert eventos.contar(config) == 2
    foto = fotografar(config)
    repeticao = recuperacao.reprocessar(config, "operador-02", MOTIVO)
    assert (repeticao.recuperadas, repeticao.ja_tratadas, repeticao.linhas_lidas) == (0, 2, 0)
    assert fotografar(config) == foto
    assert fila_de_erros.ler(config).to_pylist() == antes
    assert cli.main(["auditar-cdc"]) == 0
    recibos = recuperacao.ler_tratamentos(config).to_pylist()
    assert all(r["responsavel"] == AUTOR and r["motivo"] == MOTIVO and r["tratado_em"] for r in recibos)
    assert "TEXTO PESSOAL" not in str(recibos)


def test_falha_depois_do_delta_antes_do_recibo_permite_repetir_sem_duplicar(config, monkeypatch):
    fila_por_esquema(config)
    gravar = recuperacao._gravar
    def falhar(*args):
        raise OSError("falha de armazenamento do recibo")
    monkeypatch.setattr(recuperacao, "_gravar", falhar)
    with pytest.raises(OSError):
        recuperacao.reprocessar(config, AUTOR, MOTIVO)
    assert eventos.contar(config) == 1
    assert recuperacao.ler_tratamentos(config).num_rows == 0
    monkeypatch.setattr(recuperacao, "_gravar", gravar)
    assert recuperacao.reprocessar(config, AUTOR, MOTIVO).recuperadas == 2
    assert eventos.contar(config) == 2
    assert cli.main(["auditar-cdc"]) == 0


def test_data_ainda_invalida_fica_pendente_sem_recibo(config):
    with psycopg.connect(config.dsn, autocommit=True) as conn:
        conn.execute("alter table vistoria drop constraint if exists vistoria_ocorrido_em_representavel")
    inserir(config, "-infinity")
    fluxo_cdc.passar_uma_vez(config)
    assert cli.main(["reprocessar-fila-cdc", "--responsavel", AUTOR, "--motivo", "legado_revisado"]) == 1
    assert recuperacao.ler_tratamentos(config).num_rows == 0
    assert eventos.contar(config) == 0
    assert cli.main(["auditar-cdc"]) == 1


def test_item_com_campo_obrigatorio_nulo_nao_bloqueia_os_itens_validos(config):
    executar(config, "alter table vistoria_item alter column situacao drop not null")
    invalida = inserir(config, situacao_biblioteca=None)
    assert fluxo_cdc.passar_uma_vez(config).itens_rejeitados == 1
    valida = fila_por_esquema(config)
    entradas = [e for e in fila_de_erros.ler(config).to_pylist() if e["operacao"] == "insert"]
    assert len(entradas) == 3
    assert min(entradas, key=recuperacao.identidade)["vistoria_id"] == invalida

    resultado = recuperacao.reprocessar(config, AUTOR, MOTIVO)

    assert resultado == recuperacao.Resultado(recuperadas=2, ja_tratadas=0, pendentes=1, linhas_lidas=3)
    _, linhas, _, recibos = fotografar(config)
    assert {(linha["vistoria_id"], linha["item_codigo"]) for linha in linhas} == {
        (invalida, "internet"), (valida, "biblioteca"), (valida, "internet"),
    }
    assert {recuperacao.identidade(r) for r in recibos} == {
        recuperacao.identidade(e) for e in entradas if e["vistoria_id"] == valida
    }
    assert recuperacao.reprocessar(config, AUTOR, MOTIVO) == recuperacao.Resultado(
        recuperadas=0, ja_tratadas=2, pendentes=1, linhas_lidas=1,
    )


@pytest.mark.parametrize("operacao", ["update", "delete", "truncate", "cabecalho"])
def test_aceite_exato_libera_auditoria_e_nova_violacao_volta_a_falhar(config, operacao):
    id = inserir(config)
    fluxo_cdc.passar_uma_vez(config)
    comandos = {
        "update": ("update vistoria_item set quantidade = 3 where vistoria_id = %s and item_codigo = 'biblioteca'", (id,)),
        "delete": ("delete from vistoria_item where vistoria_id = %s and item_codigo = 'biblioteca'", (id,)),
        "truncate": ("truncate vistoria_item", None),
        "cabecalho": ("update vistoria set ocorrido_em = '2023-01-01' where id = %s", (id,)),
    }
    desviar(config, *comandos[operacao])
    assert cli.main(["auditar-cdc"]) == 1
    entrada, = fila_de_erros.ler(config).to_pylist()
    args = ["tratar-desvio-cdc", "--responsavel", AUTOR, "--motivo", "desvio_revisado", "--lsn", str(entrada["lsn_origem"]), "--chave", entrada["chave"]]
    assert cli.main(args) == 0
    antes = recuperacao.ler_tratamentos(config).to_pylist()
    assert cli.main(args) == 0
    assert recuperacao.ler_tratamentos(config).to_pylist() == antes
    assert cli.main(["auditar-cdc"]) == 0
    nova = inserir(config)
    fluxo_cdc.passar_uma_vez(config)
    assert cli.main(["auditar-cdc"]) == 0
    desviar(config, "update vistoria_item set quantidade = 9 where vistoria_id = %s and item_codigo = 'internet'", (nova,))
    assert cli.main(["auditar-cdc"]) == 1


def test_nova_ocorrencia_na_mesma_chave_exige_novo_tratamento(config):
    id = inserir(config)
    fluxo_cdc.passar_uma_vez(config)
    sql = "update vistoria_item set quantidade = %s where vistoria_id = %s and item_codigo = 'biblioteca'"
    for quantidade in (1, 2):
        desviar(config, sql, (quantidade, id))
        assert cli.main(["auditar-cdc"]) == 1
        entrada = max(fila_de_erros.ler(config).to_pylist(), key=lambda e: e["lsn_origem"])
        recuperacao.tratar_desvio(config, AUTOR, "desvio_revisado", entrada["lsn_origem"], entrada["chave"])
        assert cli.main(["auditar-cdc"]) == 0
    assert recuperacao.ler_tratamentos(config).num_rows == 2


def test_aceite_nao_esconde_mudanca_sem_nova_passada_do_consumidor(config):
    id = inserir(config)
    fluxo_cdc.passar_uma_vez(config)
    sql = "update vistoria_item set quantidade = %s where vistoria_id = %s and item_codigo = 'biblioteca'"
    desviar(config, sql, (1, id))
    entrada, = fila_de_erros.ler(config).to_pylist()
    recuperacao.tratar_desvio(config, AUTOR, "desvio_revisado", entrada["lsn_origem"], entrada["chave"])
    with psycopg.connect(config.dsn) as conn:
        conn.execute("set local session_replication_role = replica")
        conn.execute(sql, (2, id))
    assert cli.main(["auditar-cdc"]) == 1


def test_nao_permite_marcar_insert_como_tratado_sem_recuperar(config):
    fila_por_esquema(config)
    entrada = fila_de_erros.ler(config).to_pylist()[0]
    with pytest.raises(ValueError, match="reprocessamento bem-sucedido"):
        recuperacao.tratar_desvio(config, AUTOR, "desvio_revisado", entrada["lsn_origem"], entrada["chave"])
    assert recuperacao.ler_tratamentos(config).num_rows == 0


def test_recuperacao_nao_considera_sucesso_quando_delta_ja_diverge(config):
    id = fila_por_esquema(config)
    eventos.carregar_da_origem(config)
    desviar(config, "update vistoria_item set quantidade = 99 where vistoria_id = %s", (id,))
    resultado = recuperacao.reprocessar(config, AUTOR, MOTIVO)
    assert resultado.recuperadas == 0 and resultado.pendentes == 2
    assert recuperacao.ler_tratamentos(config).num_rows == 0


def test_recuperacao_nao_duplica_chave_ja_publicada_em_outro_mes(config):
    vistoria = fila_por_esquema(config)
    eventos.carregar_da_origem(config)
    destino = str(eventos.caminho_da_tabela(config))
    antes = DeltaTable(destino)
    desviar(config, "update vistoria set ocorrido_em = '2024-06-01 UTC' where id = %s", (vistoria,))

    resultado = recuperacao.reprocessar(config, AUTOR, MOTIVO)

    assert resultado == recuperacao.Resultado(recuperadas=0, ja_tratadas=0, pendentes=2, linhas_lidas=2)
    assert recuperacao.ler_tratamentos(config).num_rows == 0
    depois = DeltaTable(destino)
    assert depois.version() == antes.version()
    assert depois.to_pyarrow_table().num_rows == 2


def test_ocorrencia_inexistente_e_parametros_incompletos_falham(config):
    with pytest.raises(ValueError, match="Informe --lsn"):
        recuperacao.reprocessar(config, AUTOR, MOTIVO, lsn=123)
    with pytest.raises(ValueError, match="não encontrada"):
        recuperacao.reprocessar(config, AUTOR, MOTIVO, lsn=123, chave="inexistente")


def test_selecao_por_ocorrencia_preserva_as_outras_pendencias(config):
    fila_por_esquema(config)
    entrada = fila_de_erros.ler(config).to_pylist()[0]
    resultado = recuperacao.reprocessar(config, AUTOR, MOTIVO, entrada["lsn_origem"], entrada["chave"])
    assert resultado.recuperadas == resultado.linhas_lidas == 1
    assert eventos.contar(config) == 1
    assert cli.main(["auditar-cdc"]) == 1
    assert recuperacao.reprocessar(config, AUTOR, MOTIVO).recuperadas == 1
    assert cli.main(["auditar-cdc"]) == 0


def test_tratar_um_desvio_nao_esconde_outro(config):
    id = inserir(config)
    fluxo_cdc.passar_uma_vez(config)
    desviar(config, "update vistoria_item set quantidade = 1 where vistoria_id = %s", (id,))
    primeira, segunda = fila_de_erros.ler(config).to_pylist()
    recuperacao.tratar_desvio(config, AUTOR, "desvio_revisado", primeira["lsn_origem"], primeira["chave"])
    assert cli.main(["auditar-cdc"]) == 1
    recuperacao.tratar_desvio(config, AUTOR, "desvio_revisado", segunda["lsn_origem"], segunda["chave"])
    assert cli.main(["auditar-cdc"]) == 0


def test_duas_alteracoes_na_mesma_chave_e_transacao_nao_deixam_alerta_permanente(config):
    id = inserir(config)
    fluxo_cdc.passar_uma_vez(config)
    with psycopg.connect(config.dsn) as conn:
        conn.execute("set local session_replication_role = replica")
        for quantidade in (1, 2):
            conn.execute("update vistoria_item set quantidade = %s where vistoria_id = %s and item_codigo = 'internet'", (quantidade, id))
    fluxo_cdc.passar_uma_vez(config)
    entradas = fila_de_erros.ler(config).to_pylist()
    assert len({recuperacao.identidade(e) for e in entradas}) == 1
    entrada = entradas[0]
    assert cli.main(["auditar-cdc"]) == 1
    recuperacao.tratar_desvio(config, AUTOR, "desvio_revisado", entrada["lsn_origem"], entrada["chave"])
    assert cli.main(["auditar-cdc"]) == 0


def test_estado_aceito_nao_esconde_nova_rejeicao_na_mesma_chave(config):
    id = inserir(config)
    fluxo_cdc.passar_uma_vez(config)
    desviar(config, "update vistoria_item set quantidade = 7 where vistoria_id = %s and item_codigo = 'biblioteca'", (id,))
    entrada, = fila_de_erros.ler(config).to_pylist()
    recuperacao.tratar_desvio(config, AUTOR, "desvio_revisado", entrada["lsn_origem"], entrada["chave"])
    assert cli.main(["auditar-cdc"]) == 0
    nova = fila_de_erros.montar_linha(entrada["lsn_origem"] + 100, entrada["confirmada_em"], "vistoria_item", "insert", id, "biblioteca", "KeyError: quantidade")
    fila_de_erros.gravar(config, [nova])
    assert cli.main(["auditar-cdc"]) == 1
