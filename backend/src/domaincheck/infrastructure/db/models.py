from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from domaincheck.infrastructure.db.base import Base, IdMixin, TimestampMixin, VersionMixin


def uuid_col(*, nullable: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(Uuid(as_uuid=True), nullable=nullable)


class User(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "users"
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="Asia/Bangkok")
    authz_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthIdentity(Base, IdMixin, TimestampMixin):
    __tablename__ = "auth_identities"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    __table_args__ = (UniqueConstraint("issuer", "subject"),)


class Session(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "sessions"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    csrf_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    authz_version: Mapped[int] = mapped_column(Integer, nullable=False)


class Role(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "roles"
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Permission(Base, IdMixin, TimestampMixin):
    __tablename__ = "permissions"
    code: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    assigned_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSettings(Base, TimestampMixin, VersionMixin):
    __tablename__ = "user_settings"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    brand_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    contact_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    contact_email: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    company_name: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    business_registration_no: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    signature_role: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    logo_artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    encrypted_integrations: Mapped[bytes | None] = mapped_column(LargeBinary)
    integrations_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_user_id", "logo_artifact_id"],
            ["artifacts.owner_user_id", "artifacts.id"],
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_user_settings_logo_artifact",
        ),
    )


class SenderAccount(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "sender_accounts"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_identity: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    smtp_host: Mapped[str] = mapped_column(String(500), nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False)
    smtp_username: Mapped[str] = mapped_column(String(500), nullable=False)
    smtp_transport: Mapped[str] = mapped_column(String(24), nullable=False)
    imap_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    proxy_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    signature_overrides: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    encrypted_secret: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int | None] = mapped_column(Integer)
    secret_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    __table_args__ = (
        UniqueConstraint("owner_user_id", "normalized_identity"),
        UniqueConstraint("owner_user_id", "id"),
        Index(
            "uq_sender_accounts_owner_default",
            "owner_user_id",
            unique=True,
            postgresql_where=is_default.is_(True),
        ),
    )


class Target(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "targets"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    full_url_identity: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hostname: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    identity_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("owner_user_id", "identity_version", "canonical_url_hash"),
        UniqueConstraint("owner_user_id", "id"),
    )


class Check(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "checks"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    target_id: Mapped[uuid.UUID] = uuid_col()
    requested_url: Mapped[str] = mapped_column(Text, nullable=False)
    workflow: Mapped[str] = mapped_column(String(80), nullable=False)
    workflow_key: Mapped[str] = mapped_column(String(128), nullable=False)
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    business_day: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cache_provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (
        ForeignKeyConstraint(["owner_user_id", "target_id"], ["targets.owner_user_id", "targets.id"], ondelete="RESTRICT"),
        UniqueConstraint("owner_user_id", "id"),
        Index("ix_checks_owner_workflow_day", "owner_user_id", "workflow", "business_day"),
    )


class Job(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "jobs"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    stage: Mapped[str] = mapped_column(String(80), nullable=False, default="pending")
    business_day: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    account_scope: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    stop_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lease_token: Mapped[str | None] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (
        UniqueConstraint("owner_user_id", "id"),
        Index('ix_jobs_queue_claim', 'job_type', 'state', 'heartbeat_at', 'created_at', 'id'),
        Index('ix_jobs_owner_active', 'owner_user_id', 'job_type', 'state', 'lease_expires_at'),
        Index('ix_jobs_owner_service', 'owner_user_id', 'job_type', 'heartbeat_at'),
    )


class JobItem(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "job_items"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    job_id: Mapped[uuid.UUID] = uuid_col()
    item_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    input_index: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    stage: Mapped[str] = mapped_column(String(80), nullable=False, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (
        ForeignKeyConstraint(["owner_user_id", "job_id"], ["jobs.owner_user_id", "jobs.id"], ondelete="CASCADE"),
        UniqueConstraint("job_id", "item_identity"),
        UniqueConstraint("owner_user_id", "id"),
    )


class ReviewCase(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "review_cases"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    case_type: Mapped[str] = mapped_column(String(80), nullable=False)
    canonical_url_key: Mapped[str] = mapped_column(String(128), nullable=False)
    full_url: Mapped[str] = mapped_column(Text, nullable=False)
    business_day: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    disposition: Mapped[str | None] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_account_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    __table_args__ = (
        UniqueConstraint("owner_user_id", "case_type", "business_day", "canonical_url_key"),
        UniqueConstraint("owner_user_id", "id"),
    )


class CaseSource(Base, IdMixin, TimestampMixin):
    __tablename__ = "case_sources"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    case_id: Mapped[uuid.UUID] = uuid_col()
    check_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    job_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (
        ForeignKeyConstraint(["owner_user_id", "case_id"], ["review_cases.owner_user_id", "review_cases.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["owner_user_id", "check_id"], ["checks.owner_user_id", "checks.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["owner_user_id", "job_item_id"], ["job_items.owner_user_id", "job_items.id"], ondelete="RESTRICT"),
        UniqueConstraint("owner_user_id", "case_id", "check_id", "job_item_id"),
    )


class PreviewPlan(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "preview_plans"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[uuid.UUID] = uuid_col()
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    account_versions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    recipients: Mapped[list] = mapped_column(JSONB, nullable=False)
    draft_artifact_id: Mapped[uuid.UUID] = uuid_col()
    attachment_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(["owner_user_id", "draft_artifact_id"], ["artifacts.owner_user_id", "artifacts.id"], ondelete="RESTRICT"),
        UniqueConstraint("owner_user_id", "id"),
        UniqueConstraint("owner_user_id", "resource_type", "resource_id", "revision"),
    )


class Delivery(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "deliveries"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    preview_id: Mapped[uuid.UUID] = uuid_col()
    sender_account_id: Mapped[uuid.UUID] = uuid_col()
    recipient: Mapped[str] = mapped_column(String(1000), nullable=False)
    workflow: Mapped[str] = mapped_column(String(80), nullable=False)
    business_key: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(String(80), nullable=False)
    draft_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(1000))
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    claim_token: Mapped[str | None] = mapped_column(String(100))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(["owner_user_id", "preview_id"], ["preview_plans.owner_user_id", "preview_plans.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["owner_user_id", "sender_account_id"], ["sender_accounts.owner_user_id", "sender_accounts.id"], ondelete="RESTRICT"),
        UniqueConstraint("owner_user_id", "sender_account_id", "business_key"),
        UniqueConstraint("owner_user_id", "id"),
    )


class DeliveryAttempt(Base, IdMixin, TimestampMixin):
    __tablename__ = "delivery_attempts"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    delivery_id: Mapped[uuid.UUID] = uuid_col()
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    smtp_stage: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sanitized_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (
        ForeignKeyConstraint(["owner_user_id", "delivery_id"], ["deliveries.owner_user_id", "deliveries.id"], ondelete="CASCADE"),
        UniqueConstraint("delivery_id", "attempt_number"),
    )


class EvidenceSet(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "evidence_sets"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    check_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    requested_url: Mapped[str] = mapped_column(Text, nullable=False)
    landing_url: Mapped[str | None] = mapped_column(Text)
    destination_url: Mapped[str | None] = mapped_column(Text)
    capture_mode: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["owner_user_id", "check_id"], ["checks.owner_user_id", "checks.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["owner_user_id", "case_id"], ["review_cases.owner_user_id", "review_cases.id"], ondelete="RESTRICT"),
        UniqueConstraint("owner_user_id", "id"),
    )


class Artifact(Base, IdMixin, TimestampMixin):
    __tablename__ = "artifacts"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    evidence_set_id: Mapped[uuid.UUID | None] = uuid_col(nullable=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    original_name: Mapped[str | None] = mapped_column(String(500))
    immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    __table_args__ = (
        ForeignKeyConstraint(["owner_user_id", "evidence_set_id"], ["evidence_sets.owner_user_id", "evidence_sets.id"], ondelete="RESTRICT"),
        UniqueConstraint("owner_user_id", "id"),
    )


class CloudflareRecord(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "cloudflare_records"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    job_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    full_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url_key: Mapped[str] = mapped_column(String(128), nullable=False)
    business_day: Mapped[date] = mapped_column(Date, nullable=False)
    report_version: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_report_id: Mapped[str | None] = mapped_column(String(500))
    sanitized_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (
        ForeignKeyConstraint(["owner_user_id", "job_item_id"], ["job_items.owner_user_id", "job_items.id"], ondelete="RESTRICT"),
        UniqueConstraint("owner_user_id", "business_day", "canonical_url_key", "report_version"),
        UniqueConstraint("owner_user_id", "idempotency_key"),
    )


class ProviderSubmission(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "provider_submissions"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    check_id: Mapped[uuid.UUID] = uuid_col()
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    business_day: Mapped[date] = mapped_column(Date, nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_artifact_id: Mapped[uuid.UUID] = uuid_col()
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (
        ForeignKeyConstraint(["owner_user_id", "check_id"], ["checks.owner_user_id", "checks.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["owner_user_id", "payload_artifact_id"], ["artifacts.owner_user_id", "artifacts.id"], ondelete="RESTRICT"),
        UniqueConstraint("owner_user_id", "provider", "business_day", "url_hash"),
        UniqueConstraint("owner_user_id", "id"),
    )


class ReviewSnapshot(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "review_snapshots"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    feature: Mapped[str] = mapped_column(String(80), nullable=False)
    context_key: Mapped[str] = mapped_column(String(128), nullable=False)
    business_day: Mapped[date | None] = mapped_column(Date)
    sanitized_state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    __table_args__ = (UniqueConstraint("owner_user_id", "feature", "context_key"),)


class AuditEvent(Base, IdMixin):
    __tablename__ = "audit_events"
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False, default='user', server_default='user')
    event_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    action: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    metadata_allowlist: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    __table_args__ = (Index("ix_audit_owner_occurred", "owner_user_id", "occurred_at"),
        Index('ix_audit_occurred_id', 'occurred_at', 'id'),
        CheckConstraint("(actor_type = 'user' AND owner_user_id IS NOT NULL AND actor_user_id IS NOT NULL) OR (actor_type = 'anonymous' AND owner_user_id IS NULL AND actor_user_id IS NULL)", name='audit_actor_scope'))


class MigrationRun(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "migration_runs"
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    migration_version: Mapped[str] = mapped_column(String(100), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_root_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    counts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    errors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    checkpoint: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (UniqueConstraint("migration_version", "source_fingerprint"),)


FOUNDATION_TABLES = frozenset(Base.metadata.tables)

