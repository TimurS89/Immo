"""Engine / session helpers for the Luxembourg property monitor (SQLite).

Default database lives at ``data/monitor.db`` (relative to the project root).
Override with the ``LUX_MONITOR_DB_URL`` environment variable (used by Alembic
and convenient for tests).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

# src/lux_monitor/db.py -> parents[0]=lux_monitor, [1]=src, [2]=project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "monitor.db"


def database_url(db_path: str | Path | None = None) -> str:
    """Resolve the SQLAlchemy URL (env var wins, then explicit path, then default)."""
    env_url = os.environ.get("LUX_MONITOR_DB_URL")
    if env_url:
        return env_url
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    return f"sqlite:///{path}"


def get_engine(db_path: str | Path | None = None, *, url: str | None = None) -> Engine:
    """Create a SQLite engine with WAL + foreign-key pragmas enabled."""
    resolved = url or database_url(db_path)

    # Ensure the parent directory exists for file-based SQLite DBs.
    if resolved.startswith("sqlite:///") and ":memory:" not in resolved:
        file_path = resolved[len("sqlite:///") :]
        if file_path:
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        resolved, echo=False, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):  # pragma: no cover - driver callback
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory bound to ``engine`` (objects stay usable post-commit)."""
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine | None = None) -> Engine:
    """Create all tables (dev convenience; production uses Alembic migrations)."""
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Transactional session scope: commits on success, rolls back on error."""
    engine = engine or get_engine()
    session = make_session_factory(engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
