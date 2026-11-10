# Implementation notes

The supported application is backend FastAPI/SQLAlchemy workers and frontend React. Desktop/Streamlit, navigation/helpers/launcher, JSON worker/review queues and root wrappers were retired on 2026-10-18. The user explicitly chose the implemented web feature set; do not restore missing desktop features during cleanup.

Shared checking, browser/cloaking, mail parsing/formatting and Cloudflare code belongs to domaincheck.engine. Always import it by its full package namespace, including child-process adapters. No backend/compat, root source fallback, top-level force-include or desktop launcher is supported. Hatch builds src/domaincheck only.

Adapters: DomainQuickReportAdapter, DomainInvestigationAdapter, DomainWorkerPrecheckAdapter, EngineBrowserCaptureAdapter, ImapSentCopyAdapter. Preserve persisted engine/policy identifiers (even historic legacy-* strings): they bind caches/previews/fingerprints. Never rewrite old jobs, grants, snapshots or delivery history to rename code.

Registrar contact correction (2026-10-23): the shared WHOIS boundary exposes
only explicitly labelled Registrar Abuse Contact Email values as emails and
abuse_email; generic registrant/admin/technical emails are not report contacts.
radio.am/radio.fm registrations use an exact third-level-name lookup against
the fixed CentralNic WHOIS server, with domain-match validation and bounded
reads. Never infer a registrar from the private suffix or query its parent for
the child's contact. Quick Report uses explicit WHOIS abuse, then RDAP, then
static fallback, preserving a recovered RDAP registrar for form routing.
New Quick Report checks bind legacy-run-cdn-check-v2-registrar-abuse because the
contact policy changed; old engine bindings/results/previews stay immutable.

Link Status registration identities use the bundled offline PSL with PRIVATE
rules, preserving station.radio.am, station.africa.com and similar registrable
names. A bare private suffix still uses its own public-domain registration.
Private registrations bypass parent-TLD IANA RDAP and query only explicitly
supported fixed WHOIS routes; validate Domain Name before accepting EPP status.
Unsupported/unavailable/mismatched private records stay UNKNOWN, never parent
hold/not-found. DNS/HTTP use the original website host/URL and retain public
egress/redirect validation. Link Status remains stateless with no result cache.
Both Link Status subprocess layers must launch the explicit module
domaincheck.core.link_status, not __name__: a probe launched with -m runs as
__main__ and otherwise breaks its nested WHOIS child. Regression tests must
exercise real nested subprocesses with offline fixtures, not only imported
functions or mocked subprocess results.

Engine imports do not read config.ini, create/migrate runtime folders or write JSON/CSV caches. run_check takes explicit config and rejects persistent/file-draft/combined-capture options. Evidence lives in explicit staging/private artifacts. Provider engine contains parsing/reply formatting; IMAP and sending belong to application services with existing owner/account/permission guards.

Removed: desktop config loader/CLI/CT/brandscan, old form-browser opener, JSON batch/review/cache sender, CSV logging and duplicate mail helpers. Browser/cloaking characterizations now live in backend/tests. Frozen dummy draft/HTML fixtures preserve pre-cutover content without shipping old file writers just for comparison.

Keep RBAC/CSRF/session refresh/audit allowlists, day/account/owner scope, immutable profile/evidence/MIME hashes, SMTP intent/checkpoint/UNKNOWN and Sent reconciliation. Provider acceptance differs from HTTP command acceptance/takedown. No external send/mailbox/probe on render.

Domain Worker explicitly accepts browser fragment routes (for example /wap/#/register), retaining them in full URL identity, precheck, review and browser navigation. Other callers retain their existing input policy. Cloaking URL normalization preserves fragments; public egress validates the actual scheme/host/port/DNS independently of the fragment and still denies userinfo (including empty userinfo). Existing evidence URL secret redaction remains in place. No history, engine version or migration changes are needed for this input fix.

Cloudflare accepted-result dedupe is owner/day/cache-epoch scoped (account timezone). Clearing today's cache rotates its epoch and hides old batches; it never overwrites accepted report records or permits commands on cleared batches. UNKNOWN/SUBMITTING stay protected across days and resets. Payload/report hashes stay bound to content; cache epoch is a separate persisted field, including the record uniqueness scope. Provider-side dedupe remains independent.

Extension source is frontend/extensions/cloudflare-profile-worker. Version 3.1.0 removes localhost bridge-fetch/task tokens and the auto-submit branch; only exact-origin capability-exchanged fill_only tasks are accepted. Optional local Application origins remain supported for development. Vite packages build/dev ZIP from the manifest version. Existing unpacked Chrome IDs may depend on their old path; backend/scripts/sync_installed_extension.py can explicitly refresh an ignored installed-path copy. It never edits profiles or reloads Chrome. This is generated current-extension runtime, not a second source/application.

Configuration is backend/.env and DB-owned mail settings. Keep secret/runtime directories at their current locations. backend/compose.yaml keeps the domaincheck project/volume names with env_file relative to backend.

Validate full backend unittest on isolated PostgreSQL/Redis plus fresh wheel, FE test/build/lint, compileall/pip check and standalone install. Do not restart a production worker while it owns active work. See docs/technical-guide.md and docs/testing/web-only-cutover-2026-10-18.md.

Worker launcher: `backend/run_all_workers.bat` starts the all-job worker and Cloudflare runner in minimized independent consoles, retaining logs on failure. Run once; it does not supervise/deduplicate processes or start API/FE. `--dry-run` validates paths without claiming jobs.

The standard backend entry point is python -m domaincheck (backend/run_backend.bat on Windows; console script domaincheck-backend after install). server.py supervises API + one process per enabled job type + Cloudflare runner as separate child process groups. API reload never restarts/spawns workers. Local guard socket 127.0.0.1:8790 prevents duplicate launchers, not independently launched services. Stop former standalone workers when idle before switching. Child exit fails the development stack; shutdown signals and waits without force-killing deliveries. Raw uvicorn remains API-only for externally supervised deployments.

Team deployment uses compose.yaml + compose.production.yaml with separate independently restarted services, private Cloudflare runner, shared artifact volume, non-root backend image and FE/Caddy HTTPS image. backend/scripts/deploy_team.py (Windows deploy.bat) builds, drains app services, migrates and starts the stack, aborting on failure. Do not deploy automatically as part of source validation. First host-to-volume artifact migration and a consistent extension ID across team machines are explicit deployment prerequisites.

worker slots default 2/2/2/2/1 for quick_report/check_domain/domain_worker_precheck/domain_worker_send/browser_evidence; capture remains opt-in. `all` now uses independent polling tasks and includes capture. Each worker shares a bounded DB pool (5+2). CLI workers checkpoint/yield after WORKER_ITEMS_PER_TURN=1, preserving the job and completed items; diagnostic --once retains one-operation behavior. Failed counts are derived from persisted items across turns. Cloudflare limits concurrent per-URL steps to 2 with a private semaphore, releases capacity while paused/delayed, and retains task heartbeat and Cookie isolation. claim_next(fair_owner=True) serializes the short claim transaction under advisory lock 7348291741284, excludes a live same-owner/type batch and orders by the owner's last service across batches before job order. A new batch cannot bypass a waiting owner. No network call occurs under that claim lock. Migration 0006 adds queue/owner indexes only.

Worker SIGINT/SIGTERM drains after the current item, requeues pending items on the same leased job and preserves checkpoints. Domain send retains one-domain-per-claim and uncertain-delivery ledger. Redis slot heartbeat (5s/20s TTL) exposes only permitted group names/state/count at authenticated health/workers. health/ready is DB/cache readiness, distinct from process liveness/worker availability. FE polls every 15s and warns on offline/draining/unknown. Background slot limits do not globally throttle direct review/capture/IMAP/SMTP requests. See docs/team-deployment.md.

## Domain Worker negative-contact cache reset (2026-10-22)

Explicit `POST /api/v1/domain-worker/cache/clear` requires existing
`domain_worker.clear_cache`, active user, trusted origin and CSRF. Under the
owner batch lock, invalidate only today's succeeded excluded_no_email checks
via cache provenance; retain original results/history and delivery protections.
Busy/paused/awaiting-send batches reject reset. Ordinary precheck retains cached
negative contacts until explicit reset or the next account-local business day.

## Dynadot adapter ? 2026-11-06

`dynadot_phishing` is registrar-routed, exact-origin HTTPS www.dynadot.com/report-abuse and fill_only. Fields use the supplied Phishing form names with `_2` suffix, never Vue-generated data attributes. Domain is the target hostname; full URL retains path/query/fragment. Official Website and VPN/Proxy/user-agent use N/A by operator instruction. Preserve nonempty operator values, repair empty fields after Vue mount, leave phone/file/verification/submit untouched. Native Phishing selection requires one exact unambiguous option; custom dropdowns remain manual pending observed markup. No CAPTCHA token from the supplied HTML is stored.

Private registrar WHOIS update (2026-11-07): africa.com registrations use the fixed TLS-verified https://whois.gatewaysrs.com/whois/whois.sh endpoint (Domain parameter), bounded to 256 KiB and a streaming deadline. Parse only preformatted record text and require exact Domain Name matching; expose only explicitly labelled registrar abuse emails. hk.com registrations use bounded TCP WHOIS at whois.udr.hk.com; unavailable/mismatched records return manual lookup metadata for https://whois.hk.com/. Both example domains were checked against registry WHOIS only. Marcaria's officially published abuse@marcaria.com is a static registrar fallback when the validated record has no abuse email, never an assumed registrar for the suffix. Neither namespace may fall back to public parent WHOIS/RDAP. Quick Report new checks use v3-private-whois; historical bindings remain unchanged. Link Status routing is unchanged by this contact lookup update.


## Verisign adapter — 2026-11-10

Correction in extension 3.1.6 / adapter 1.0.1: select PHISHING while waiting and require the dynamically revealed phishingDetails input, filled from brand_name. Forward registrar/target_domain from background session storage to form-task-for-tab (storage-only assertions missed this boundary). In isolated content-script worlds Lightning synthetic shadow is light DOM: datepicker ancestry must follow parentElement OR native root.host. Page-world-only smoke cannot prove extension behavior; verify with a CDP isolated-world smoke and synthetic light-DOM fixtures. Confirmed both dates, brand and registrar on the real page using dummy data, without Next/submission.

`verisign_dns_abuse` is registry-routed using the existing Verisign catalog; never route private suffixes (hk.com/africa.com/uk.com) via their parent .com. Extension 3.1.5 accepts only HTTPS verisign.my.site.com /DNSAbuse and its observed /ReportAbuse/ redirect. Traverse open Lightning shadow roots; scope unique native controls to c-abuse-form and use stable field names. Select DNS Abuse/PHISHING and exact TLD; use the check domain with original full URL. Registrar and target_domain travel in the existing owner/device-bound task snapshot and exchange (defaults preserve older tasks); no migration. Click each observed lightning-datepicker input and its unique Today button, using the local browser day. Never overwrite nonempty values or truncate oversized descriptions; conflicts/unsupported TLD remain manual. Registrar is optional. Next, CAPTCHA, attachments and Submit are always manual; dates/notification claims require operator review. No delivery outcome is inferred.


Quick Report compatibility (2026-11-10): import extension manifest as a Vite module for HMR-safe download labels; do not freeze version in startup define. Gate autofill per provider minimum: baseline fill-only 3.1.0, UDR 3.1.1, CentralNic 3.1.2, GoDaddy 3.1.3, Dynadot 3.1.4, Verisign 3.1.6. Older connected versions retain compatible forms; incompatible forms expose a manual link, never create a task. Invalid versions fail compatibility. A refreshed handshake unlocks updated providers. This is FE compatibility, not backend authorization.


Unified environment ownership (2026-11-10): VITE_FORM_ASSISTANT_EXTENSION_ID belongs in frontend/.env in both local and Docker production. backend/.env owns backend/infrastructure only. deploy_team.py passes both --env-file .env and --env-file ../frontend/.env from backend for every Compose command and checks both files before any mutation. Frontend env remains excluded from Docker context; only the public extension ID is forwarded as a build argument. Never copy/edit real env during source updates. Direct Compose runbooks must use both env files; ID changes require a FE rebuild.
