"""Shared pytest fixtures for the lux_monitor test suite."""

from __future__ import annotations

import pytest

from src.lux_monitor.db import get_engine, init_db, make_session_factory


@pytest.fixture
def lux_session(tmp_path):
    """A Session bound to a throwaway file-based SQLite lux_monitor DB."""
    engine = get_engine(url=f"sqlite:///{tmp_path / 'lux.db'}")
    init_db(engine)
    sess = make_session_factory(engine)()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()
