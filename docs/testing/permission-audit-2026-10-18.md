# Web permission audit — 2026-10-18

Scope: React menus/direct URLs/action controls, FastAPI route dependencies,
artifact download boundary, catalog/seed behavior and read-only runtime catalog.
No live SMTP/IMAP, report submission, suspicious URL check or role assignment.

Follow-up requested by the operator: the seven unused permissions listed below
were removed from code/DB. Current catalog is 50, superseding the original 57-code
read-only observation. API now seeds before traffic after schema migration.

## Findings and corrections

- Runtime Permission table has all 57 registered codes and no unregistered codes.
  No sync, permission grant or role-data update was needed or performed.
- All 11 sidebar feature destinations gate their menu and direct page on their
  feature view permission. Dashboard is available to any active authenticated user;
  its shortcuts are individually filtered. Desktop/mobile share Navigation.
- 97 method/path pairs use registered action-permission dependencies. All reject
  both an empty permission set and a set containing every permission except the
  required action. Mutation guards require Origin/CSRF and reject missing headers.
  This is a route-boundary test with mocked sessions, not proof of every business
  combination or external-provider behavior.
- UsersPage fetched `/roles` and offered assignment with `users.assign_roles`
  even though that read API requires `roles.view`. It now requires both; direct
  role assignment remains governed by `users.assign_roles` and the backend's
  existing anti-escalation/last-admin rules.
- Check Domain's submit callback now also checks `check_domain.check`, covering
  form submit events in addition to the disabled input/button.
- Cloudflare Worker no longer offers a mail-settings link without
  `mail_accounts.view_own`; it retains explanatory text and Cookie fallback.
- Generic owned artifact download previously required only feature view even
  for `feature:draft`, bypassing the preview endpoints' intentional redaction.
  Draft downloads now additionally require `feature.preview`, or
  `quick_report.submit_netcraft` for Quick Report drafts. Evidence/manifest/logo
  rules remain feature-scoped, with ownership/hash validation unchanged.

## Feature matrix

| Menu | View | Active action permissions / additional conditions |
| --- | --- | --- |
| Check Domain | `check_domain.view` | `check`, optional `submit_vt`; API upload/capture use their own codes; report guidance is view-only |
| Quick Report | `quick_report.view` | `check`, `open_form`, `submit_netcraft`, `clear_cache`; XLSX API uses `export` (no current FE export control) |
| Domain Worker | `domain_worker.view` | `precheck`, `preview`, `start`, `stop`, `retry`, `clear_cache` (generic cache API); batch resume uses `start` |
| Cloaking Review | `cloaking_review.view` | `upload`, `preview`, `approve`, `send`, `skip`; capture additionally uses `check_domain.capture`; approve required for preview/send |
| Domain Evidence Review | `domain_evidence_review.view` | `upload`, `preview`, `send`; combined upload+preview checks both when files are supplied |
| Cloudflare Worker | `cloudflare_worker.view` | `precheck`, `start`, `resume`, `stop`, `retry`, `clear_cache`; pause/cancel share `stop` |
| Check Link Status | `link_status.view` (FE) | `link_status.check` (API + FE), no export feature |
| Phản hồi NCC | `provider_replies.view` | `sync`, `mark_seen`, `preview`, `send`; capture also requires `check_domain.capture` and `provider_replies.preview`; upload is part of preview |
| Cấu hình mail | `mail_accounts.view_own` | `manage_own` covers profile/accounts/logo/Cloudflare Account ID; `test_own` covers explicit connection tests |
| Người dùng | `users.view` | `manage`, `assign_roles`; FE role choices additionally require `roles.view` |
| Role & quyền | `roles.view` | `roles.manage`; server prevents privilege escalation/last-active-admin removal |

View and action codes are separate grants: menu/page visibility is driven by view;
API actions enforce their own action grant and workflow-specific extra conditions.
Having an action alone does not reveal its FE page. Roles should grant view together
with the intended actions. No new automatic role grant or reset was introduced.

## Removed catalog entries (operator-requested follow-up)

`check_domain.preview`, `check_domain.send`, all four `lead_triage.*` codes and
`link_status.export` have been removed from the catalog and DB, including bindings.
Check Domain has no preview/send endpoints; Lead Triage has no React page/API in
this scope; Link Status export was removed. Legacy Streamlit remains separate.
Likewise Quick Report export and Domain Worker cache clear have guarded APIs but
no corresponding current FE button. These distinctions must not be reported as
fully implemented web features simply because a permission code exists.

`PERMISSION_CATALOG` remains the source for first-deploy seed. API lifespan calls
sync_permission_catalog in a transaction before traffic; PostgreSQL advisory lock
serializes replicas and manual CLI. Fresh boot seeds roles/defaults, later boots
upsert newly registered permissions without granting them to existing roles.
Explicit retirement deletes only the seven inert codes, preserves active/custom
choices and unknown rows, updates affected role versions once. Sessions and worker
authz versions remain valid because retired codes had no executable API behavior.
Unsupported Check Domain/Lead Triage draft kinds now fail closed.

Regression checks cover rejected retired-code grants, idempotent cleanup across
admin/user/custom roles, preservation of unknown rows and active selections,
missing/new code materialization without auto-grants, startup-before-traffic,
startup failure cleanup and fresh migrated DB boot/restart. Runtime sync reports
two roles, 50 permissions and 95 active role bindings; no active role was reset.

Cleanup follow-up validation: fresh-migrated DB boot/restart smoke passed; full
backend 301 tests, 299 passed / 2 skipped (isolated Redis and fresh-wheel checks),
142.499s. Temporary DB dropped afterwards. Full legacy 420 retains baseline four
failures / seven errors. compileall, backend pip check and whitespace checks passed.
Runtime DB verified exactly 50 current codes, zero retired codes and no missing
registered code; API health 200. No frontend source changed in this follow-up;
the preceding 125/125 frontend result remains the applicable UI verification.

## Explicit nonstandard boundaries

- Public health/Telegram login endpoints and authenticated `/me`/logout use their
  own session/CSRF contracts; no feature permission is expected.
- `/cache/clear` resolves the permission from a server-owned feature mapping.
- `/artifacts/{id}` enforces owner, feature/purpose permission and byte integrity.
- Form-task exchange/status use extension-origin checks and expiring, device-bound
  capability/status tokens. Creation requires `quick_report.open_form`; these
  extension endpoints intentionally do not use browser-session CSRF authorization.
- Worker sending also retains current authorization/version validation; menu
  hiding is never the authority for external actions.

## Validation

- Frontend: 125/125 across 18 files, including 13 new direct-route/role-choice/
  form-submit permission regressions. No-view direct navigation to all 11 feature
  pages fetches no protected data and exposes only Dashboard navigation.
- Backend focused: 97 protected route combinations (empty and missing-action
  sets; mutation CSRF), plus seven feature draft-permission combinations.
- Full backend on a newly created, migrated isolated PostgreSQL DB: 297 tests,
  295 passed / 2 skipped, 146.978s. The temporary DB was dropped afterwards;
  production role/catalog/runtime records were not modified.
- Full legacy: 420 tests, existing baseline 4 failures / 7 errors remains.
- Frontend build/typecheck/lint, compileall and targeted whitespace checks passed.
  No new dependency or migration. Async debug timing notices and PowerShell's npm
  stderr notice formatting were observed; they did not indicate failed tests.
