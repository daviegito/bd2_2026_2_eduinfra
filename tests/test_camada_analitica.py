"""Tabela Delta da camada analítica sobre uma Silver em miniatura, sem banco."""

from pathlib import Path

import duckdb
import pytest

from eduinfra import camada_analitica
from eduinfra.camada_analitica import caminho_da_tabela, contar_no_duckdb, publicar
from eduinfra.comparacao_particionamento import (
    comparar,
    formatar_markdown,
    respostas_coincidem,
)


@pytest.fixture(scope="module", autouse=True)
def extensao_delta():
    conexao = duckdb.connect()
    try:
        camada_analitica.abrir_no_duckdb(conexao, Path("/inexistente"), "sonda")
    except duckdb.IOException:
        pass  # a extensão carregou; só a tabela não existe
    except duckdb.Error as erro:
        pytest.skip(f"extensão delta do DuckDB indisponível: {erro}")
    finally:
        conexao.close()


def _escrever_silver(destino: Path, ano: int, escolas: list[tuple], itens: list[tuple]) -> None:
    destino.mkdir(parents=True)
    conexao = duckdb.connect()
    conexao.execute(
        f"""
        copy (select * from (values (5300108, 'Brasília', 'DF', 53), (3550308, 'São Paulo', 'SP', 35))
              t(co_municipio, no_municipio, sg_uf, co_uf))
        to '{(destino / "municipio.parquet").as_posix()}' (format parquet)
        """
    )
    linhas = ", ".join(f"({e}, 'ESCOLA', {m}, 2, 1, {ano})" for e, m in escolas)
    conexao.execute(
        f"""
        copy (select * from (values {linhas})
              t(co_entidade, no_entidade, co_municipio, tp_dependencia, tp_localizacao, ano_censo_referencia))
        to '{(destino / "escola.parquet").as_posix()}' (format parquet)
        """
    )
    linhas = ", ".join(f"({e}, '{item}', '{situacao}')" for e, item, situacao in itens)
    conexao.execute(
        f"""
        copy (select * from (values {linhas}) t(co_entidade, item_codigo, situacao))
        to '{(destino / "vistoria_item.parquet").as_posix()}' (format parquet)
        """
    )
    conexao.close()


ESCOLAS = [(53000001, 5300108), (53000002, 5300108), (35000001, 3550308)]
ITENS = [
    (53000001, "lab_ciencias", "inexistente"),
    (53000001, "biblioteca", "existente"),
    (53000002, "lab_ciencias", "inexistente"),
    (53000002, "biblioteca", "inexistente"),
    (35000001, "lab_ciencias", "inexistente"),
]


@pytest.fixture
def dados(tmp_path: Path) -> Path:
    _escrever_silver(tmp_path / "dados" / "silver", 2024, ESCOLAS, ITENS)

    return tmp_path / "dados"


def test_publica_e_le_de_volta_com_duckdb(dados):
    resumo = publicar(dados)

    assert resumo.linhas_por_ano == {2024: 5}
    assert contar_no_duckdb(resumo.caminho) == {2024: 5}


def test_particiona_por_ano_e_uf(dados):
    publicar(dados)

    tabela = caminho_da_tabela(dados)
    assert (tabela / "_delta_log").is_dir()
    assert {p.relative_to(tabela).as_posix() for p in tabela.glob("ano_censo=*/sg_uf=*")} == {
        "ano_censo=2024/sg_uf=DF",
        "ano_censo=2024/sg_uf=SP",
    }


def test_publicar_de_novo_nao_duplica(dados):
    publicar(dados)
    resumo = publicar(dados)

    assert contar_no_duckdb(resumo.caminho) == {2024: 5}
    assert resumo.versao == 1


def test_silver_particionada_por_ano_vira_um_ano_por_particao(dados):
    silver = dados / "silver"
    particao = silver / "ano_censo=2024"
    particao.mkdir()
    for arquivo in list(silver.glob("*.parquet")):
        arquivo.rename(particao / arquivo.name)
    _escrever_silver(silver / "ano_censo=2025", 2025, ESCOLAS[:1], ITENS[:2])

    resumo = publicar(dados)

    assert resumo.linhas_por_ano == {2024: 5, 2025: 2}
    assert contar_no_duckdb(resumo.caminho) == {2024: 5, 2025: 2}


def test_silver_ausente_explica_o_que_fazer(tmp_path):
    with pytest.raises(RuntimeError, match="rode a carga"):
        publicar(tmp_path)


def test_compactacao_junta_os_lotes_sem_mudar_o_dado(dados):
    comparacao = comparar(dados / "silver", repeticoes=1, lotes=4)

    antes, depois = comparacao.compactacao
    assert antes.arquivos > depois.arquivos
    assert depois.arquivos == 2  # uma por partição: 2024/DF e 2024/SP
    assert respostas_coincidem(comparacao)
    assert not list(dados.glob("comparacao_particionamento_*"))


def test_evolucao_de_esquema_nao_reescreve_arquivos(dados):
    evolucao = comparar(dados / "silver", repeticoes=1, lotes=2).evolucao

    assert evolucao.arquivos_antigos_intactos
    assert evolucao.linhas_antigas_com_nulo == 5
    assert evolucao.linhas_novas_com_valor == 4  # itens das escolas do DF


def test_relatorio_cita_os_cenarios(dados):
    relatorio = formatar_markdown(comparar(dados / "silver", repeticoes=1, lotes=2))

    for nome in ("Sem partição", "Por ano e UF", "Antes: 2 lotes", "Depois de compactar", "sem reescrita"):
        assert nome in relatorio
    assert "Respostas idênticas em todos os cenários: sim" in relatorio
