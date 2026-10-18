# Check Link Status web — 2026-10-18

Follow-up: removed the CSV button, download function and export error state.
The remaining three frontend workflow tests pass. Earlier four-test results below
refer to the preceding version that still had CSV export. Backend is unchanged.

## Behavior (updated to stateless streaming)

The menu streams fresh observations directly to the requesting tab. No results
are stored/read in DB, user workspaces, query cache or browser storage. Reload or
unmount clears results; abort stops scheduling remaining work. Separate tabs and
users check independently. Existing login/action permissions/CSRF remain.

No application URL count or text length cap; regression accepts 5,000 URLs and
more than 100,000 characters, and streams 250 results. O(n) dedupe preserves URL
paths/query, IDNA and group headings. Five executor probes maximum per API process,
five workers per request and a bounded result queue avoid task-per-URL fan-out.

Filters combine domain and website statuses with AND. Excel/CSV export has been
removed from the web page, including download logic and export-specific state. Recheck always sends a fresh
request. No GET workspace or server export route remains. Old runtime snapshots
are left untouched and unused; no migration or runtime cleanup was performed.

## Interpretation

- Registration: HTTPS RDAP using IANA bootstrap; validate returned domain identity,
  cap response size, validate redirects, keep TLS verification. WHOIS fallback is
  separately bounded. EPP clientHold/serverHold are explicit hold evidence.
  RDAP inactive is not treated as a specific EPP hold. Lookup failure means unknown.
  Authoritative RDAP 404 means not found at that service, not proof of availability
  for registration. Registrable domain uses tldextract 5.3.0's bundled offline PSL.
- DNS: OS socket resolver A/AAAA for the exact hostname (source SYSTEM); on
  OS failure use bounded direct DNS A/AAAA/CNAME plus registrable-domain NS
  fallback, distinguishing NXDOMAIN, no address, timeout and resolver failure. DNS is
  observed from backend infrastructure, not a worldwide reachability assertion.
- HTTP: validate public destinations before requests and each redirect; bounded
  GET and response preview, HTTPS certificate verification, maximum eight hops.
  Cloudflare abuse warning requires provider evidence and warning title/body pair;
  cf-mitigated:challenge is separate. HTTP 403, 429, 451 and 5xx remain access/rate/
  legal/server conditions. 404/410 concern the URL, not domain registration.
  HTTP OK means successful HTTP response, not verified site legitimacy/content.
- Registration hold/not-found with DNS still resolving displays a conflict. No
  result automatically asserts successful takedown, its cause or provider action.

## Execution and validation

POST /link-status/check returns NDJSON start/result/heartbeat/complete events.
X-Accel-Buffering:no and Cache-Control:no-store. Heartbeat while waiting is 10s.
Each probe retains its 45s subprocess deadline. IANA bootstrap metadata may be
cached; domain/DNS/HTTP observations are not. Reverse proxy must allow streaming
and sufficient connection lifetime/body size for the user's large input.

Focused backend 12/12 and frontend 4/4 cover stateless isolation, fresh repeated
requests, large input, bounded scheduling, disconnect, permission/CSRF, partial
stream, no restoration after remount, filters and local CSV. Final clean PostgreSQL
run: 291 tests, 290 passed and one Redis integration skip, 164.902 seconds; fresh
wheel smoke 3/3. Frontend 113/113, typecheck/lint/build passed. compileall/pip check/
diff whitespace checks passed. Desktop 1440px/mobile 390px fixture QA passed check,
filter/clear, reload discards results, no JS errors or horizontal page overflow.
Legacy 420 retains known baseline four failures/seven errors. API health 200.
No real suspicious URL/report/mail operation or runtime cleanup.

Previous version had owned snapshots and a 200 URL cap; this revision explicitly
replaces those choices per operator request. No legacy source changed.

References: [ICANN RDAP transition](https://www.icann.org/en/announcements/details/icann-update-launching-rdap-sunsetting-whois-27-01-2025-en),
[EPP status codes](https://www.icann.org/resources/pages/epp-status-codes-2014-06-16-en),
[Cloudflare challenge detection](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/),
[HTTP semantics](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.5.5).

## DNS correction follow-up

On this Windows host, direct dnspython A/AAAA queries for the operator-provided
freightforwarder.my timed out while socket.getaddrinfo returned public addresses.
The old DNS gate skipped HTTP, and the HTTP public-address guard also depended on
direct DNS. Both Link Status paths now use the OS resolver with a 3s local deadline;
the shared policy implementation and other workflows remain unchanged. Failed OS
resolution retains the detailed direct-DNS fallback. Successful OS results do not
invent CNAME/NS data. Mixed private/public addresses and private redirects still
fail closed. End-to-end check_bounded returned DNS_OK, HTTP_OK, HTTP 301 redirect
to https://filmeonline.live/vi-vn/, then HTTP 200. This was an explicit user-requested
GET observation; no report, mail or runtime mutation.

Focused backend: 16/16 passed, including four new DNS/guard regressions.

DNS fix validation: backend 295 total, 137 passed / 158 integration skips
(no isolated DB/Redis configured); tests namespace was explicitly scoped to
backend/tests to avoid collision with legacy tests. Legacy 420 keeps the existing
4 failures / 7 errors. compileall passed. No frontend change or dependency change.
