"""Enforce one default sender account per owner.

Revision ID: 0002_sender_default_unique
Revises: 0001_foundation
Create Date: 2026-10-15
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_sender_default_unique"
down_revision: str | None = "0001_foundation"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sender_accounts",
        sa.Column("secret_metadata", postgresql.JSONB(), server_default="{}", nullable=False),
    )
    op.create_index(
        "uq_sender_accounts_owner_default",
        "sender_accounts",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("is_default IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index("uq_sender_accounts_owner_default", table_name="sender_accounts")
    op.drop_column("sender_accounts", "secret_metadata")
