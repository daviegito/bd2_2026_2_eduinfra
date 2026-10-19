from pathlib import Path
import duckdb
import tempfile
import os

from eduinfra.transformacao import (
    preparar_camada_silver,
    CamadaSilver,
    COLUNAS_CADASTRAIS,
)
from eduinfra.configuracao import Configuracao


def make_config(tmpdir: Path) -> Configuracao:
    return Configuracao(
        dsn="sqlite:///:memory:",
        url_censo="http://example.com/censo.zip",
        ano_censo=2024,
        data_referencia=None,
        diretorio_dados=tmpdir,
        arquivo_alembic=Path("alembic.ini"),
    )


def test_preparar_camada_silver_creates_parquet(tmp_path: Path):
    csv = tmp_path / "bruto.csv"
    csv.write_text("NU_ANO_CENSO;CO_ENTIDADE;NO_ENTIDADE;CO_MUNICIPIO;NO_MUNICIPIO;SG_UF;CO_UF;TP_DEPENDENCIA;TP_LOCALIZACAO;TP_SITUACAO_FUNCIONAMENTO;ITEM_A\n2024;1;Escola A;123;Mun A;UF;12;1;1;1;1", encoding="latin-1")

    colunas = {"ITEM_A": "ITEM_A"}
    cfg = make_config(tmp_path)

    camada = preparar_camada_silver(cfg, csv, colunas)

    assert isinstance(camada, CamadaSilver)
    assert camada.municipios.exists()
    assert camada.escolas.exists()
    assert camada.itens.exists()
