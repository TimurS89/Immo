"""drop unused walk-distance columns

Revision ID: f3b9c1d4e7a2
Revises: d2218c88bfbc
Create Date: 2026-05-30

The walk_to_school / walk_to_creche / walk_to_park columns are no longer
populated or scored — the commute estimate was simplified to drive + PT only.
Drop them.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3b9c1d4e7a2"
down_revision = "d2218c88bfbc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("listings", schema=None) as batch_op:
        batch_op.drop_column("walk_to_school_min")
        batch_op.drop_column("walk_to_creche_min")
        batch_op.drop_column("walk_to_park_min")


def downgrade() -> None:
    with op.batch_alter_table("listings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("walk_to_school_min", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("walk_to_creche_min", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("walk_to_park_min", sa.Integer(), nullable=True))
