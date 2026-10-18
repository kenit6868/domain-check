# Audit Log web — 2026-10-18

## Delivered behavior

One permission-controlled menu `/audit-log` with three tabs: Đăng nhập & phiên,
Hoạt động, Quản trị & cấu hình. Local-account date range defaults to seven days,
maximum 367 days inclusive. Filters: feature, exact action/outcome/Request ID;
global scope additionally searches actor/owner display name or UUID. SQL search
escapes wildcard input. Stable descending occurred_at/UUID cursor, 50 rows/page
(API maximum 100), detail lookup with the same owner/permission rules. No edit,
delete or export route/control. No automatic retention deletion or fake backfill.

`audit_logs.view` gates menu/direct route/API/service reads. `view_all` additionally
permits global scope and anonymous security events. User defaults include only
view; existing admin/user/custom grants remain unchanged. Catalog and runtime DB
now contain 52 permissions. New grants must be selected in Role & quyền; seed does
not silently change existing roles. Sidebar remains scrollable on short desktops.

## Migration and privacy

`0005_audit_log` preserves all existing audit rows, makes owner/actor nullable for
explicit anonymous events only, adds actor_type/check constraint, nullable unique
event_key and timestamp/UUID pagination index. Migration applied to local runtime;
fresh isolated databases migrate from zero in tests. Downgrade fails if anonymous
records remain, rather than deleting history. No config.ini access/modification.

New recorder and read projection use bounded metadata keys/types. No request body,
query/target URL, email/evidence bytes, Cookie/token/OIDC code/state, password, raw
provider exception, IP or User-Agent is collected by this implementation. Existing
audit data remains stored; only allowlisted fields are exposed by new APIs. User
names are current directory names; stable actor/owner IDs remain in the response.
Private no-store responses and fixed server-side scope apply to list/detail.

## Event coverage and semantics

- Verified login success (including pending-user session), logout: same transaction
  as session change. Login failure has a fixed reason code; a disabled verified
  account is attributed to its user, otherwise the event is anonymous.
- Rejected callbacks and observed expired/revoked sessions use an independent
  recorder so the request's rejected transaction cannot erase the security event.
  Session terminal key dedupes repeated requests/logout/admin revocation. Expiry
  is observed on subsequent access, not a clock-driven expiry scan.
- Admin session revocation records each affected session in the admin transaction;
  role changes record safe permission deltas/role IDs; account updates record
  field names, never changed secret values.
- Existing preview/approval/form/Cloudflare/provider-reply/job events remain.
  Authenticated HTTP mutation commands add accepted/denied/failed request events,
  including unhandled 500 failures. Ordinary GET/polling is skipped; denied
  feature reads can emit a security event. No request payload is inspected.
- Job completion and stop events share their version/lease-guarded transition.
  SMTP intent/outcome/expired-claim UNKNOWN events share the delivery ledger
  transaction and attempt ID. Existing dedupe, Message-ID and retry rules remain.
- Check Link Status adds count-only started/completed/interrupted observations;
  actual URL lists and results remain request-local and are never stored by audit.
- Existing Cloudflare checkpoint and Netcraft uppercase outcomes normalize for
  filters/display; historical rows are not rewritten.

An HTTP `accepted` event only confirms a successful request response. SMTP `sent`
means server acceptance; provider outcome events retain their own meaning. Audit
is a trace alongside durable job/delivery/provider ledgers, not an independent
reason to retry a message or infer recipient delivery/takedown.

Optional independent command/security recorder has a two-second deadline and a
fixed `audit_event_persistence_unavailable` warning on failure. It must not change
an accepted operation into a resend opportunity. During a DB outage these optional
events can be missing; monitor the warning. Critical ledger/transition events are
transactional. Application exposes append-only access; this is not a separate
tamper-proof/WORM storage system or a guarantee of recording before every crash.

## Validation

- Focused audit backend: 9/9 on fresh isolated PostgreSQL. Covers owner/global/
  anonymous access, read-only routes, exact detail scope, redaction, cursor/name/
  wildcard/date/outcome filters, failed callback durability, disabled-account
  denial without a session, expiry dedupe, logout, atomic rollback/idempotency,
  skipped polling and redacted HTTP command/500 capture.
- Full backend: 310 tests, 308 passed / 2 skipped (isolated Redis and fresh wheel),
  133.144s on a newly created/migrated PostgreSQL DB; temporary DB removed afterwards.
- Frontend: 129/129 across 19 files, including three Audit Log workflow tests and
  no-permission direct navigation to all 12 feature pages. Focused audit tests
  also rerun after final lint/detail markup correction.
- Build/typecheck/lint, compileall, backend pip check and whitespace checks passed.
- Playwright fixture QA at 1440px/390px: three tabs, detail opening, no JavaScript
  error or whole-page horizontal overflow; images visually inspected. Table has
  its own horizontal scrolling on mobile.
- Legacy 420 retains the known four failures / seven errors. No live login,
  SMTP/IMAP, provider submission or suspicious-URL operation was performed.

Local migration/seed applied, API health 200. Roles were not automatically granted
the two new permissions. Browser QA used isolated fixture contexts, not personal
browser profiles or real user data. No runtime mail/report/job history was changed.
