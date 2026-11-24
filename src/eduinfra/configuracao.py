import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

URL_PADRAO_CENSO = "https://download.inep.gov.br/dados_abertos/microdados_censo_escolar_2024.zip"
# Link da página de microdados do ENEM, conferido em 08/11/2026 (docs/enem.md).
URL_PADRAO_ENEM = "https://download.inep.gov.br/microdados/microdados_enem_2024.zip"

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
    url_enem: str = URL_PADRAO_ENEM
    ano_enem: int = 2024
    # Superusuário usado apenas por migrar/reverter (issue #86).
    dsn_admin: str | None = None
    # Papel da transformação, lido pelo dbt.
    dsn_transformacao: str | None = None
    # Orquestrador (carga + dbt) da opção nula construir-gold e do pipeline.
    dsn_orquestrador: str | None = None
    # Papel de bancada do CDC (replicação sem ser o consumidor de rotina).
    dsn_cdc_bancada: str | None = None


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
        url_enem=os.environ.get("URL_ENEM", URL_PADRAO_ENEM),
        ano_enem=int(os.environ.get("ANO_ENEM", "2024")),
        dsn_admin=os.environ.get("DATABASE_URL_ADMIN"),
        dsn_transformacao=os.environ.get("DATABASE_URL_TRANSFORMACAO"),
        dsn_orquestrador=os.environ.get("DATABASE_URL_ORQUESTRADOR"),
        dsn_cdc_bancada=os.environ.get("DATABASE_URL_CDC_BANCADA"),
    )
