"""Execução real do dbt: equivalência, grafo, histórico e ENEM por contrato."""

import json
import subprocess

import pytest

from eduinfra import modelo_analitico
from eduinfra.fontes_transformacao import bloquear, preparar
from tests.test_fontes_transformacao import arquivos_da_origem
from tests.test_modelo_analitico import dsn, historia_da_escola_um, consultar, vistoria, construir
from transformacao.scripts.executar import dbt, verificar_slot

pytestmark = pytest.mark.integracao


def linhas(dsn, schema, tabela):
    return consultar(dsn, f"select to_jsonb(t)::text from {schema}.{tabela} t order by 1")


def test_build_real_equivale_a_gold_preserva_historico_e_documenta_grafo(dsn, tmp_path, monkeypatch):
    historia_da_escola_um(dsn)
    # Quantidade e nao_informado não podem apagar o conhecimento anterior.
    vistoria(dsn, 1, "2025-07-01", {"lab_ciencias": ("existente", 2)})
    vistoria(dsn, 1, "2025-08-01", {"lab_ciencias": "existente", "biblioteca": "nao_informado"})
    alvo = tmp_path / "target"
    monkeypatch.setenv("DBT_TARGET_PATH", str(alvo))
    monkeypatch.setenv("DBT_LOG_PATH", str(tmp_path / "logs"))
    construir(dsn)
    entradas = arquivos_da_origem(dsn, tmp_path / "dados", com_enem=True, delta=True)
    with bloquear(dsn) as conexao:
        preparar(conexao, entradas)
        # Uma vistoria posterior ao retrato não pode vazar para os modelos desta execução.
        vistoria(dsn, 2, "2025-09-01", {"biblioteca": "existente"})
        dbt("build", dsn, True, "gold_dbt")
        for tabela in modelo_analitico.TABELAS_DA_GOLD:
            assert linhas(dsn, "gold", tabela) == linhas(dsn, "gold_dbt", tabela), tabela
        assert consultar(dsn, "select co_entidade, n_participantes from gold_dbt.fato_desempenho_escola_ano") == [(1, 10)]
        assert consultar(dsn, "select count(*) from entrada_transformacao.enem_escola_ano") == [(2,)]
        antes = {t: linhas(dsn, "gold_dbt", t) for t in modelo_analitico.TABELAS_DA_GOLD}
        preparar(conexao, entradas)
        dbt("build", dsn, True, "gold_dbt")
        assert antes == {t: linhas(dsn, "gold_dbt", t) for t in antes}
        # A nota de 2024 se liga ao perfil anterior à reforma de 2025.
        assert consultar(dsn, "select d.itens ->> 'lab_ciencias' from gold_dbt.fato_desempenho_escola_ano f join gold_dbt.dim_escola_infraestrutura d using (escola_infra_sk)") == [("inexistente",)]
        dbt("docs generate", dsn, True, "gold_dbt")
    manifesto = json.loads((alvo / "manifest.json").read_text())
    assert (alvo / "index.html").exists()
    assert (alvo / "catalog.json").exists()
    fato = manifesto["nodes"]["model.eduinfra_gold.fato_vistoria_item"]
    assert "model.eduinfra_gold.stg_eventos_vistoria" in fato["depends_on"]["nodes"]
    assert not any(s.get("schema") == "public" for s in manifesto["sources"].values())
    # Um teste de dados precisa acusar um defeito real, com saída de erro para o job.
    with bloquear(dsn) as conexao:
        conexao.execute("update gold_dbt.dim_escola_infraestrutura set valid_to = valid_from where is_current")
        with pytest.raises(subprocess.CalledProcessError):
            dbt("test --select scd_sem_sobreposicao", dsn, True, "gold_dbt")


def test_build_sem_enem_nao_fabrica_fato_de_desempenho(dsn, tmp_path, monkeypatch):
    historia_da_escola_um(dsn)
    entradas = arquivos_da_origem(dsn, tmp_path / "dados")
    monkeypatch.setenv("DBT_TARGET_PATH", str(tmp_path / "target"))
    monkeypatch.setenv("DBT_LOG_PATH", str(tmp_path / "logs"))
    with bloquear(dsn) as conexao:
        preparar(conexao, entradas)
        dbt("build", dsn, False, "gold_dbt")
    assert consultar(dsn, "select to_regclass('gold_dbt.fato_desempenho_escola_ano')") == [(None,)]


def test_preflight_recusa_cluster_com_slot_sem_gerar_wal_da_gold(dsn):
    with bloquear(dsn) as conexao:
        # O nome do banco descartável é único, inclusive entre execuções concorrentes.
        nome = conexao.execute("select current_database()").fetchone()[0]
        conexao.execute("select pg_create_logical_replication_slot(%s, 'pgoutput')", (nome,))
        try:
            with pytest.raises(RuntimeError, match="construção bloqueada"):
                verificar_slot(conexao)
            assert conexao.execute("select to_regnamespace('gold')").fetchone()[0] is None
        finally:
            conexao.execute("select pg_drop_replication_slot(%s)", (nome,))
