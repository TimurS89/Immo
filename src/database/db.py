"""Database connection and session management."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.database.models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None
_lock = threading.Lock()


def get_engine(db_path: str = "./data/immo.db") -> Engine:
    """Get or create the SQLAlchemy engine."""
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                path = Path(db_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                _engine = create_engine(
                    f"sqlite:///{path}",
                    echo=False,
                    connect_args={"check_same_thread": False},
                )
                # Enable WAL mode and foreign keys for SQLite
                @event.listens_for(_engine, "connect")
                def set_sqlite_pragma(dbapi_conn, _):
                    cursor = dbapi_conn.cursor()
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA foreign_keys=ON")
                    cursor.close()
    return _engine


@contextmanager
def get_session(db_path: str = "./data/immo.db") -> Generator[Session, None, None]:
    """Create a new database session as a context manager."""
    global _SessionLocal
    with _lock:
        if _SessionLocal is None:
            engine = get_engine(db_path)
            _SessionLocal = sessionmaker(bind=engine)
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db(db_path: str = "./data/immo.db") -> None:
    """Create all tables."""
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
