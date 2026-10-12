"""Comparação de formatos sobre uma camada Silver em miniatura, sem banco."""

from pathlib import Path

import duckdb
import pytest

from eduinfra.comparacao_compressao import FORMATOS, comparar, formatar_markdown, respostas_coincidem


@pytest.fixture
def silver(tmp_path: Path) -> Path:
    destino = tmp_path / "silver"
    destino.mkdir()
    conexao = duckdb.connect()
    conexao.execute(
        f"""
        copy (select * from (values (5300108, 'Brasília', 'DF', 53), (3550308, 'São Paulo', 'SP', 35))
              t(co_municipio, no_municipio, sg_uf, co_uf))
        to '{(destino / "municipio.parquet").as_posix()}' (format parquet)
        """
    )
    conexao.execute(
        f"""
        copy (select * from (values (53000001, 'ESCOLA A', 5300108, 2, 1, 2024),
                                    (53000002, 'ESCOLA B', 5300108, 3, 2, 2024),
                                    (35000001, 'ESCOLA C', 3550308, 2, 1, 2024))
              t(co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia))
        to '{(destino / "escola.parquet").as_posix()}' (format parquet)
        """
    )
    conexao.execute(
        f"""
        copy (select * from (values (53000001, 'lab_ciencias', 'inexistente'),
                                    (53000001, 'biblioteca', 'existente'),
                                    (53000002, 'lab_ciencias', 'inexistente'),
                                    (53000002, 'biblioteca', 'inexistente'),
                                    (35000001, 'lab_ciencias', 'inexistente'))
              t(co_entidade, item_codigo, situacao))
        to '{(destino / "vistoria_item.parquet").as_posix()}' (format parquet)
        """
    )
    conexao.close()

    return destino


def test_mede_todos_os_formatos(silver):
    comparacao = comparar(silver, repeticoes=1)

    assert [medida.formato for medida in comparacao.medidas] == list(FORMATOS)
    assert all(medida.bytes_em_disco > 0 for medida in comparacao.medidas)
    assert comparacao.linhas == {"municipio": 2, "escola": 3, "vistoria_item": 5}


def test_trocar_o_formato_nao_muda_o_dado(silver):
    comparacao = comparar(silver, repeticoes=1)

    assert respostas_coincidem(comparacao)
    zstd = next(medida for medida in comparacao.medidas if medida.formato == "zstd")
    assert zstd.respostas["filtro_uf"] == [(2,)]


def test_nao_deixa_arquivos_temporarios(silver):
    comparar(silver, repeticoes=1)

    assert not list(silver.parent.glob("comparacao_compressao_*"))


def test_aceita_silver_particionada_por_ano(silver):
    particao = silver / "ano_censo=2024"
    particao.mkdir()
    for arquivo in list(silver.glob("*.parquet")):
        arquivo.rename(particao / arquivo.name)

    assert comparar(silver, repeticoes=1).linhas["vistoria_item"] == 5


def test_relatorio_cita_os_codecs(silver):
    relatorio = formatar_markdown(comparar(silver, repeticoes=1))

    for nome in ("CSV (texto)", "Parquet uncompressed", "Parquet snappy", "Parquet zstd"):
        assert nome in relatorio
    assert "Respostas idênticas em todos os formatos: sim" in relatorio
