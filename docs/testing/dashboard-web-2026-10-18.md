# Permission-scoped Dashboard

Implemented system and personal aggregates at GET `/api/v1/dashboard`, backed by
local PostgreSQL records only. No migration or new dependency.

## Authorization and definitions

- `dashboard.view` required; system scope additionally requires
  `dashboard.view_all`. No role-name-based authorization. Personal feature
  metrics are omitted without the corresponding view permission. Scope/feature
  changes use new FE cache keys including the entire permission set.
- Cross-owner summaries disclose counts, user display names/status and bounded
  worker summaries, never target URLs, recipient addresses, message content,
  evidence or artifact references. They do not grant private workflow access.
- Successful checks count distinct requested URLs within saved business dates.
  Daily unique URLs need not sum to range unique URLs. Cloudflare counts only
  SUBMITTED, not ALREADY_SUBMITTED or UNKNOWN. Neither is takedown confirmation.
- Successful SMTP deliveries count once at their first successful checkpoint,
  in viewer timezone, regardless of failed/repeated attempts. Failed/UNKNOWN
  cards are existing backlog across all dates. Shared review/worker deliveries
  use preview artifact and owned case provenance for feature filtering.
- Review queues use today's business date and valid saved check sources.
  Provider replies use saved needs_reply/sent-match/ledger state and show the
  last recorded sync. Dashboard never reads IMAP or reconciles workflow state.
- User summary pagination is 50 rows; worker lists cap at 20 each. Read queries
  are aggregated, with no per-user/job-item query loops. Polling is 30 seconds
  in visible tabs; a manual refresh is available.

## Validation

- Full isolated PostgreSQL backend: 316 tests, 314 passed / 2 skipped.
  Six focused Dashboard regressions passed after the explicit rollout option
  was added, including preservation of later operator permission choices.
- Frontend full: 137/137; focused Dashboard/header/session tests: 8/8 after final
  responsive adjustments. Build/typecheck/lint, compileall, pip check and diff
  whitespace checks passed.
- Playwright fixtures: desktop 1440x1000 and mobile 390x844, no JS errors or
  page-wide horizontal overflow. Screenshots inspected; chart bars visible,
  metric grid responsive, wide tables scroll locally and header actions wrap.
- Read-only runtime smoke used a transaction marked READ ONLY, 5s SQL statement
  timeout and 15s aggregate deadline: system result successful in 82ms.
- Legacy 420 tests retain the known 4 failures / 7 errors: macOS Chrome/Windows
  process mocks and existing worker/lead-triage/provider fixtures. No new legacy
  production paths were changed.

## Local rollout

Ran backend `python -m domaincheck.cli sync-rbac --grant-dashboard`: 2 roles,
54 permissions, 101 bindings. This explicit option adds dashboard.view to user
and dashboard.view/view_all to admin, without resetting other grants or revoking
sessions. Ordinary startup/sync does not repeat the grants. Custom roles remain
administrator-configured. No live SMTP/IMAP/provider submission or suspicious
URL probe was performed; runtime mail/job/report histories were not changed.
