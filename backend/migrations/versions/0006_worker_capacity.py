"""Indexes for bounded, owner-fair multi-worker claims; no history rewritten."""
from alembic import op

revision = '0006_worker_capacity'
down_revision = '0005_audit_log'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_jobs_queue_claim', 'jobs', ['job_type', 'state', 'heartbeat_at', 'created_at', 'id'])
    op.create_index('ix_jobs_owner_active', 'jobs', ['owner_user_id', 'job_type', 'state', 'lease_expires_at'])
    op.create_index('ix_jobs_owner_service', 'jobs', ['owner_user_id', 'job_type', 'heartbeat_at'])


def downgrade():
    op.drop_index('ix_jobs_owner_service', table_name='jobs')
    op.drop_index('ix_jobs_owner_active', table_name='jobs')
    op.drop_index('ix_jobs_queue_claim', table_name='jobs')
