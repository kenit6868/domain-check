"""Exact, owner scoped email previews for completed normal Domain Worker checks."""
from __future__ import annotations

import importlib
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from domaincheck.application.previews import PreviewConflict, PreviewNotFound, PreviewService
from domaincheck.application.signature_logo import load_profile_logo, logo_response
from domaincheck.core.email_content import html_report_body
from domaincheck.core.worker_precheck import email_examples, valid_recipients
from domaincheck.domain.previews import PreparedDelivery, canonical_payload_bytes
from domaincheck.infrastructure.cache import business_day
from domaincheck.infrastructure.db.models import (
    Artifact, Check, EvidenceSet, Job, JobItem, PreviewPlan, SenderAccount, UserSettings,
)
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.ports.artifacts import ImageUpload

POLICY_VERSION = "domain-worker-normal-email-v1"
PROFILE_FIELDS = ("brand_name", "contact_name", "contact_email", "company_name",
                  "business_registration_no", "signature_role")


class DomainWorkerPreviewService:
    policy_version = POLICY_VERSION
    feature = 'domain_worker'

    def evidence_block(self, check, manifest):
        return importlib.import_module('browser_evidence').format_email_evidence_manifest(manifest)
    def __init__(self, session, store, *, owner, actor, timezone, request_id, now):
        self.session, self.store, self.owner = session, store, owner
        self.timezone, self.now = timezone, now
        self.previews = PreviewService(session, store, owner_user_id=owner,
            actor_user_id=actor, request_id=request_id)

    async def _check(self, check_id):
        check = await OwnedRepository(self.session, Check, self.owner).get(check_id, for_update=True)
        if check is None or check.workflow != "domain_worker":
            raise PreviewNotFound("Domain Worker check not found")
        cloak = check.result.get("cloaking") or {}
        normal = cloak.get("verdict") == "BLOCKED_OR_UNAVAILABLE" or (
            cloak.get("verdict") == "NO_SIGNAL" and not cloak.get("manual_review_required")
            and not (cloak.get("coverage") or {}).get("multi_vantage_recommended"))
        if (check.status != "succeeded" or check.result.get("disposition") != "ready"
                or check.business_day != business_day(self.now, self.timezone)
                or not valid_recipients(check.result.get("recipients") or [])
                or not normal
                or check.result.get("browser_verification_pending")):
            raise PreviewConflict("A current-day normal precheck with valid Browser Evidence is required")
        completed = await self.session.scalar(select(JobItem.id).join(Job,
            (Job.id == JobItem.job_id) & (Job.owner_user_id == JobItem.owner_user_id)).where(
            JobItem.owner_user_id == self.owner, Job.job_type == "domain_worker_precheck",
            JobItem.result_metadata["check_id"].astext == str(check.id), JobItem.state == "succeeded"))
        if completed is None:
            raise PreviewConflict("Precheck item must complete before creating a preview")
        return check

    async def _evidence(self, check):
        try:
            evidence_id = UUID(check.result["evidence_set_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PreviewConflict("Browser Evidence is missing") from exc
        evidence = await OwnedRepository(self.session, EvidenceSet, self.owner).get(evidence_id, for_update=True)
        if (evidence is None or evidence.check_id != check.id or evidence.case_id is not None
                or evidence.requested_url != check.requested_url or evidence.manifest.get("terminal")
                or evidence.capture_mode not in {"browser_capture", "manual_upload"}
                or evidence.manifest.get("source") != evidence.capture_mode
                or evidence.manifest.get("version") != 1):
            raise PreviewConflict("Browser Evidence does not match this URL")
        images = await OwnedRepository(self.session, Artifact, self.owner).list(criteria=(
            Artifact.evidence_set_id == evidence.id, Artifact.kind == "domain_worker:evidence"))
        shots = evidence.manifest.get("screenshots")
        if not isinstance(shots, list) or not 1 <= len(shots) <= 3 or len(images) != len(shots):
            raise PreviewConflict("Browser Evidence requires 1–3 images and a matching manifest")
        by_id = {str(image.id): image for image in images}
        ordered, seen = [], set()
        for shot in shots:
            image = by_id.get(shot.get("artifact_id")) if isinstance(shot, dict) else None
            if (image is None or image.id in seen or not image.immutable
                    or image.mime_type not in {"image/png", "image/jpeg"}
                    or not 0 < image.byte_size <= 10 * 1024 * 1024
                    or image.sha256 != shot.get("sha256") or image.byte_size != shot.get("byte_size")):
                raise PreviewConflict("Browser Evidence image bindings are invalid")
            self.store.read_verified(storage_key=image.storage_key, expected_size=image.byte_size,
                                     expected_sha256=image.sha256)
            ordered.append(image)
            seen.add(image.id)
        if evidence.manifest.get("evidence_type") == "dom_destination_opened" and (
                len(ordered) != 2 or not evidence.manifest.get("destination_opened")
                or evidence.manifest.get("navigation_verified")):
            raise PreviewConflict("DOM destination evidence is incomplete")
        if evidence.manifest.get('evidence_type') == 'dom_destination_opened':
            browser = importlib.import_module('browser_evidence')
            control = evidence.manifest.get('control') or {}
            navigation = evidence.manifest.get('navigation') or {}
            if (not control.get('found') or not control.get('label')
                    or not browser._safe_http_destination(control.get('resolved_destination', ''))
                    or not browser._safe_http_destination(evidence.manifest.get('final_url', ''))
                    or not browser._safe_http_destination(navigation.get('source_url', ''))):
                raise PreviewConflict('DOM evidence metadata is incomplete; run precheck again to capture the full observation')
        return evidence, ordered

    async def options(self, check_id):
        check = await self._check(check_id)
        await self._evidence(check)
        accounts = await OwnedRepository(self.session, SenderAccount, self.owner).list(
            criteria=(SenderAccount.enabled.is_(True),),
            order_by=(SenderAccount.is_default.desc(), SenderAccount.created_at, SenderAccount.id), limit=500)
        return {"check_id": check.id, "accounts": [{"id": account.id, "label": account.label,
            "sender_email": account.smtp_username, "is_default": account.is_default}
            for account in accounts], "recipients": valid_recipients(check.result.get("recipients") or [])}

    async def create(self, check_id, account_ids, *, lifetime=timedelta(minutes=30), review_scope=None):
        if not 1 <= len(account_ids) <= 100 or len(set(account_ids)) != len(account_ids):
            raise ValueError("Choose between 1 and 100 distinct sender accounts")
        check = await self._check(check_id)
        scope = await self.session.scalar(select(Job.account_scope).join(JobItem,
            (JobItem.job_id == Job.id) & (JobItem.owner_user_id == Job.owner_user_id)).where(
            Job.owner_user_id == self.owner, Job.job_type == 'domain_worker_precheck',
            JobItem.result_metadata['check_id'].astext == str(check.id), JobItem.state == 'succeeded'))
        chosen = {str(account_id) for account_id in account_ids}
        allowed = set(review_scope) if review_scope is not None else set(scope or [])
        if not allowed or (not chosen.issubset(allowed) if review_scope is not None else allowed != chosen):
            raise PreviewConflict('Use the sender accounts selected before this precheck')
        evidence, images = await self._evidence(check)
        # Lock the versions before rendering so content and version bindings agree.
        accounts = list(await self.session.scalars(select(SenderAccount).where(
            SenderAccount.owner_user_id == self.owner, SenderAccount.id.in_(account_ids))
            .order_by(SenderAccount.id).with_for_update().execution_options(populate_existing=True)))
        if len(accounts) != len(account_ids) or any(not account.enabled for account in accounts):
            raise PreviewConflict("One or more selected sender accounts are unavailable")
        by_id = {account.id: account for account in accounts}
        profile = await self.session.scalar(select(UserSettings).where(UserSettings.owner_user_id == self.owner)
            .with_for_update().execution_options(populate_existing=True))
        from domaincheck.application.profile_readiness import missing_report_profile
        if missing_report_profile(profile):
            raise PreviewConflict("Complete your organization profile before creating a preview")
        config = {field: getattr(profile, field) for field in PROFILE_FIELDS}
        examples = email_examples(check.domain, check.requested_url, check.result, config)
        expected = valid_recipients(check.result.get("recipients") or [])
        if (not examples or len(examples) != len(expected)
                or len(examples) * len(account_ids) > 100):
            raise PreviewConflict("The complete recipient plan cannot be rendered (maximum 100 deliveries)")
        logo = await load_profile_logo(self.session, self.store, self.owner, profile)
        created = []
        try:
            logo_id = profile.logo_artifact_id
            if logo_id is None:
                stored = self.store.store_evidence_images(owner_user_id=self.owner, evidence_set_id=uuid4(),
                    uploads=[ImageUpload("signature-logo.jpg", logo.mime_type, logo.data)],
                    minimum_count=1, maximum_count=1)[0]
                created.append(stored)
                logo_id = stored.artifact_id
                self.session.add(Artifact(id=logo_id, owner_user_id=self.owner, kind="mail_settings:logo",
                    mime_type=stored.mime_type, byte_size=stored.byte_size, sha256=stored.sha256,
                    storage_key=stored.storage_key, immutable=True))
            manifest = self.store.store_evidence_manifest(owner_user_id=self.owner, evidence_set_id=evidence.id,
                data=canonical_payload_bytes(evidence.manifest))
            created.append(manifest)
            self.session.add(Artifact(id=manifest.artifact_id, owner_user_id=self.owner,
                evidence_set_id=evidence.id, kind="domain_worker:manifest", mime_type=manifest.mime_type,
                byte_size=manifest.byte_size, sha256=manifest.sha256, storage_key=manifest.storage_key, immutable=True))
            await self.session.flush()
            deliveries = prepare_deliveries(check, examples, config,
                [by_id[key] for key in account_ids], evidence.manifest, f"logo-{logo_id}",
                evidence_block=self.evidence_block(check, evidence.manifest))
            exact = await self.previews.create_revision(resource_type="check", resource_id=check.id,
                feature=self.feature, policy_version=self.policy_version, deliveries=deliveries,
                resource_version=check.version, attachment_artifact_ids=[image.id for image in images] + [manifest.artifact_id],
                inline_artifact_ids=[logo_id], now=self.now, lifetime=lifetime)
            return await self._response(exact, check)
        except Exception:
            for stored in created:
                self.store.delete(storage_key=stored.storage_key)
            raise

    async def get(self, check_id, preview_id):
        check = await self._check(check_id)
        await self._evidence(check)
        plan = await OwnedRepository(self.session, PreviewPlan, self.owner).get(preview_id)
        if plan is None or plan.resource_type != "check" or plan.resource_id != check.id:
            raise PreviewNotFound("Domain Worker preview not found")
        exact = await self.previews.get_exact(preview_id, now=self.now)
        if exact.payload.get("policy_version") != self.policy_version:
            raise PreviewNotFound("Domain Worker preview not found")
        return await self._response(exact, check)

    async def _response(self, exact, check):
        plan = await OwnedRepository(self.session, PreviewPlan, self.owner).get(exact.preview_id)
        logo_binding = next(binding for binding in exact.payload["artifacts"] if binding["disposition"] == "inline")
        artifact = await OwnedRepository(self.session, Artifact, self.owner).get(UUID(logo_binding["artifact_id"]))
        from domaincheck.core.email_content import InlineLogo
        logo = InlineLogo(self.store.read_verified(storage_key=artifact.storage_key,
            expected_size=artifact.byte_size, expected_sha256=artifact.sha256), artifact.mime_type, logo_binding["content_id"])
        return {"id": exact.preview_id, "check_id": check.id, "revision": exact.revision,
            "fingerprint": exact.fingerprint, "expires_at": plan.expires_at,
            "requested_url": check.requested_url, "deliveries": exact.payload["deliveries"],
            "artifacts": exact.payload["artifacts"], "signature_logo": logo_response(logo)}


def prepare_deliveries(check, examples, config, accounts, manifest, logo_cid, *, evidence_block=None):
    """Render the shared legacy personalization, then escape the exact HTML body."""
    toolkit = importlib.import_module("phishing_toolkit")
    browser = importlib.import_module('browser_evidence')
    block = browser.format_email_evidence_manifest(manifest) if evidence_block is None else evidence_block
    result = []
    for account in accounts:
        signature = {key: str((account.signature_overrides or {}).get(key) or "") for key in
                     ("contact_name", "contact_email", "signature_role")}
        signature["username"] = account.smtp_username
        for example in examples:
            body = toolkit._insert_before_email_signature(example["body"], block)
            body = toolkit.personalize_email_body(body, config, signature)
            if toolkit.validate_report_delivery({"to": example["recipient"], "subject": example["subject"],
                                                 "body": body}, target_url=check.requested_url):
                raise PreviewConflict("The report body failed the external email quality gate")
            result.append(PreparedDelivery(sender_account_id=account.id,
                recipient=example["recipient"], channel=example["channel"],
                draft_identity=f"domain-worker-{example['channel']}-v1", subject=example["subject"],
                text_body=body, html_body=html_report_body(body, logo_cid), sender_email=account.smtp_username,
                sender_name=signature["contact_name"] or config["contact_name"], sender_label=account.label))
    return result
