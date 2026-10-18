---
name: phishing-takedown-tool
description: Develop or review domain checks, defensive takedown reporting, evidence, SMTP/IMAP, provider replies and owned web workers.
---

# Domain Check web workflows

Read AGENTS.md first. The supported product is backend FastAPI/workers and
frontend React. Streamlit/desktop/old CLI and JSON worker/review/mail caches
were retired by explicit user instruction. Do not restore old-only features.
Shared code belongs to `domaincheck.engine`, never root wrappers, bare imports,
backend/compat or source-path fallback. Engine imports cannot read config.ini,
create/migrate runtime folders or write global CSV/JSON state.

## Scope and safety

- Backend uses explicit settings and owner-scoped DB/private artifacts. Keep
  config.ini, .env, data, reports, logs, existing builds and Chrome profiles at
  their current locations unless explicitly asked otherwise. Never print secrets
  or bundle real configuration. Only config examples belong in documentation.
- No real provider/SMTP/IMAP/suspect-URL activity without explicit authorization.
  Tests use mocks, temporary artifacts and isolated test DB/cache services.
- Every network request has a timeout; trusted APIs verify TLS. No exploitation,
  credential collection, evasive interaction or active browser form submission.

## Permissions and durable state

- Preserve owner/day/account scope on queries, preview/artifact download and
  commands. FE guards never replace API authorization/CSRF. Read current grants;
  permission edits preserve valid sessions only, never revive revoked/expired
  sessions. Keep worker authz invalidation and user-status revocation behavior.
- Seed catalog under the existing PostgreSQL advisory lock. Register new codes
  without automatically granting old roles; retire only explicit obsolete codes,
  never broadly reset grants or delete unknown permissions.
- Persisted engine/policy identifiers remain stable across code relocation,
  including historical legacy-* strings that bind caches/previews/fingerprints.
- Audit uses own scope unless view_all is granted, including anonymous scope.
  Allowlist metadata; never body/token/Cookie/password/IP/UA. Distinguish accepted
  commands from actual job/SMTP/provider outcomes. Do not audit normal polling.

## Checking and evidence

- Link Status observations of registration, DNS and HTTP remain independent.
  Only explicit WHOIS/RDAP hold proves hold. 404/410, WAF, DNS failure and timeout
  do not prove domain takedown. Keep OS DNS/public/mixed-IP guards and per-redirect
  checks; bound DNS and probes. Request-local streamed results are not DB history;
  reload clears them, recheck probes fresh, no CSV export.
- Cloaking always probes HTTP profiles first. Playwright is passive verification,
  includes Googlebot Smartphone and preserves the approved representative pair.
  Terminal Cloudflare/DNS/browser pages are not cloaking; all-terminal results
  become BLOCKED_OR_UNAVAILABLE. Path probes do not add cloaking score.
- Normal Browser Evidence is separate from cloaking evidence. Capture only into
  explicit staging, validate images/manifest/hash, preserve source/destination
  facts and use passive/manual fallback. Manual normal evidence is 1–3 PNG/JPEG;
  confirmed cloaking requires a valid approved pair, manifest and file limits.
- Verified navigation remains opt-in for normal reports only: isolated allowed
  Register/Login anchor or non-submit data-href button, before/after images and
  navigation facts. Never type credentials, submit or download. Fail closed.
- External report text is English; UI Vietnamese labels must not leak into it.
  Original observed website titles/keywords can retain their language. URLScan
  remains removed, including legacy-string scrubbing at delivery boundaries.

## Domain Worker and review

- v4 precheck resolves contacts and cloaking in parallel by full URL. No-email
  cases are excluded before review; normal cases need valid Browser Evidence to
  become ready. Cloaking/evidence-review cases never enter automatic normal send.
- Account scope is chosen before precheck and retained in job/review sources.
  Workspace restores only today's work using backend account timezone; stale
  query/storage selection cannot restore yesterday automatically. Historical
  batches are explicit/read-only; preserve active locks and all prior records.
- Ordinary batch send is DB-owned and checkpointed. Current ready results show
  per-account/recipient delivery, not a sent inference from one successful email.
  Batch delay uses durable next_run_at; reload does not enqueue or resend.
- Review uploads/preview/send operate on exact selected owned records, do not
  create worker jobs or widen account/day scope. Keep locks/version/hash bindings
  and immutable profile/logo/MIME/evidence. not_cloaking removes cloaking evidence
  and requires a separate normal report preview. Confirmed evidence remains truth
  even if later detector probes differ.

## Delivery and provider replies

- Use shared application dispatch/ledger and core mail transports. 465/ssl is
  implicit TLS, other ports default STARTTLS unless explicitly disabled. Keep
  allowlisted endpoints, timeout and send_message semantics. Retry once only for
  transient pre-DATA connection errors with the same Message-ID; never retry auth,
  sender or recipient failures. A disconnect after DATA is UNKNOWN, not failed.
- Commit intent before delivery, checkpoint each result immediately. Retry only
  missing/eligible failed deliveries; sent/already_sent/unknown are not reset.
  Sent copy uses the existing message and separate status, never resends SMTP.
- Provider synchronization is explicit, readonly/BODY.PEEK and isolated per folder
  and owned account; exact optional folder names remain configurable. Match replies
  to same-account thread/ticket/domain/provider, not unrelated Inbox/Junk messages.
  Keep immutable original/draft and exact preview bindings with explicit approval.
- Provider Replies web opens actionable Cloudflare cases without requiring an old
  delivery-log match, while keeping source account/thread/ledger checks. Other
  providers remain readonly. Preserve progress/run IDs, current list/draft state
  and safe browser capture diagnostics. No old JSON mail cache or threaded sender.
- Cloudflare Dashboard Cookie stays in the private runner RAM session. Configured
  Account ID takes priority unless a readable unambiguous Cookie account differs.
  Missing/unreadable account, settings mismatch and batch mismatch have distinct
  fixed codes. Keep profile/payload bindings, accepted/unknown protection and
  durable checkpoints; never resend by editing report text.

## Quick Report and presentation

- Netcraft direct API action is explicit preview-then-submit with existing guards,
  no extra confirmation added by refactor. Other form actions remain fill-only;
  no submission/captcha automation. Show daily owned latest-per-workflow history,
  not just the last session operation. Cache clear does not delete DB history.
- Source extension is frontend/extensions; Vite packages its current manifest
  version. Version 3.1.0 has no local bridge or auto-submit branch; web tasks are
  exact-origin capability-bound and fill_only. An ignored installed-path copy can be explicitly refreshed to preserve
  unpacked Chrome IDs. Never modify profiles/install/reload automatically.
- Dashboard reads stored data only, respects feature permissions and personal/
  system scopes, and counts successful deliveries once. Pending review is today;
  historical results are not silently reintroduced as today's work.

## Validation and documentation

Run direct and full backend unittest for core/worker/mail/parser changes. Use
isolated PostgreSQL/Redis and fresh-wheel smoke outside repo for architecture
changes; FE test/build/lint, compileall and pip check as appropriate. Useful
browser/cloaking characterizations and frozen dummy mail fixtures live in
backend/tests. Do not ship retired code solely to make old UI tests pass.
Update README, CLAUDE, docs/technical-guide.md, AGENTS and this skill when their
behavior/workflow changes. Do not restart a worker while it owns active work.

Windows worker shortcut: backend/run_all_workers.bat starts the all-job worker and private Cloudflare runner together in minimized consoles. Run once, keep API/FE separate, stop only when idle. Use --dry-run for launcher validation without claiming real work.

Standard local BE startup is python -m domaincheck (backend/run_backend.bat), supervising API + per-job-type worker processes + Cloudflare runner. Individual worker scripts are separate-service tools only. Never combine them with the supervisor. Local guard prevents duplicate launchers, not standalone workers; transition only when old workers are idle. API reload is isolated from worker lifecycle. Test launcher with mocks/dry-run without claiming queued work.

Team production uses independent Compose services; deploy_team.py builds before draining app services/migrating/restarting. Do not execute this against the user's live stack for validation. Queue slots are bounded per process, claims are owner/type fair under a short advisory transaction lock, browser capture is opt-in and all includes its worker. Graceful drain checkpoints the current item and requeues pending items on the same job; never reset sent/UNKNOWN to implement a restart. Heartbeat health is permission-filtered aggregate metadata only, no URLs/accounts/secrets. Worker slot limits do not replace SMTP-provider quotas or throttle every direct request. Validate a 20x50 mock-probe fixture against isolated PostgreSQL, mock SMTP and Cloudflare concurrency, fresh-wheel packaging, container builds and FE tests. See docs/team-deployment.md.

Team fairness uses one URL per turn and the owner's last-service time across batches, preserving job IDs/item checkpoints/order; derive failure counts from persisted items. Cover both 20x50 and 30x50 mocked-probe workloads, multi-owner rotation and restart without replay. Cloudflare capacity is per URL/step, not held by paused/delayed sessions; keep consent/profile checks inside each acquired step before its intent. Transient Redis heartbeat errors expire markers and retry without logging secret connection errors. Clean production images must install httpx as a runtime dependency and launch pinned Chromium under the non-root application user.
