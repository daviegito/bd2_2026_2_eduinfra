import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

URL_PADRAO_CENSO = "https://download.inep.gov.br/dados_abertos/microdados_censo_escolar_2024.zip"

# Data de referência do Censo 2024, usada como carimbo do evento.
DATA_REFERENCIA_CENSO_2024 = date(2024, 5, 29)


@dataclass(frozen=True)
class Configuracao:
    dsn: str
    url_censo: str
    ano_censo: int
    data_referencia: date
    diretorio_dados: Path
    arquivo_alembic: Path


def carregar_configuracao() -> Configuracao:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL não definida. Copie .env.example para .env ou exporte a variável antes de rodar."
        )

    raiz = Path(__file__).resolve().parents[2]

    return Configuracao(
        dsn=dsn,
        url_censo=os.environ.get("URL_CENSO", URL_PADRAO_CENSO),
        ano_censo=int(os.environ.get("ANO_CENSO", "2024")),
        data_referencia=DATA_REFERENCIA_CENSO_2024,
        diretorio_dados=Path(os.environ.get("DIRETORIO_DADOS", raiz / "dados")),
        arquivo_alembic=raiz / "alembic.ini",
    )
