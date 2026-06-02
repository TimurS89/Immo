"""add market_snapshots_lu (daily market aggregates)

Revision ID: b8e2f1a9c3d5
Revises: f3b9c1d4e7a2
Create Date: 2026-06-02

One row per (date, listing_type, commune) capturing counts and price/m² medians,
to build the long-run rent-vs-buy trend.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8e2f1a9c3d5"
down_revision = "f3b9c1d4e7a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_snapshots_lu",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.DateTime(), nullable=False),
        sa.Column("listing_type", sa.String(length=16), nullable=False),
        sa.Column("commune", sa.String(length=128), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("median_price_eur", sa.Float(), nullable=True),
        sa.Column("mean_price_eur", sa.Float(), nullable=True),
        sa.Column("median_price_per_m2_eur", sa.Float(), nullable=True),
        sa.Column("median_surface_m2", sa.Float(), nullable=True),
        sa.Column("new_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_date", "listing_type", "commune", name="uq_snapshot_segment"),
    )
    with op.batch_alter_table("market_snapshots_lu", schema=None) as batch_op:
        batch_op.create_index("ix_market_snapshots_lu_snapshot_date", ["snapshot_date"], unique=False)
        batch_op.create_index("ix_snapshot_type_commune", ["listing_type", "commune"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("market_snapshots_lu", schema=None) as batch_op:
        batch_op.drop_index("ix_snapshot_type_commune")
        batch_op.drop_index("ix_market_snapshots_lu_snapshot_date")
    op.drop_table("market_snapshots_lu")
