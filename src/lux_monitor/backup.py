"""Database backup / restore — protect the accumulated history.

The SQLite file at ``data/monitor.db`` holds everything we've collected: every
listing, its full price history, and the daily market snapshots that build the
long-run trend. Re-scraping can't recover the *history* (athome only shows the
current snapshot of the market), so the DB is precious — losing it resets the
"days on market" clock and the trend series to zero.

This module makes backups cheap and automatic:
- ``backup_db()``  — copy the live DB to ``data/backups/monitor-<ts>.db`` (uses
  SQLite's online-backup API, so it's safe even mid-write), pruning to the most
  recent ``keep`` copies.
- ``latest_backup()`` / ``restore_db()`` — find and restore the newest backup.

The cron wrapper calls ``backup`` before each run, so there's always a recent
restore point.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.lux_monitor.db import DEFAULT_DB_PATH

logger = logging.getLogger(__name__)

BACKUP_DIR = DEFAULT_DB_PATH.parent / "backups"
KEEP_DEFAULT = 14  # ~2 weeks of daily backups


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup_db(
    db_path: Path | None = None, *, backup_dir: Path | None = None, keep: int = KEEP_DEFAULT
) -> Path | None:
    """Snapshot the live DB to a timestamped file; prune to ``keep`` newest.

    Returns the backup path, or ``None`` if the source DB doesn't exist yet.
    Uses the SQLite online-backup API so it's consistent even if a write is in
    flight.
    """
    src = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not src.exists():
        logger.info("backup_db: no DB at %s yet — nothing to back up", src)
        return None

    out_dir = Path(backup_dir) if backup_dir else BACKUP_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    # Guarantee a unique filename even for two backups in the same second (e.g.
    # restore_db snapshots the live DB right before overwriting it).
    dest = out_dir / f"monitor-{_ts()}.db"
    suffix = 1
    while dest.exists():
        dest = out_dir / f"monitor-{_ts()}-{suffix}.db"
        suffix += 1

    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)  # atomic, consistent online backup
        finally:
            target.close()
    finally:
        source.close()

    _prune(out_dir, keep)
    logger.info("backup_db: wrote %s", dest)
    return dest


def _prune(out_dir: Path, keep: int) -> None:
    backups = sorted(out_dir.glob("monitor-*.db"))
    for old in backups[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
        logger.debug("backup_db: pruned old backup %s", old)


def latest_backup(backup_dir: Path | None = None) -> Path | None:
    out_dir = Path(backup_dir) if backup_dir else BACKUP_DIR
    backups = sorted(out_dir.glob("monitor-*.db"))
    return backups[-1] if backups else None


def restore_db(
    backup: Path | None = None, *, db_path: Path | None = None, backup_dir: Path | None = None
) -> Path:
    """Restore a backup over the live DB (the current DB is itself backed up first).

    With no ``backup`` argument, restores the most recent one. Raises if there's
    nothing to restore.
    """
    dest = Path(db_path) if db_path else DEFAULT_DB_PATH
    src = Path(backup) if backup else latest_backup(backup_dir)
    if src is None or not src.exists():
        raise FileNotFoundError("No backup found to restore from.")

    # Safety: snapshot whatever is currently live before overwriting it.
    if dest.exists():
        backup_db(dest, backup_dir=backup_dir)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    logger.info("restore_db: restored %s -> %s", src, dest)
    return dest
