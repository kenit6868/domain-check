"""Make role permissions explicit and administrator-owned.

Revision ID: 0003_explicit_role_permissions
Revises: 0002_sender_default_unique
Create Date: 2026-10-15
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_explicit_role_permissions"
down_revision: str | None = "0002_sender_default_unique"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


LEGACY_SAFE_USER_CODES = (
    "quick_report.view", "quick_report.check", "quick_report.open_form",
    "quick_report.export", "quick_report.clear_cache",
    "check_domain.view", "check_domain.check", "check_domain.upload",
    "check_domain.preview",
    "cloudflare_worker.view", "cloudflare_worker.precheck",
    "cloudflare_worker.clear_cache",
    "domain_worker.view", "domain_worker.precheck", "domain_worker.clear_cache",
    "cloaking_review.view", "cloaking_review.upload", "cloaking_review.preview",
    "domain_evidence_review.view", "domain_evidence_review.upload",
    "domain_evidence_review.preview",
    "link_status.view", "link_status.check", "link_status.export",
    "mail_accounts.view_own", "mail_accounts.manage_own",
    "provider_replies.view", "provider_replies.preview",
    "lead_triage.view", "lead_triage.check", "lead_triage.preview",
)


def upgrade() -> None:
    connection = op.get_bind()
    # Old releases rewrote the user role to a built-in safe set.  Clearing it
    # once makes the administrator's permission selection the only authority.
    connection.execute(sa.text("""
        DELETE FROM role_permissions
        WHERE role_id IN (SELECT id FROM roles WHERE key = 'user')
    """))
    # Existing bootstrap administrators become full application admins.  A
    # subsequent `domaincheck-admin sync-rbac` narrows this to the code-owned
    # catalog if a deployment has obsolete permission rows.
    connection.execute(sa.text("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT roles.id, permissions.id
        FROM roles CROSS JOIN permissions
        WHERE roles.key = 'admin'
        ON CONFLICT DO NOTHING
    """))
    connection.execute(sa.text("""
        DELETE FROM user_roles AS user_assignment
        USING roles AS user_role
        WHERE user_assignment.role_id = user_role.id
          AND user_role.key = 'user'
          AND EXISTS (
              SELECT 1
              FROM user_roles AS admin_assignment
              JOIN roles AS admin_role ON admin_role.id = admin_assignment.role_id
              WHERE admin_assignment.user_id = user_assignment.user_id
                AND admin_role.key = 'admin'
          )
    """))


def downgrade() -> None:
    connection = op.get_bind()
    user_role_id = connection.execute(
        sa.text("SELECT id FROM roles WHERE key = 'user'")
    ).scalar_one_or_none()
    if user_role_id is None:
        return
    permission_ids = connection.execute(
        sa.text("SELECT id FROM permissions WHERE code = ANY(:codes)"),
        {"codes": list(LEGACY_SAFE_USER_CODES)},
    ).scalars()
    for permission_id in permission_ids:
        connection.execute(
            sa.text("""
                INSERT INTO role_permissions (role_id, permission_id)
                VALUES (:role_id, :permission_id)
                ON CONFLICT DO NOTHING
            """),
            {"role_id": user_role_id, "permission_id": permission_id},
        )
    connection.execute(sa.text("""
        INSERT INTO user_roles (user_id, role_id, assigned_by_user_id)
        SELECT admin_assignment.user_id, :user_role_id, admin_assignment.user_id
        FROM user_roles AS admin_assignment
        JOIN roles AS admin_role ON admin_role.id = admin_assignment.role_id
        WHERE admin_role.key = 'admin'
        ON CONFLICT DO NOTHING
    """), {"user_role_id": user_role_id})
