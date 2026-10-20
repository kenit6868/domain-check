"""Separate Cloudflare daily cache resets without rewriting report history."""
from alembic import op
import sqlalchemy as sa

revision = '0007_cloudflare_cache_epoch'
down_revision = '0006_worker_capacity'
branch_labels = None
depends_on = None

OLD = 'uq_cloudflare_records_owner_user_id_business_day_canonical_url_key_report_version'


def upgrade():
    op.add_column('cloudflare_records', sa.Column('cache_epoch', sa.String(36), nullable=False, server_default=''))
    op.drop_constraint(op.f(OLD), 'cloudflare_records', type_='unique')
    op.create_unique_constraint('uq_cf_record_cache_epoch', 'cloudflare_records',
        ['owner_user_id', 'business_day', 'canonical_url_key', 'report_version', 'cache_epoch'])


def downgrade():
    # Refuse a lossy rollback if multiple cache epochs contain the same report.
    op.create_unique_constraint(op.f(OLD), 'cloudflare_records',
        ['owner_user_id', 'business_day', 'canonical_url_key', 'report_version'])
    op.drop_constraint('uq_cf_record_cache_epoch', 'cloudflare_records', type_='unique')
    op.drop_column('cloudflare_records', 'cache_epoch')
