from __future__ import annotations

import base64
import hashlib
from uuid import uuid4

from sqlalchemy import func, select
from domaincheck.core.email_content import InlineLogo, bundled_logo_bytes, default_logo
from domaincheck.infrastructure.db.models import Artifact, AuditEvent, UserSettings
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.ports.artifacts import ArtifactIntegrityError, ImageUpload


async def load_profile_logo(session, store, owner, profile) -> InlineLogo | None:
    if profile is None or profile.logo_artifact_id is None:
        return default_logo()
    artifact = await OwnedRepository(session, Artifact, owner).get(profile.logo_artifact_id)
    if (artifact is None or artifact.kind != 'mail_settings:logo' or not artifact.immutable
            or artifact.evidence_set_id is not None or artifact.mime_type not in {'image/jpeg', 'image/png'}):
        raise ArtifactIntegrityError('Signature logo is unavailable')
    data = store.read_verified(storage_key=artifact.storage_key, expected_size=artifact.byte_size,
                               expected_sha256=artifact.sha256)
    return InlineLogo(data, artifact.mime_type, f'logo-{artifact.id}')


def logo_response(logo: InlineLogo | None):
    if logo is None:
        return None
    return {'data_url': f'data:{logo.mime_type};base64,{base64.b64encode(logo.data).decode("ascii")}',
        'mime_type': logo.mime_type, 'content_id': logo.content_id,
        'sha256': hashlib.sha256(logo.data).hexdigest(), 'byte_size': len(logo.data)}


async def import_bundled_logo(sessions, store, owner, request_id="signature-logo"):
    return await save_profile_logo(sessions, store, owner,
        ImageUpload('signature-logo.jpg', 'image/jpeg', bundled_logo_bytes()), request_id)


async def save_profile_logo(sessions, store, owner, upload, request_id="signature-logo"):
    data = upload.data
    stored = ()
    try:
        async with sessions() as session, session.begin():
            await session.execute(select(func.pg_advisory_xact_lock(
                int.from_bytes(hashlib.sha256(f'profile-logo:{owner}'.encode()).digest()[:8], 'big', signed=True))))
            profile = await session.scalar(select(UserSettings).where(UserSettings.owner_user_id == owner).with_for_update())
            if profile is None:
                profile = UserSettings(owner_user_id=owner)
                session.add(profile)
                await session.flush()
            try:
                current = await load_profile_logo(session, store, owner, profile)
            except (ArtifactIntegrityError, FileNotFoundError):
                current = None  # An explicit replacement can repair a missing/corrupt custom logo.
            if profile.logo_artifact_id is not None and current is not None and current.data == data:
                return profile.logo_artifact_id
            stored = store.store_evidence_images(owner_user_id=owner, evidence_set_id=uuid4(),
                uploads=[upload], minimum_count=1, maximum_count=1)
            item = stored[0]
            session.add(Artifact(id=item.artifact_id, owner_user_id=owner, evidence_set_id=None,
                kind='mail_settings:logo', mime_type=item.mime_type, byte_size=item.byte_size,
                sha256=item.sha256, storage_key=item.storage_key, immutable=True))
            await session.flush()
            profile.logo_artifact_id = item.artifact_id
            profile.version += 1
            session.add(AuditEvent(owner_user_id=owner, actor_user_id=owner, action='mail_profile.logo',
                resource_type='artifact', resource_id=item.artifact_id, outcome='success',
                metadata_allowlist={'source': 'profile_logo', 'sha256': item.sha256}, request_id=request_id[:128]))
            result = item.artifact_id
        return result
    except Exception:
        for item in stored:
            store.delete(storage_key=item.storage_key)
        raise


async def reset_profile_logo(sessions, owner, request_id='signature-logo'):
    async with sessions() as session, session.begin():
        profile = await session.scalar(select(UserSettings).where(UserSettings.owner_user_id == owner).with_for_update())
        if profile is not None and profile.logo_artifact_id is not None:
            profile.logo_artifact_id = None
            profile.version += 1
            session.add(AuditEvent(owner_user_id=owner, actor_user_id=owner, action='mail_profile.logo_reset',
                resource_type='profile', resource_id=owner, outcome='success', metadata_allowlist={}, request_id=request_id[:128]))
