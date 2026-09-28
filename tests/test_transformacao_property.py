from pathlib import Path
import tempfile
from datetime import date

from hypothesis import given, strategies as st

from eduinfra.transformacao import preparar_camada_silver
from eduinfra.configuracao import Configuracao, DATA_REFERENCIA_CENSO_2024


def make_config(tmpdir: Path) -> Configuracao:
    return Configuracao(
        dsn="sqlite:///:memory:",
        url_censo="http://example.com/censo.zip",
        ano_censo=2024,
        data_referencia=DATA_REFERENCIA_CENSO_2024,
        diretorio_dados=tmpdir,
        arquivo_alembic=Path("alembic.ini"),
    )


@st.composite
def csv_row(draw):
    item_val = draw(st.sampled_from(["1", "0", "", "9"]))
    return {
        "NU_ANO_CENSO": "2024",
        "CO_ENTIDADE": "1",
        "NO_ENTIDADE": "Escola X",
        "CO_MUNICIPIO": "123",
        "NO_MUNICIPIO": "Mun X",
        "SG_UF": "UF",
        "CO_UF": "12",
        "TP_DEPENDENCIA": "1",
        "TP_LOCALIZACAO": "1",
        "TP_SITUACAO_FUNCIONAMENTO": "1",
        "ITEM_A": item_val,
    }


@given(rows=st.lists(csv_row(), min_size=1, max_size=5))
def test_preparar_camada_silver_with_generated_rows(rows):
    header = ";".join(rows[0].keys())
    lines = [header]
    for r in rows:
        lines.append(";".join(r[k] for k in r.keys()))

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        csv = tmp_path / "bruto.csv"
        csv.write_text("\n".join(lines), encoding="latin-1")

        colunas = {"ITEM_A": "ITEM_A"}
        cfg = make_config(tmp_path)

        camada = preparar_camada_silver(cfg, csv, colunas)

        assert camada.municipios.exists()
        assert camada.escolas.exists()
        assert camada.itens.exists()