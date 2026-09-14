"""Proof that the schema migrations never destroy stored data.

The operator's standing constraint is *"we must not overwrite existing database,
but enhance it"* — the listings table holds months of price history that cannot
be re-scraped once lost. A migration that silently rebuilds the table (Alembic's
``batch_alter_table`` does exactly that) would be invisible in review and fatal
in production.

So this test does the real thing: build a database at the revision that existed
*before* the property-type extension, fill it with listings, price history and
market snapshots, run ``alembic upgrade head``, and assert every pre-existing
byte survived. It is generic on purpose — the last stanza walks every migration
from the recorded baseline forward, so a future destructive migration fails here
too, not on the operator's machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The head immediately before the property-type / amenity extension. Data written
# at this revision is what an operator's live database already contains.
BASELINE_REVISION = "b8e2f1a9c3d5"

# Columns the extension added. Existing rows must read NULL for these.
ADDED_COLUMNS = (
    "property_type",
    "property_subtype",
    "bathrooms_count",
    "land_m2",
    "terrace_m2",
    "balcony_m2",
    "livingroom_m2",
    "is_new_build",
    "has_air_conditioning",
    "has_solar_panels",
    "has_heat_pump",
    "has_pool",
    "has_attic",
    "has_basement",
    "has_wine_cellar",
    "has_heating",
    "heating_type",
    "kitchen_type",
    "accepts_pets",
)


def _alembic(db_path: Path, *args: str) -> None:
    env = {**os.environ, "LUX_MONITOR_DB_URL": f"sqlite:///{db_path}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
        )


def _seed_baseline_data(db_path: Path) -> None:
    """Insert rows using raw SQL — the ORM knows columns this revision lacks."""
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        for i in (1, 2, 3):
            conn.execute(
                text(
                    """
                    INSERT INTO listings (
                        id, portal, portal_listing_id, url, commune,
                        listing_type, bedrooms, surface_m2,
                        has_garage, parking_spaces, has_garden, has_balcony_terrace,
                        rent_eur, rent_total_eur, price_eur,
                        description_raw, description_lang, title, photos_urls,
                        first_seen_at, last_seen_at, is_active, price_history,
                        user_shortlist, score_total, energy_class
                    ) VALUES (
                        :id, 'athome', :ext, :url, 'Strassen',
                        'rent', 4, 140.0,
                        1, 1, 1, 1,
                        :rent, :rent, NULL,
                        'a description that is long enough', 'fr', :title, '[]',
                        '2026-01-01 10:00:00', '2026-09-01 10:00:00', 1,
                        '[{"date": "2026-01-01T10:00:00", "price": 3000, "charges": null}]',
                        0, 71.5, 'B'
                    )
                    """
                ),
                {
                    "id": i,
                    "ext": f"seed-{i}",
                    "url": f"https://www.athome.lu/id-{i}.html",
                    "rent": 3000.0 + i,
                    "title": f"Seeded listing {i}",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO price_history_entries
                        (listing_id, recorded_at, price_eur, charges_eur)
                    VALUES (:lid, '2026-01-01 10:00:00', :price, 150.0)
                    """
                ),
                {"lid": i, "price": 3000.0 + i},
            )
        conn.execute(
            text(
                """
                INSERT INTO market_snapshots_lu
                    (snapshot_date, listing_type, commune, count,
                     median_price_eur, mean_price_eur, median_price_per_m2_eur,
                     median_surface_m2, new_count)
                VALUES ('2026-09-01 00:00:00', 'rent', 'Strassen', 3,
                        3002.0, 3002.0, 21.4, 140.0, 3)
                """
            )
        )
    engine.dispose()


def _fingerprint(db_path: Path) -> dict[str, list[tuple]]:
    """Every row of every pre-existing table, as comparable tuples."""
    engine = create_engine(f"sqlite:///{db_path}")
    snapshot: dict[str, list[tuple]] = {}
    with engine.connect() as conn:
        tables = sorted(inspect(engine).get_table_names())
        for table in tables:
            if table == "alembic_version":
                continue
            cols = [c["name"] for c in inspect(engine).get_columns(table)]
            order = "id" if "id" in cols else cols[0]
            rows = conn.execute(
                text(f'SELECT * FROM "{table}" ORDER BY "{order}"')  # noqa: S608
            ).mappings().all()
            snapshot[table] = [tuple(sorted(dict(r).items())) for r in rows]
    engine.dispose()
    return snapshot


@pytest.fixture()
def populated_baseline_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "monitor.db"
    _alembic(db_path, "upgrade", BASELINE_REVISION)
    _seed_baseline_data(db_path)
    return db_path


def test_upgrade_to_head_preserves_every_existing_row(populated_baseline_db: Path) -> None:
    before = _fingerprint(populated_baseline_db)
    assert len(before["listings"]) == 3
    assert len(before["price_history_entries"]) == 3
    assert len(before["market_snapshots_lu"]) == 1

    _alembic(populated_baseline_db, "upgrade", "head")

    after = _fingerprint(populated_baseline_db)

    # No table lost, no row lost.
    assert set(after) == set(before)
    for table, rows in before.items():
        assert len(after[table]) == len(rows), f"{table} lost or gained rows"

    # Every value that existed before still reads back identically. New columns
    # are additive, so compare on the old column set only.
    for table, rows in before.items():
        for old_row, new_row in zip(rows, after[table]):
            old = dict(old_row)
            new = dict(new_row)
            for key, value in old.items():
                assert new[key] == value, f"{table}.{key} changed: {value!r} -> {new[key]!r}"


def test_added_columns_exist_and_default_to_unknown(populated_baseline_db: Path) -> None:
    _alembic(populated_baseline_db, "upgrade", "head")

    engine = create_engine(f"sqlite:///{populated_baseline_db}")
    columns = {c["name"] for c in inspect(engine).get_columns("listings")}
    missing = set(ADDED_COLUMNS) - columns
    assert not missing, f"migration did not add: {sorted(missing)}"

    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT {', '.join(ADDED_COLUMNS)} FROM listings WHERE id = 1")
        ).mappings().one()
    engine.dispose()

    # NULL, not False/0: "the listing doesn't say" is the truthful value for a
    # row scraped before these fields were collected.
    assert all(value is None for value in row.values()), dict(row)


def test_upgrade_does_not_rebuild_the_listings_table(populated_baseline_db: Path) -> None:
    """A table rebuild reassigns SQLite rowids and drops the original table.

    Pinning the rowid identity is the cheapest available proof that the upgrade
    used ``ALTER TABLE ... ADD COLUMN`` rather than the copy-and-swap that
    ``batch_alter_table`` performs.
    """
    engine = create_engine(f"sqlite:///{populated_baseline_db}")
    with engine.connect() as conn:
        before = conn.execute(
            text("SELECT rowid, id, portal_listing_id FROM listings ORDER BY id")
        ).all()
    engine.dispose()

    _alembic(populated_baseline_db, "upgrade", "head")

    engine = create_engine(f"sqlite:///{populated_baseline_db}")
    with engine.connect() as conn:
        after = conn.execute(
            text("SELECT rowid, id, portal_listing_id FROM listings ORDER BY id")
        ).all()
        # No orphaned copy left behind by a half-finished rebuild.
        leftovers = [
            t for t in inspect(engine).get_table_names()
            if t.startswith("_alembic_tmp") or t.endswith("_old")
        ]
    engine.dispose()

    assert after == before
    assert not leftovers, f"migration left temporary tables behind: {leftovers}"
