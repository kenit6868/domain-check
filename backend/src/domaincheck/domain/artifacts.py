from __future__ import annotations


_FEATURE_VIEW_PERMISSIONS: dict[str, frozenset[str]] = {
    "quick_report": frozenset({"quick_report.view"}),
    "check_domain": frozenset({"check_domain.view"}),
    "domain_worker": frozenset({"domain_worker.view"}),
    "cloaking_review": frozenset({"cloaking_review.view"}),
    "domain_evidence_review": frozenset({"domain_evidence_review.view"}),
    "provider_replies": frozenset({"provider_replies.view"}),
    "mail_settings": frozenset({"mail_accounts.view_own"}),
}

_DRAFT_READ_PERMISSIONS = {
    'quick_report': 'quick_report.submit_netcraft',
    'domain_worker': 'domain_worker.preview',
    'cloaking_review': 'cloaking_review.preview',
    'domain_evidence_review': 'domain_evidence_review.preview',
    'provider_replies': 'provider_replies.preview',
}


def can_read_artifact(kind: str, permissions: frozenset[str]) -> bool:
    """Require the feature encoded by the server-generated artifact kind.

    Artifact creators must use ``feature:purpose`` kinds. Unknown and malformed
    values fail closed instead of granting access based only on ownership.
    """

    feature, separator, purpose = kind.partition(":")
    required = _FEATURE_VIEW_PERMISSIONS.get(feature)
    if not (separator and purpose and required and permissions.intersection(required)):
        return False
    if purpose == "draft":
        # Match preview endpoint redaction; generic download must not bypass it.
        return _DRAFT_READ_PERMISSIONS.get(feature) in permissions
    return True
