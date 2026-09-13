---
name: protect-database
description: >-
  MUST-FOLLOW rules for the Luxembourg property monitor's SQLite database at
  data/monitor.db. Use this skill whenever a task involves the database,
  re-scraping, "starting fresh", resetting, migrations, ADDING A COLUMN, or
  anything that could delete or overwrite data/monitor.db. The DB holds
  irreplaceable history (price history + daily market snapshots) that re-scraping
  CANNOT recover.
---

# Protect the database — `data/monitor.db` is precious

The SQLite file `data/monitor.db` is the single source of accumulated value in
this project. It holds, per listing, the **full price history** and the
**daily `market_snapshots_lu` series** that powers the trend charts and the
**days-on-market** signal. **Re-scraping cannot recover any of this** — athome
only exposes the *current* snapshot of the market, so a wipe permanently resets:

- every listing's **days-on-market** clock to 0,
- the **price-drop** detection (needs ≥2 observations of a listing),
- the **market trend** series (medians over time) to a single point.

## 🚫 Never do (without explicit, reconfirmed user intent)

- **Never** `rm data/monitor.db`, `rm -f data/monitor.db`, or delete/move it.
- **Never** suggest `rm data/monitor.db && alembic upgrade head` as a routine
  "refresh". A normal `python -m src.lux_monitor run` already updates in place
  and prunes non-matching rows — it does **not** need a wipe.
- **Never** call `Base.metadata.drop_all`, `init-db` over an existing DB, or any
  destructive migration on the live file.

A schema change is an **Alembic migration** (`alembic revision` + `upgrade`),
never a delete. But "it's a migration" is not by itself a guarantee — see below.

## Extending the schema — additive only

**Adding a field must never rewrite the `listings` table.** On SQLite,
`op.batch_alter_table(...)` works by *creating a new table, copying every row
across, and dropping the original*. That is a full rebuild of the file's most
valuable table, and it is one interrupted run, one type mismatch or one bad
`server_default` away from losing price history — while looking, in review, like
an ordinary migration.

✅ **Do** — one nullable column at a time, native `ALTER TABLE … ADD COLUMN`:

```python
op.add_column("listings", sa.Column("land_m2", sa.Float(), nullable=True))
```

🚫 **Don't** — in the `upgrade()` path of a migration against `listings`:

- `op.batch_alter_table(...)` (rebuild-by-copy),
- `NOT NULL` on a new column, or a `server_default` backfilling every row,
- any `DROP COLUMN` / `DROP TABLE` / `drop_all`.

Existing rows read `NULL` for a new column, which is the **truthful** value:
"we don't know yet". They fill in on their own — `save_listings` only writes
non-`None` values (`UPDATABLE_FIELDS` in `src/scrapers/luxembourg/base.py`), so
the next run that re-sees an advert **backfills it in place**. No reset, no
re-import. A new column must be added to `UPDATABLE_FIELDS`, or it stays NULL
forever on every row that already exists.

`alembic/versions/c4f7a2b6e9d1_add_property_type_and_amenity_fields.py` is the
worked example (19 columns, guarded against already-present ones).

**Prove it, don't assert it.** `tests/test_migration_additive.py` builds a
database at the pre-change revision, seeds listings + price history + snapshots,
runs `alembic upgrade head`, and asserts every row, every old value and every
**rowid** is unchanged (a rebuild reassigns rowids, so that pin is what catches
one). Extend `BASELINE_REVISION`/`ADDED_COLUMNS` when you add the next migration
— a destructive one should fail in the test suite, not on the operator's box.

## ✅ Always do

- **Before anything that touches the DB destructively, back it up first:**
  ```bash
  python -m src.lux_monitor backup       # -> data/backups/monitor-<ts>.db (keeps 14)
  ```
- The cron wrapper (`scripts/run_lux.sh`) **already backs up before every run**,
  so the daily job is self-protecting.
- To recover from an accidental wipe or a bad run:
  ```bash
  python -m src.lux_monitor restore      # restores the newest backup (asks to confirm)
  python -m src.lux_monitor restore --file data/backups/monitor-<ts>.db
  ```
  `restore` snapshots the current DB first, so it's itself non-destructive.

## When the user says "start fresh" / "reset" / "re-scrape"

Do **not** assume they want the file deleted. Default to a normal in-place run
(`python -m src.lux_monitor run`), which refreshes listings and self-prunes.
Only if they *explicitly* confirm they want to discard all accumulated history:

1. `python -m src.lux_monitor backup` first (so it's reversible),
2. then, and only then, remove the file.

State plainly that wiping loses the days-on-market and trend history, and that a
plain `run` is almost always what they actually want.
