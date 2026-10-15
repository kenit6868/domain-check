"""Owned provider submission previews, intent and deduplication ledger."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_provider_submissions"
down_revision = "0003_explicit_role_permissions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "provider_submissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("check_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("business_day", sa.Date(), nullable=False),
        sa.Column("url_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("result_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id", "check_id"], ["checks.owner_user_id", "checks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id", "payload_artifact_id"], ["artifacts.owner_user_id", "artifacts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("owner_user_id", "provider", "business_day", "url_hash"),
        sa.UniqueConstraint("owner_user_id", "id"),
    )


def downgrade():
    op.drop_table("provider_submissions")
