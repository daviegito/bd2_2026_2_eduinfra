import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def url_do_ambiente() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL não definida. Copie .env.example para .env ou exporte a variável."
        )

    # O projeto usa psycopg 3; sem o sufixo o SQLAlchemy procuraria psycopg2.
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg://", 1)

    return dsn


def migrar_sem_conexao() -> None:
    context.configure(
        url=url_do_ambiente(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def migrar_com_conexao() -> None:
    config.set_main_option("sqlalchemy.url", url_do_ambiente())

    conectavel = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with conectavel.connect() as conexao:
        context.configure(connection=conexao)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    migrar_sem_conexao()
else:
    migrar_com_conexao()
