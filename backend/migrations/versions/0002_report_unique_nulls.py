"""reports: treat NULL owner as one value in uq_report_period (NULLS NOT DISTINCT)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-22
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_report_period", "reports", type_="unique")
    op.execute("ALTER TABLE reports ADD CONSTRAINT uq_report_period UNIQUE NULLS NOT DISTINCT (owner_id, period, period_start)")


def downgrade() -> None:
    op.drop_constraint("uq_report_period", "reports", type_="unique")
    op.create_unique_constraint("uq_report_period", "reports", ["owner_id", "period", "period_start"])
