"""Alembic environment for the Luxembourg property monitor.

Targets the whole project schema: the canonical ``src/lux_monitor`` model plus
the enum-decoupled legacy ``src/database`` model (kept for the disabled DE/FR
cross-border option). The DB URL comes from the ``LUX_MONITOR_DB_URL`` env var
when set, otherwise from ``alembic.ini`` (default ``data/monitor.db``).
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the project root importable (so `src.lux_monitor` resolves).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lux_monitor.models import Base as LuxBase  # noqa: E402
from src.database.models import Base as LegacyBase  # noqa: E402

# One migration system for the whole project. The canonical LU schema plus the
# (now enum-decoupled) legacy DE/FR schema, all in monitor.db.
ALL_METADATA = [LuxBase.metadata, LegacyBase.metadata]

config = context.config

# Allow env-var override of the DB URL (tests, alternate locations).
_env_url = os.environ.get("LUX_MONITOR_DB_URL")
if _env_url:
    config.set_main_option("sqlalchemy.url", _env_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = ALL_METADATA


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DBAPI connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite-friendly ALTERs for future migrations
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (with a live connection)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
