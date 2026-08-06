from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from starkbank_trial.config import Settings
from starkbank_trial.persistence.schema import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = config.get_main_option("sqlalchemy.url") or Settings().database_url
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
