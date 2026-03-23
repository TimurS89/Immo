"""Database connection and session management."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.database.models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine(db_path: str = "./data/immo.db") -> Engine:
    """Get or create the SQLAlchemy engine."""
    global _engine
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


def get_session(db_path: str = "./data/immo.db") -> Session:
    """Create a new database session."""
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_engine(db_path)
        _SessionLocal = sessionmaker(bind=engine)
    return _SessionLocal()


def init_db(db_path: str = "./data/immo.db") -> None:
    """Create all tables."""
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
