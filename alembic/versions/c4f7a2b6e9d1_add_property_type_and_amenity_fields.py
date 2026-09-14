"""add property type, plot size and amenity fields to listings

Revision ID: c4f7a2b6e9d1
Revises: b8e2f1a9c3d5
Create Date: 2026-09-13

PURELY ADDITIVE — this migration must never lose a row.

Every statement is a plain ``ALTER TABLE listings ADD COLUMN <x>`` (natively
supported by SQLite) plus one ``CREATE INDEX``. There is deliberately **no**
``batch_alter_table`` in ``upgrade()``: batch mode works by creating a new
table, copying the rows across and dropping the original, which is exactly the
copy-and-swap we do not want anywhere near a database holding months of
irreplaceable price history. ``ADD COLUMN`` rewrites nothing — existing rows
keep their values and read ``NULL`` for the new columns until a re-scrape
enriches them (see ``UPDATABLE_FIELDS`` in ``src/scrapers/luxembourg/base.py``).

Columns are added one at a time and only when absent, so the migration is safe
against a database whose schema was bootstrapped by ``create_all()`` (the dev
fallback) and therefore already carries some of them.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4f7a2b6e9d1"
down_revision = "b8e2f1a9c3d5"
branch_labels = None
depends_on = None


# (name, type). All nullable, no server default: an existing row simply reads
# NULL = "we don't know yet", which is a truthful answer, not a fabricated one.
NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    # --- what kind of property ---
    ("property_type", sa.String(length=32)),      # house | apartment | other
    ("property_subtype", sa.String(length=64)),   # detached_house | penthouse | …
    ("bathrooms_count", sa.Integer()),
    # --- sizes ---
    ("land_m2", sa.Float()),                      # plot/lot size (houses)
    ("terrace_m2", sa.Float()),
    ("balcony_m2", sa.Float()),
    ("livingroom_m2", sa.Float()),
    ("is_new_build", sa.Boolean()),
    # --- comfort / equipment (tri-state: True / False / NULL = unknown) ---
    ("has_air_conditioning", sa.Boolean()),
    ("has_solar_panels", sa.Boolean()),
    ("has_heat_pump", sa.Boolean()),
    ("has_pool", sa.Boolean()),
    ("has_attic", sa.Boolean()),
    ("has_basement", sa.Boolean()),
    ("has_wine_cellar", sa.Boolean()),
    ("has_heating", sa.Boolean()),
    ("heating_type", sa.String(length=64)),
    ("kitchen_type", sa.String(length=64)),
    ("accepts_pets", sa.Boolean()),
)

INDEX_NAME = "ix_listings_property_type"


def _offline() -> bool:
    """True under ``alembic upgrade --sql``: there is no connection to inspect.

    Reporting nothing as present then emits every statement, which is what a
    generated SQL script should contain.
    """
    return bool(op.get_context().as_sql)


def _existing_columns() -> set[str]:
    if _offline():
        return set()
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns("listings")}


def _existing_indexes() -> set[str]:
    if _offline():
        return set()
    return {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes("listings")}


def upgrade() -> None:
    present = _existing_columns()
    for name, type_ in NEW_COLUMNS:
        if name in present:
            continue
        op.add_column("listings", sa.Column(name, type_, nullable=True))

    if INDEX_NAME not in _existing_indexes():
        op.create_index(INDEX_NAME, "listings", ["property_type"], unique=False)


def downgrade() -> None:
    # SQLite cannot DROP COLUMN before 3.35, so the downgrade path does need the
    # table-recreate that upgrade() avoids. It is destructive of the new columns
    # by definition; nothing calls it automatically.
    if INDEX_NAME in _existing_indexes():
        op.drop_index(INDEX_NAME, table_name="listings")

    present = _existing_columns()
    with op.batch_alter_table("listings", schema=None) as batch_op:
        for name, _ in reversed(NEW_COLUMNS):
            if name in present:
                batch_op.drop_column(name)
