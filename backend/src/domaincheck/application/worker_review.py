"""Current-day owned review queues populated by Domain Worker prechecks."""
from uuid import uuid4

from sqlalchemy import select

from domaincheck.infrastructure.cache import business_day
from domaincheck.infrastructure.db.models import Artifact, AuditEvent, CaseSource, Check, EvidenceSet, ReviewCase
from domaincheck.infrastructure.db.owned import OwnedRepository
from domaincheck.core.worker_precheck import valid_recipients


async def review_checks(session, owner, case):
    return list(await session.scalars(select(Check).join(CaseSource,
        (CaseSource.check_id == Check.id) & (CaseSource.owner_user_id == Check.owner_user_id)).where(
        CaseSource.owner_user_id == owner, CaseSource.case_id == case.id,
        Check.workflow == 'domain_worker', Check.status == 'succeeded')
        .order_by(Check.created_at.desc(), Check.id.desc())))


async def list_reviews(session, owner, timezone, now, kind):
    cases = await OwnedRepository(session, ReviewCase, owner).list(criteria=(
        ReviewCase.case_type == kind, ReviewCase.business_day == business_day(now, timezone),
        ReviewCase.state.in_(['pending', 'failed', 'partial'])), order_by=(ReviewCase.created_at.desc(),), limit=500)
    result = []
    for case in cases:
        checks = await review_checks(session, owner, case)
        expected = 'cloaking_review' if kind == 'cloaking' else 'evidence_review'
        check = next((row for row in checks if row.result.get('disposition') == expected
                      and valid_recipients(row.result.get('recipients') or [])), None)
        if check is None:
            continue
        result.append({'id': case.id, 'check_id': check.id, 'requested_url': case.full_url,
            'state': case.state, 'sender_account_ids': case.required_account_ids,
            'recipients': valid_recipients(check.result.get('recipients') or []),
            'cloaking_verdict': (check.result.get('cloaking') or {}).get('verdict'),
            'evidence_reason': check.result.get('evidence_reason')})
    return result


async def upload_review_images(session, store, owner, timezone, now, case_id, uploads):
    case = await OwnedRepository(session, ReviewCase, owner).get(case_id, for_update=True)
    if case is None or case.case_type != 'domain_evidence':
        raise LookupError('Evidence review case not found')
    if case.business_day != business_day(now, timezone) or case.state not in {'pending', 'failed', 'partial'}:
        raise ValueError('A pending current-day evidence review is required')
    if not case.required_account_ids:
        raise ValueError('Run a new precheck with sender accounts selected before adding evidence')
    checks = [row for row in await review_checks(session, owner, case)
        if row.business_day == case.business_day and row.result.get('disposition') == 'evidence_review'
        and valid_recipients(row.result.get('recipients') or [])]
    if not checks:
        raise ValueError('No normal report with valid recipients remains in this case')
    created = []
    try:
        for check in checks:
            evidence_id = uuid4()
            images = store.store_evidence_images(owner_user_id=owner, evidence_set_id=evidence_id,
                uploads=uploads, minimum_count=1, maximum_count=3)
            created.extend(images)
            manifest = {'version': 1, 'source': 'manual_upload', 'evidence_type': 'manual_upload',
                'requested_url': check.requested_url, 'terminal': False, 'navigation_verified': False,
                'screenshots': [{'artifact_id': str(image.artifact_id), 'sha256': image.sha256,
                                'byte_size': image.byte_size} for image in images]}
            session.add(EvidenceSet(id=evidence_id, owner_user_id=owner, check_id=check.id,
                requested_url=check.requested_url, capture_mode='manual_upload', manifest=manifest))
            await session.flush()
            for image in images:
                session.add(Artifact(id=image.artifact_id, owner_user_id=owner, evidence_set_id=evidence_id,
                    kind='domain_worker:evidence', mime_type=image.mime_type, byte_size=image.byte_size,
                    sha256=image.sha256, storage_key=image.storage_key, immutable=True))
            check.result = {**check.result, 'disposition': 'ready', 'evidence_set_id': str(evidence_id),
                            'evidence_reason': None, 'evidence_failure_code': None}
            check.version += 1
        case.state = 'ready'
        case.evidence_version += 1
        case.version += 1
        session.add(AuditEvent(owner_user_id=owner, actor_user_id=owner,
            action='domain_evidence_review.upload', resource_type='review_case', resource_id=case.id,
            outcome='success', metadata_allowlist={'image_count': len(uploads), 'check_count': len(checks)},
            request_id='evidence-review-upload'))
        await session.flush()
        return {'case_id': case.id, 'state': 'ready', 'check_ids': [check.id for check in checks]}
    except Exception:
        for image in created:
            store.delete(storage_key=image.storage_key)
        raise
