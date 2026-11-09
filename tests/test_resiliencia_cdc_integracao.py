"""Falhas do CDC num banco descartável, recriado a cada teste a partir das migrações.

Os cenários alteram o esquema e gravam linhas que a origem insert-only nunca mais
apaga, como ocorrido_em = -infinity. Não podem rodar no banco de desenvolvimento.
"""

import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from eduinfra import auditoria_cdc, cdc, eventos, fila_de_erros, fluxo_cdc, telemetria_cdc
from eduinfra.configuracao import carregar_configuracao
from eduinfra.pgoutput import OPERACAO_DELETE, OPERACAO_INSERT, OPERACAO_TRUNCATE, OPERACAO_UPDATE
from eduinfra.vistorias_de_teste import contar_itens_por_vistoria, garantir_tecnico, inserir_vistoria

pytestmark = pytest.mark.integracao

BANCO_MODELO = "eduinfra_teste_modelo"
SLOT = "eduinfra_teste_resiliencia"
MATRICULA = "TESTE-RESILIENCIA"
OBSERVACAO = "Teste de resiliência do CDC"
ESCOLA = 100001


def _dsn_do_banco(dsn: str, banco: str) -> str:
    return urlunsplit(urlsplit(dsn)._replace(path=f"/{banco}"))


def _executar_como_admin(dsn: str, *comandos: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as admin:
        for comando in comandos:
            admin.execute(comando)


def _semear(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conexao:
        conexao.execute("insert into municipio (co_municipio, no_municipio, sg_uf, co_uf) values (1, 'Município', 'DF', 53)")
        conexao.execute(
            "insert into escola (co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia) "
            "values (%s, 'Escola', 1, 1, 1, 2024)",
            (ESCOLA,),
        )


@pytest.fixture(scope="module")
def banco_modelo():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL não definida")

    modelo = _dsn_do_banco(dsn, BANCO_MODELO)
    _executar_como_admin(dsn, f"drop database if exists {BANCO_MODELO} with (force)", f"create database {BANCO_MODELO}")
    with pytest.MonkeyPatch.context() as ambiente:
        ambiente.setenv("DATABASE_URL", modelo)
        command.upgrade(Config(str(carregar_configuracao().arquivo_alembic)), "head")
    _semear(modelo)
    yield dsn
    _executar_como_admin(dsn, f"drop database if exists {BANCO_MODELO} with (force)")


@pytest.fixture
def configuracao(banco_modelo, tmp_path):
    nome = f"eduinfra_teste_{uuid4().hex[:8]}"
    _executar_como_admin(banco_modelo, f"create database {nome} template {BANCO_MODELO}")
    yield replace(carregar_configuracao(), dsn=_dsn_do_banco(banco_modelo, nome), diretorio_dados=tmp_path)
    _executar_como_admin(
        banco_modelo,
        f"select pg_drop_replication_slot(slot_name) from pg_replication_slots where database = '{nome}'",
        f"drop database if exists {nome} with (force)",
    )


@pytest.fixture
def conexao(configuracao):
    with psycopg.connect(configuracao.dsn, autocommit=True) as aberta:
        cdc.garantir_slot(aberta, SLOT)
        yield aberta


def _inserir(conexao) -> int:
    return inserir_vistoria(conexao, MATRICULA, OBSERVACAO, datetime.now(timezone.utc) - timedelta(minutes=1))


def _inserir_com_ocorrido(conexao, expressao: str) -> int:
    tecnico = conexao.execute(
        "insert into tecnico (matricula, nome, lotacao_uf) values (%s, 'Técnico', 'DF') "
        "on conflict (matricula) do update set lotacao_uf = 'DF' returning id",
        (MATRICULA,),
    ).fetchone()[0]
    with conexao.transaction():
        vistoria = conexao.execute(
            f"insert into vistoria (co_entidade, tecnico_id, origem, ocorrido_em, observacao) "
            f"values (%s, %s, 'vistoria_presencial', {expressao}, %s) returning id",
            (ESCOLA, tecnico, OBSERVACAO),
        ).fetchone()[0]
        conexao.execute(
            "insert into vistoria_item (vistoria_id, item_codigo, situacao) "
            "select %s, codigo, 'existente' from item_infraestrutura",
            (vistoria,),
        )

    return vistoria


def _passar(configuracao, **opcoes):
    return fluxo_cdc.passar_uma_vez(configuracao, slot=SLOT, **opcoes)


def _auditar(configuracao, **opcoes):
    return auditoria_cdc.auditar(configuracao, slot=SLOT, **opcoes)


def _desviar(conexao, comando: str, *parametros) -> None:
    conexao.execute("set session_replication_role = replica")
    try:
        conexao.execute(comando, parametros or None)
    finally:
        conexao.execute("set session_replication_role = origin")


def test_publicacao_entrega_as_quatro_operacoes_mesmo_vinda_da_versao_so_com_insert(configuracao, conexao, monkeypatch):
    conexao.execute("alter publication eduinfra_cdc set (publish = 'insert')")
    monkeypatch.setenv("DATABASE_URL", configuracao.dsn)
    alembic = Config(str(configuracao.arquivo_alembic))
    command.downgrade(alembic, "0007")
    command.stamp(alembic, "0006")

    command.upgrade(alembic, "head")

    assert conexao.execute("select pubinsert, pubupdate, pubdelete, pubtruncate from pg_publication where pubname = 'eduinfra_cdc'").fetchone() == (True, True, True, True)


def test_entrega_normal_passa_na_auditoria_com_o_atraso_registrado(configuracao, conexao):
    _inserir(conexao)
    _inserir(conexao)

    resultado = _passar(configuracao)
    _passar(configuracao)
    auditoria = _auditar(configuracao)

    itens = 2 * contar_itens_por_vistoria(conexao)
    assert resultado.itens_inseridos == itens
    assert auditoria.linhas.iguais_com_lsn == itens
    assert auditoria_cdc.violacoes(auditoria) == []
    assert auditoria.latencias["ponta_a_ponta_s"].mediana_s > 0
    assert auditoria.saldo[0].lidas_do_wal == auditoria.saldo[0].aplicadas == itens


def test_vistoria_ainda_no_slot_aparece_como_pendente_e_nao_como_perda(configuracao, conexao):
    _inserir(conexao)

    auditoria = _auditar(configuracao)

    assert auditoria.linhas.pendentes == contar_itens_por_vistoria(conexao)
    assert auditoria.linhas.sem_explicacao == 0


def test_linha_que_sumiu_do_fluxo_e_violacao(configuracao, conexao):
    _inserir(conexao)
    conexao.execute("select pg_replication_slot_advance(%s, pg_current_wal_lsn())", (SLOT,))
    _passar(configuracao)

    auditoria = _auditar(configuracao)

    assert auditoria.linhas.sem_explicacao == contar_itens_por_vistoria(conexao)
    assert any("fora do Delta" in violacao for violacao in auditoria_cdc.violacoes(auditoria))


def test_coluna_nova_entrega_as_linhas_e_acusa_o_desvio(configuracao, conexao):
    conexao.execute("alter table vistoria_item add column coluna_nova text")
    _inserir(conexao)

    resultado = _passar(configuracao)
    auditoria = _auditar(configuracao)

    assert resultado.itens_inseridos == contar_itens_por_vistoria(conexao)
    assert "vistoria_item.coluna_nova: coluna_nova" in auditoria.desvios_vistos_no_wal
    assert any("coluna_nova" in violacao for violacao in auditoria_cdc.violacoes(auditoria))


def test_coluna_renomeada_vai_para_a_fila_e_nao_trava_o_que_vem_depois_de_desfazer(configuracao, conexao):
    conexao.execute("alter table vistoria_item rename column quantidade to qtd")
    _inserir(conexao)
    primeira = _passar(configuracao)
    conexao.execute("alter table vistoria_item rename column qtd to quantidade")
    _inserir(conexao)

    segunda = _passar(configuracao)
    auditoria = _auditar(configuracao)

    itens = contar_itens_por_vistoria(conexao)
    assert (primeira.itens_rejeitados, primeira.itens_inseridos) == (itens, 0)
    assert (segunda.itens_rejeitados, segunda.itens_inseridos) == (0, itens)
    assert auditoria.linhas.na_fila == itens
    assert auditoria.linhas.sem_explicacao == 0


@pytest.mark.parametrize("ocorrido_em", ["'-infinity'::timestamptz", "'4000-01-01 BC'::timestamptz"], ids=["menos-infinito", "antes-de-cristo"])
def test_data_que_o_python_nao_representa_vai_para_a_fila_e_a_vizinha_segue(configuracao, conexao, ocorrido_em):
    _inserir(conexao)
    _inserir_com_ocorrido(conexao, ocorrido_em)
    _inserir(conexao)

    resultado = _passar(configuracao)
    seguinte = _passar(configuracao)
    auditoria = _auditar(configuracao)

    itens = contar_itens_por_vistoria(conexao)
    assert (resultado.itens_inseridos, resultado.itens_rejeitados) == (2 * itens, itens)
    assert seguinte.transacoes == 0
    assert eventos.contar(configuracao) == 2 * itens
    assert auditoria.linhas.na_fila == itens
    assert auditoria.linhas.sem_explicacao == 0
    assert auditoria.linhas.iguais_com_lsn == 2 * itens
    assert all("DataError" in motivo for motivo in auditoria.fila_por_motivo)


def test_reentrega_nao_duplica_a_fila_e_aparece_na_telemetria(configuracao, conexao):
    _inserir_com_ocorrido(conexao, "'-infinity'::timestamptz")

    with pytest.raises(fluxo_cdc.FalhaSimulada):
        _passar(configuracao, falhar_antes_de_confirmar=True)
    _passar(configuracao)

    itens = contar_itens_por_vistoria(conexao)
    assert fila_de_erros.ler(configuracao).num_rows == itens
    ordem = telemetria_cdc.resumir_ordem(telemetria_cdc.ler_telemetria(configuracao))
    assert ordem.reentregas >= 1
    assert ordem.inversoes_de_lsn == 0


def _sem_consumir_antes(configuracao, conexao) -> int:
    vistoria = _inserir(conexao)
    _passar(configuracao)
    return vistoria


def test_update_que_desvia_dos_gatilhos_e_contado_e_o_checksum_acusa(configuracao, conexao):
    vistoria = _sem_consumir_antes(configuracao, conexao)
    _desviar(conexao, "update vistoria_item set situacao = 'inexistente' where vistoria_id = %s", vistoria)

    resultado = _passar(configuracao)
    auditoria = _auditar(configuracao)

    itens = contar_itens_por_vistoria(conexao)
    assert resultado.transacoes == 1
    assert auditoria.linhas.diferentes_com_lsn == itens
    assert next(s for s in auditoria.saldo if s.operacao == OPERACAO_UPDATE).lidas_do_wal == itens
    assert next(s for s in auditoria.saldo if s.operacao == OPERACAO_UPDATE).na_fila == itens
    assert any("insert-only" in violacao for violacao in auditoria_cdc.violacoes(auditoria))


def test_delete_que_desvia_dos_gatilhos_deixa_a_linha_so_no_delta(configuracao, conexao):
    vistoria = _sem_consumir_antes(configuracao, conexao)
    _desviar(conexao, "delete from vistoria_item where vistoria_id = %s", vistoria)

    _passar(configuracao)
    auditoria = _auditar(configuracao)

    itens = contar_itens_por_vistoria(conexao)
    assert auditoria.linhas.so_no_delta == itens
    assert next(s for s in auditoria.saldo if s.operacao == OPERACAO_DELETE).lidas_do_wal == itens


def test_truncate_que_desvia_dos_gatilhos_e_contado(configuracao, conexao):
    _sem_consumir_antes(configuracao, conexao)
    _desviar(conexao, "truncate vistoria_item")

    _passar(configuracao)
    auditoria = _auditar(configuracao)

    assert next(s for s in auditoria.saldo if s.operacao == OPERACAO_TRUNCATE).lidas_do_wal == 1
    assert auditoria.linhas.so_no_delta == contar_itens_por_vistoria(conexao)


def test_transacao_longa_inverte_o_registro_escapa_da_marca_dagua_e_chega_pelo_slot(configuracao, conexao):
    evento = datetime.now(timezone.utc) - timedelta(minutes=1)
    garantir_tecnico(conexao, MATRICULA)
    with psycopg.connect(configuracao.dsn) as longa, psycopg.connect(configuracao.dsn) as curta:
        inserir_vistoria(longa, MATRICULA, OBSERVACAO, evento)
        time.sleep(0.2)
        inserir_vistoria(curta, MATRICULA, OBSERVACAO, evento)
        curta.commit()
        time.sleep(0.05)
        longa.commit()

    resultado = _passar(configuracao)
    telemetria = telemetria_cdc.ler_telemetria(configuracao)
    ordem = telemetria_cdc.resumir_ordem(telemetria)
    pares = [(linha["registrado_em"], linha["confirmada_em"]) for linha in telemetria if linha["registrado_em"]]

    assert resultado.itens_inseridos == 2 * contar_itens_por_vistoria(conexao)
    assert ordem.inversoes_de_lsn == 0
    assert ordem.inversoes_de_registro == 1
    assert ordem.maior_inversao_de_registro_s >= 0.2
    assert telemetria_cdc.perdas_por_marca_dagua(pares, periodo_s=0.001, margem_s=0) == 1
    assert telemetria_cdc.perdas_por_marca_dagua(pares, periodo_s=0.001, margem_s=600) == 0


def test_passada_ociosa_renova_o_frescor_e_sla_estourado_vira_violacao(configuracao, conexao):
    _passar(configuracao)
    primeiro = telemetria_cdc.ler_frescor(configuracao)
    time.sleep(0.01)
    _passar(configuracao)

    auditoria = _auditar(configuracao, sla_de_frescor_s=0)

    assert telemetria_cdc.ler_frescor(configuracao) > primeiro
    assert auditoria.frescor_s > 0
    assert any("SLA" in violacao for violacao in auditoria_cdc.violacoes(auditoria))


def test_slot_inexistente_e_violacao_sem_quebrar_a_auditoria(configuracao, conexao):
    _inserir(conexao)
    cdc.remover_slot(conexao, SLOT)

    auditoria = _auditar(configuracao)

    encontradas = auditoria_cdc.violacoes(auditoria)
    assert any("não existe" in violacao for violacao in encontradas)
    assert auditoria.linhas.sem_explicacao == contar_itens_por_vistoria(conexao)


def test_linha_rejeitada_guarda_so_a_chave_e_o_motivo(configuracao, conexao):
    _inserir_com_ocorrido(conexao, "'-infinity'::timestamptz")
    _passar(configuracao)

    linha = fila_de_erros.ler(configuracao).to_pylist()[0]

    assert linha["operacao"] == OPERACAO_INSERT
    assert OBSERVACAO not in str(linha)
    assert set(linha) == {campo.name for campo in fila_de_erros.ESQUEMA}
