"""Tests for DB backup / restore."""

from __future__ import annotations

import sqlite3

from src.lux_monitor.backup import backup_db, latest_backup, restore_db


def _make_db(path, value: str) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
    conn.execute("DELETE FROM t")
    conn.execute("INSERT INTO t (v) VALUES (?)", (value,))
    conn.commit()
    conn.close()


def _read(path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT v FROM t").fetchone()[0]
    finally:
        conn.close()


def test_backup_creates_file(tmp_path):
    db = tmp_path / "monitor.db"
    bdir = tmp_path / "backups"
    _make_db(db, "original")

    dest = backup_db(db, backup_dir=bdir)
    assert dest is not None and dest.exists()
    assert _read(dest) == "original"


def test_backup_none_when_no_db(tmp_path):
    assert backup_db(tmp_path / "missing.db", backup_dir=tmp_path / "b") is None


def test_backup_prunes_to_keep(tmp_path):
    db = tmp_path / "monitor.db"
    bdir = tmp_path / "backups"
    # five backups in quick succession (unique names via same-second suffixing);
    # keep=2 must leave exactly the 2 newest.
    for i in range(5):
        _make_db(db, f"v{i}")
        backup_db(db, backup_dir=bdir, keep=2)
    remaining = sorted(bdir.glob("monitor-*.db"))
    assert len(remaining) == 2


def test_restore_brings_back_old_data(tmp_path):
    db = tmp_path / "monitor.db"
    bdir = tmp_path / "backups"
    _make_db(db, "precious")
    backup_db(db, backup_dir=bdir)

    # simulate accidental overwrite (the "rm + re-scrape" mistake)
    _make_db(db, "fresh-empty")
    assert _read(db) == "fresh-empty"

    restored = restore_db(latest_backup(bdir), db_path=db, backup_dir=bdir)
    assert _read(restored) == "precious"  # history recovered
