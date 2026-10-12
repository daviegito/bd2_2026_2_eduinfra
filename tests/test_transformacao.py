"""Transformação sobre um CSV do Censo em miniatura, sem banco."""

from datetime import date
from pathlib import Path

import duckdb
import pytest

from eduinfra.configuracao import Configuracao
from eduinfra.transformacao import preparar_camada_silver

COLUNAS_POR_ITEM = {
    "biblioteca": "IN_BIBLIOTECA",
    "quadra_esportes": "IN_QUADRA_ESPORTES",
}

CABECALHO = (
    "NU_ANO_CENSO;NO_REGIAO;CO_UF;SG_UF;NO_MUNICIPIO;CO_MUNICIPIO;NO_ENTIDADE;"
    "CO_ENTIDADE;TP_DEPENDENCIA;TP_LOCALIZACAO;TP_SITUACAO_FUNCIONAMENTO;"
    "IN_BIBLIOTECA;IN_QUADRA_ESPORTES"
)

LINHAS = [
    "2024;Centro-Oeste;53;DF;Brasília;5300108;ESCOLA ATIVA COM TUDO;53000001;2;1;1;1;1",
    "2024;Centro-Oeste;53;DF;Brasília;5300108;ESCOLA ATIVA SEM QUADRA;53000002;3;2;1;1;0",
    "2024;Centro-Oeste;53;DF;Brasília;5300108;ESCOLA SEM RESPOSTA;53000003;4;1;1;;1",
    "2024;Centro-Oeste;53;DF;Brasília;5300108;ESCOLA PARALISADA;53000004;2;1;2;1;1",
]


@pytest.fixture
def censo_reduzido(tmp_path: Path) -> Path:
    arquivo = tmp_path / "microdados_ed_basica_2024.csv"
    arquivo.write_text("\n".join([CABECALHO, *LINHAS]), encoding="latin-1")

    return arquivo


@pytest.fixture
def configuracao(tmp_path: Path) -> Configuracao:
    return Configuracao(
        dsn="postgresql://irrelevante",
        url_censo="https://exemplo.invalido/censo.zip",
        ano_censo=2024,
        data_referencia=date(2024, 5, 29),
        diretorio_dados=tmp_path / "dados",
        arquivo_alembic=tmp_path / "alembic.ini",
    )


def ler(parquet: Path) -> list[tuple]:
    return duckdb.connect().execute(f"select * from read_parquet('{parquet.as_posix()}')").fetchall()


def test_escola_paralisada_fica_de_fora(configuracao, censo_reduzido):
    camada = preparar_camada_silver(configuracao, censo_reduzido, COLUNAS_POR_ITEM)

    codigos = {linha[0] for linha in ler(camada.escolas)}

    assert codigos == {53000001, 53000002, 53000003}


def test_municipio_sai_deduplicado(configuracao, censo_reduzido):
    camada = preparar_camada_silver(configuracao, censo_reduzido, COLUNAS_POR_ITEM)

    assert ler(camada.municipios) == [(5300108, "Brasília", "DF", 53)]


@pytest.mark.parametrize(
    ("escola", "item", "situacao"),
    [
        (53000001, "quadra_esportes", "existente"),
        (53000002, "quadra_esportes", "inexistente"),
        (53000003, "biblioteca", "nao_informado"),
    ],
)
def test_indicador_do_censo_vira_situacao(configuracao, censo_reduzido, escola, item, situacao):
    camada = preparar_camada_silver(configuracao, censo_reduzido, COLUNAS_POR_ITEM)

    itens = {(linha[0], linha[1]): linha[2] for linha in ler(camada.itens)}

    assert itens[(escola, item)] == situacao


def test_coluna_ausente_no_censo_interrompe_a_carga(configuracao, censo_reduzido):
    catalogo = {**COLUNAS_POR_ITEM, "laboratorio": "IN_LABORATORIO_CIENCIAS"}

    with pytest.raises(RuntimeError, match="IN_LABORATORIO_CIENCIAS"):
        preparar_camada_silver(configuracao, censo_reduzido, catalogo)
