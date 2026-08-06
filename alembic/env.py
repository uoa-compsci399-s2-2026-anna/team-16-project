"""Alembic environment.

One chain for the whole project (contract §2.4). Every model module must be
imported here — autogenerate only sees what is registered on Base.metadata,
and a table nobody imported is silently treated as "dropped".
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import admin.models  # noqa: F401  - registers staff, staff_recovery_code, audit_log
import admin.taxonomy_models  # noqa: F401  - registers the six taxonomy tables
from admin.config import load_settings
from db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The URL comes from the same settings the application uses, so there is one
# place it is configured. Doubling '%' is required: Alembic reads this value
# back through configparser, which treats a single '%' as interpolation — and
# a URL-encoded password is exactly where that bites.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", load_settings().database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
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
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
