from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PermissionDefinition:
    code: str
    description: str
    safe_for_default_user: bool = False


def _feature(feature: str, actions: dict[str, tuple[str, bool]]) -> tuple[PermissionDefinition, ...]:
    return tuple(
        PermissionDefinition(f"{feature}.{action}", description, safe)
        for action, (description, safe) in actions.items()
    )


PERMISSION_CATALOG = (
    *_feature('dashboard', {
        'view': ('View personal dashboard statistics', True),
        'view_all': ('View system dashboard aggregates across users', False),
    }),
    *_feature('audit_logs', {
        'view': ('View own audit log', True),
        'view_all': ('View audit log across all users, including anonymous security events', False),
    }),
    *_feature("quick_report", {
        "view": ("View Quick Report", True), "check": ("Run Quick Report checks", True),
        "open_form": ("Create fill-only provider form tasks", True),
        "submit_netcraft": ("Submit Netcraft reports", False), "export": ("Export Quick Report data", True),
        "clear_cache": ("Clear own Quick Report cache", True),
    }),
    *_feature("check_domain", {
        "view": ("View Check Domain", True), "check": ("Run full domain checks", True),
        "submit_vt": ("Submit URLs to VirusTotal", False), "capture": ("Capture browser evidence", False),
        "upload": ("Upload domain evidence", True),
    }),
    *_feature("cloudflare_worker", {
        "view": ("View Cloudflare Worker", True), "precheck": ("Precheck Cloudflare targets", True),
        "start": ("Start Cloudflare submissions", False), "resume": ("Resume Cloudflare submissions", False),
        "stop": ("Stop own Cloudflare jobs", False), "retry": ("Retry failed Cloudflare records", False),
        "clear_cache": ("Clear own Cloudflare editable cache", True),
    }),
    *_feature("domain_worker", {
        "view": ("View Domain Worker", True), "precheck": ("Run Domain Worker precheck", True),
        "preview": ("Create exact Domain Worker email previews", True),
        "start": ("Start Domain Worker sending", False), "stop": ("Stop own Domain Worker job", False),
        "retry": ("Retry missing Domain Worker deliveries", False),
        "clear_cache": ("Clear own Domain Worker cache", True),
    }),
    *_feature("cloaking_review", {
        "view": ("View own cloaking cases", True), "upload": ("Upload cloaking evidence", True),
        "preview": ("Create cloaking report previews", True), "approve": ("Approve a cloaking disposition", False),
        "send": ("Send approved cloaking reports", False), "skip": ("Skip own cloaking cases", False),
    }),
    *_feature("domain_evidence_review", {
        "view": ("View own evidence cases", True), "upload": ("Upload normal browser evidence", True),
        "preview": ("Create evidence report previews", True), "send": ("Send evidence review reports", False),
    }),
    *_feature("link_status", {
        "view": ("View Link Status", True), "check": ("Run link checks", True),
    }),
    *_feature("mail_accounts", {
        "view_own": ("View own mail accounts", True), "manage_own": ("Manage own mail accounts", True),
        "test_own": ("Test own SMTP or IMAP connection", False),
    }),
    *_feature("users", {
        "view": ("View application users", False), "manage": ("Activate or disable users", False),
        "assign_roles": ("Assign permitted roles to users", False),
    }),
    *_feature("roles", {
        "view": ("View roles and permissions", False), "manage": ("Manage roles and permissions", False),
    }),
    *_feature("provider_replies", {
        "view": ("View own provider replies", True), "sync": ("Read own configured mailboxes", False),
        "mark_seen": ("Mark own mailbox messages seen", False), "preview": ("Preview provider replies", True),
        "send": ("Send provider replies", False),
    }),
)

# Explicit retirement list: prune these inert legacy entries and their bindings,
# never arbitrary unknown/custom rows or codes introduced by another release.
RETIRED_PERMISSION_CODES = frozenset({
    'check_domain.preview', 'check_domain.send', 'link_status.export',
    'lead_triage.view', 'lead_triage.check', 'lead_triage.preview', 'lead_triage.send',
})

PERMISSIONS_BY_CODE = {item.code: item for item in PERMISSION_CATALOG}
if len(PERMISSIONS_BY_CODE) != len(PERMISSION_CATALOG):
    raise RuntimeError("Permission catalog contains duplicate codes")

# Initial role seed; administrators may subsequently customize the DB bindings.
DEFAULT_USER_PERMISSIONS = frozenset(
    item.code for item in PERMISSION_CATALOG
    if not item.code.startswith(("users.", "roles.")) and item.code not in {'audit_logs.view_all', 'dashboard.view_all'}
)


def validate_permission_codes(codes: set[str] | frozenset[str]) -> None:
    unknown = sorted(set(codes) - PERMISSIONS_BY_CODE.keys())
    if unknown:
        raise ValueError("Unknown permission codes: " + ", ".join(unknown))

