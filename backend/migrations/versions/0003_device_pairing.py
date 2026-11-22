"""device sessions + one-time pairing codes for phone access

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_sessions", sa.Column("device_name", sa.String(100), nullable=False, server_default=""))
    op.add_column("user_sessions", sa.Column("created_via", sa.String(20), nullable=False, server_default="password"))
    op.add_column("user_sessions", sa.Column("client_ip", sa.String(64), nullable=False, server_default=""))
    op.create_table(
        "pairing_codes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("used_session_id", sa.BigInteger(), sa.ForeignKey("user_sessions.id", ondelete="SET NULL")),
        sa.Column("created_ip", sa.String(64), nullable=False, server_default=""),
        sa.Column("used_ip", sa.String(64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("pairing_codes")
    op.drop_column("user_sessions", "client_ip")
    op.drop_column("user_sessions", "created_via")
    op.drop_column("user_sessions", "device_name")
