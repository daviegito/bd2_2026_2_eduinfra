"""Contratos de entrada: falhas não publicam um retrato parcial para o dbt."""

from datetime import date
from dataclasses import replace

import pyarrow as pa
import pyarrow.parquet as pq
import psycopg
import pytest
from deltalake import write_deltalake

from eduinfra import enem as enem_modulo
from eduinfra import fontes_transformacao as fontes
from eduinfra.fontes_transformacao import COLUNAS, Entradas, bloquear, preparar
from tests.test_enem import escrever_resultados, linha
from tests.test_modelo_analitico import configuracao_de
from tests.test_modelo_analitico import dsn, historia_da_escola_um, consultar, vistoria

pytestmark = pytest.mark.integracao


def gravar_consulta(dsn, consulta, destino):
    with psycopg.connect(dsn) as conexao:
        cursor = conexao.execute(consulta)
        nomes = [coluna.name for coluna in cursor.description]
        linhas = [dict(zip(nomes, linha)) for linha in cursor]
    destino.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(linhas), destino)


def arquivos_da_origem(dsn, pasta, com_enem=False, delta=False):
    silver = pasta / "silver" / "ano_censo=2024"
    gravar_consulta(dsn, "select co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia from escola", silver / "escola.parquet")
    gravar_consulta(dsn, "select * from municipio", silver / "municipio.parquet")
    eventos = pasta / "eventos.parquet"
    gravar_consulta(dsn, "select v.id as vistoria_id, i.item_codigo, v.co_entidade, v.origem, v.ocorrido_em, v.registrado_em, i.situacao::text, i.quantidade from vistoria v join vistoria_item i on i.vistoria_id = v.id", eventos)
    if delta:
        destino = pasta / "eventos_delta"
        write_deltalake(destino, pq.read_table(eventos))
        eventos = destino
    enem = None
    if com_enem:
        configuracao = replace(configuracao_de(dsn), diretorio_dados=pasta)
        csv = escrever_resultados(pasta, [
            linha(n, 1, ("1", "1", "1", "1"), ("500", "510", "520", "530"), "1", "600")
            for n in range(1, 11)
        ] + [linha(11, 60000001, ("1", "1", "1", "1"), ("400", "400", "400", "400"), "1", "400")])
        _, preparo = enem_modulo.montar_silver_enem(configuracao, csv, "fixture-enem")
        enem = enem_modulo.publicar_silver_enem(configuracao, preparo) / "desempenho_escola.parquet"
    return Entradas(silver.parent, eventos, enem)


@pytest.fixture
def entradas(dsn, tmp_path):
    historia_da_escola_um(dsn)
    return arquivos_da_origem(dsn, tmp_path)


def test_reexecucao_preserva_contagens_e_nao_exporta_dado_do_tecnico(dsn, entradas):
    with bloquear(dsn) as conexao:
        primeira = preparar(conexao, entradas)
        segunda = preparar(conexao, entradas)
    assert primeira == segunda
    assert primeira["linhas"]["eventos_vistoria"] == 7
    assert not primeira["com_enem"]
    assert "tecnico_id" not in COLUNAS["eventos_vistoria"]


def test_fonte_delta_fixa_a_versao_e_consome_o_mesmo_contrato(dsn, tmp_path):
    historia_da_escola_um(dsn)
    entradas = arquivos_da_origem(dsn, tmp_path, delta=True)
    with bloquear(dsn) as conexao:
        resumo = preparar(conexao, entradas)
    assert resumo["versao_delta"] == 0
    assert resumo["linhas"]["eventos_vistoria"] == 7


def test_duplicata_de_evento_falha_e_preserva_retrato_anterior(dsn, entradas):
    with bloquear(dsn) as conexao:
        preparar(conexao, entradas)
        dados = pq.read_table(entradas.eventos)
        pq.write_table(pa.concat_tables([dados, dados.slice(0, 1)]), entradas.eventos)
        with pytest.raises(psycopg.errors.UniqueViolation):
            preparar(conexao, entradas)
        assert conexao.execute("select count(*) from entrada_transformacao.eventos_vistoria").fetchone()[0] == 7


def test_cabecalho_incompativel_falha_sem_publicar(dsn, entradas):
    with bloquear(dsn) as conexao:
        preparar(conexao, entradas)
        pq.write_table(pa.table({"qualquer": [1]}), entradas.eventos)
        with pytest.raises(RuntimeError, match="colunas ausentes"):
            preparar(conexao, entradas)
        assert conexao.execute("select count(*) from entrada_transformacao.censo_escola").fetchone()[0] == 3


def test_enem_configurado_e_ausente_nao_vira_tabela_vazia(dsn, entradas, tmp_path):
    with bloquear(dsn) as conexao:
        with pytest.raises(RuntimeError, match="FONTE_ENEM não existe"):
            preparar(conexao, Entradas(entradas.silver, entradas.eventos, tmp_path / "ausente"))


def test_bloqueio_impede_trocar_as_fontes_durante_outro_build(dsn):
    with bloquear(dsn):
        with pytest.raises(RuntimeError, match="outra transformação"):
            with bloquear(dsn):
                pytest.fail("duas execuções entraram no mesmo retrato")
    with bloquear(dsn):
        pass


def test_enem_publicado_preserva_todas_escolas_e_referencia_explicita(dsn, tmp_path):
    historia_da_escola_um(dsn)
    entradas = arquivos_da_origem(dsn, tmp_path, com_enem=True)
    with bloquear(dsn) as conexao:
        resumo = preparar(conexao, entradas)
    assert resumo["com_enem"]
    assert consultar(dsn, "select co_entidade, ano, data_referencia_infraestrutura::text from entrada_transformacao.enem_escola_ano order by co_entidade") == [(1, 2024, "2024-11-03"), (60000001, 2024, "2024-11-03")]
    assert resumo["cobertura_enem"] == {"escolas_enem": 2, "escolas_com_censo": 1, "escolas_sem_censo": 1, "poucos_sem_censo": 1, "participantes": 11, "participantes_com_censo": 10}


def test_mesma_escola_e_ano_com_cadastros_conflitantes_nao_escolhe_um_ao_acaso(dsn, entradas):
    escolas = next(entradas.silver.glob("ano_censo=*/escola.parquet"))
    dados = pq.read_table(escolas)
    outra = dados.slice(0, 1).to_pylist()[0]
    outra["no_entidade"] = "OUTRO CADASTRO"
    pq.write_table(pa.concat_tables([dados, pa.Table.from_pylist([outra], schema=dados.schema)]), escolas)
    with bloquear(dsn) as conexao:
        with pytest.raises(RuntimeError, match="cadastros conflitantes"):
            preparar(conexao, entradas)


def test_referencia_temporal_parametrizada_e_validada_no_ano(dsn, tmp_path):
    historia_da_escola_um(dsn)
    entradas = arquivos_da_origem(dsn, tmp_path, com_enem=True)
    with bloquear(dsn) as conexao:
        resumo = preparar(conexao, replace(entradas, referencia_enem=date(2024, 11, 10)))
        assert resumo["referencia_enem"] == "2024-11-10"
        with pytest.raises(psycopg.errors.CheckViolation):
            preparar(conexao, replace(entradas, referencia_enem=date(2025, 11, 3)))
        assert conexao.execute("select min(data_referencia_infraestrutura)::text from entrada_transformacao.enem_escola_ano").fetchone()[0] == "2024-11-10"


def test_evento_de_outra_origem_nao_publica_retrato(dsn, entradas):
    dados = pq.read_table(entradas.eventos).to_pylist()
    dados[0]["vistoria_id"] = 999999
    pq.write_table(pa.Table.from_pylist(dados), entradas.eventos)
    with bloquear(dsn) as conexao:
        with pytest.raises(RuntimeError, match="eventos sem chave na origem"):
            preparar(conexao, entradas)


def test_entrada_temporaria_e_unlogged_sem_tocar_na_origem(dsn, entradas):
    with bloquear(dsn) as conexao:
        preparar(conexao, entradas)
        assert conexao.execute("select relpersistence from pg_class where oid = 'entrada_transformacao.eventos_vistoria'::regclass").fetchone() == ("u",)
        assert conexao.execute("select count(*) from public.vistoria_item").fetchone()[0] == 7


def test_versao_delta_e_fixada_antes_do_snapshot_postgres(dsn, tmp_path, monkeypatch):
    historia_da_escola_um(dsn)
    entradas = arquivos_da_origem(dsn, tmp_path, delta=True)
    copiar_real = fontes._copiar_parquet
    alterada = False

    def copiar_e_publicar(conexao, tabela, caminhos):
        nonlocal alterada
        total = copiar_real(conexao, tabela, caminhos)
        if tabela == "censo_escola" and not alterada:
            alterada = True
            novo = vistoria(dsn, 2, "2025-09-01", {"biblioteca": "existente"})
            arquivo = tmp_path / "evento_novo.parquet"
            gravar_consulta(dsn,
                f"select v.id as vistoria_id, i.item_codigo, v.co_entidade, v.origem, v.ocorrido_em, v.registrado_em, i.situacao::text, i.quantidade from vistoria v join vistoria_item i on i.vistoria_id=v.id where v.id={novo}", arquivo)
            write_deltalake(entradas.eventos, pq.read_table(arquivo), mode="append")
        return total

    monkeypatch.setattr(fontes, "_copiar_parquet", copiar_e_publicar)
    with bloquear(dsn) as conexao:
        antes = preparar(conexao, entradas)
        assert (antes["versao_delta"], antes["linhas"]["eventos_vistoria"]) == (0, 7)
        depois = preparar(conexao, entradas)
        assert (depois["versao_delta"], depois["linhas"]["eventos_vistoria"]) == (1, 8)
