"""Anonymous security events, deduplicated session audit and stable pagination."""
from alembic import op
import sqlalchemy as sa

revision = '0005_audit_log'
down_revision = '0004_provider_submissions'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('audit_events', 'owner_user_id', nullable=True)
    op.alter_column('audit_events', 'actor_user_id', nullable=True)
    op.add_column('audit_events', sa.Column('actor_type', sa.String(16), nullable=False, server_default='user'))
    op.add_column('audit_events', sa.Column('event_key', sa.String(200), nullable=True))
    op.create_unique_constraint('uq_audit_events_event_key', 'audit_events', ['event_key'])
    op.create_index('ix_audit_occurred_id', 'audit_events', ['occurred_at', 'id'])
    op.create_check_constraint('audit_actor_scope', 'audit_events', "(actor_type = 'user' AND owner_user_id IS NOT NULL AND actor_user_id IS NOT NULL) OR (actor_type = 'anonymous' AND owner_user_id IS NULL AND actor_user_id IS NULL)")


def downgrade():
    # Never silently delete anonymous security history on downgrade.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM audit_events WHERE owner_user_id IS NULL OR actor_user_id IS NULL)")).scalar():
        raise RuntimeError('Archive anonymous audit events explicitly before downgrading')
    op.drop_constraint('audit_actor_scope', 'audit_events', type_='check')
    op.drop_index('ix_audit_occurred_id', table_name='audit_events')
    op.drop_constraint('uq_audit_events_event_key', 'audit_events', type_='unique')
    op.drop_column('audit_events', 'event_key')
    op.drop_column('audit_events', 'actor_type')
    op.alter_column('audit_events', 'actor_user_id', nullable=False)
    op.alter_column('audit_events', 'owner_user_id', nullable=False)
