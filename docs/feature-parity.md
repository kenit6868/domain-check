# Ma trận feature parity và truy vết yêu cầu

Trạng thái ban đầu: `planned`. Cột test là acceptance bắt buộc, không phải bằng
chứng đã chạy. Chi tiết dependency nằm trong `implementation-plan.md`.

| Feature/action/invariant | Nguồn legacy | Task | Đầu ra mới | Test nghiệm thu | Trạng thái |
|---|---|---|---|---|---|
| Login Telegram/pending/logout | chưa có | AUTH-01, AUTH-02, ADMIN-01 | OIDC/session API + React | HTTP Basic token exchange, signature/state/replay/open redirect/pending; live BotFather credentials still required | implemented with mock/protocol tests; live verification pending |
| User/role/custom permission | chưa có | RBAC-01, ADMIN-01 | admin APIs/pages | action authz, last admin concurrent | planned |
| Mail/proxy/signature multi-account | `load_config`, email helpers | MAIL-01/02 | owned encrypted settings + React config page + explicit connection test | secret presence-only, owner isolation/default constraint; TLS/timeout/no-send/no-mailbox-read mock đạt | implemented |
| Owner isolation mọi resource | chưa có | CTX-01, ISO-01 | immutable context + owned repository + composite FK | A/B same URL/account, direct/nested ID, API 404, pool reuse | verified foundation; feature-specific tests tiếp tục ở từng task |
| Quick parse/filter/check | page 7, `run_cdn_check` | QR-01/02 | owned durable checks API/worker + React two-step filter/check/poll/F5 | full URL/order/duplicate, A/B cache, F5 polling | verified except remaining QR action acceptance |
| Quick registrar/registry recipients | resolver | QR-01/02 | sanitized result DTO + React result cards | legacy resolver tests + adapter không gọi heavy `run_check` | implemented and FE component-tested |
| Quick provider forms 8 adapters | page 7, extension | EXT-01/02 | HTTPS capability task + provider adapters | EXT-01: one-time/replay/device/owner/manual fallback; EXT-02: đủ 8 adapter | transport + Google/Microsoft verified; six adapters pending |
| Quick Netcraft submit | page 7 | QR-01/02 | explicit preview/submit | permission+confirmation+idempotency | planned |
| Quick nic.top export | page 7 | QR-01/02 | authorized export | exact rows/order/full URL | planned |
| Full Check Domain | `run_check`, page 1 | CD-01/02 | operation API/page | WHOIS/SSL/VT/GSB/HTTP/MX/CDN errors isolated | planned |
| VT optional submit | page 1 | CD-01/02 | explicit action | default off, separate permission | planned |
| Cloaking panel | detector/UI | CD-01/02 | DTO/page section | HTTP-first, passive browser, terminal semantics | planned |
| Browser Evidence passive/DOM-open/upload | `browser_evidence`, page 1 | EVID-01, CD-02 | artifact API/gallery | 1–3, full URL/hash, no click claim | planned |
| Draft preview/download/send | formatters/email UI | PREVIEW-01, DELIVERY-01, CD-02 | exact plan | same immutable artifacts single/all | planned |
| Wayback actions | page 1 | CD-02 | explicit actions | no auto-call on page load | planned |
| Cloudflare precheck | page 14/worker | CF-01/03 | API/table | Cloudflare filter, excluded table opt-in | planned |
| Cookie session | dashboard session | CF-02 | session worker | RAM-only/restart/auth affinity/log scan | planned |
| Cloudflare start/stop/resume/retry | worker ledger | CF-01/03 | durable job | input order, UNKNOWN/no retry, ALREADY terminal | planned |
| Domain Worker precheck v4 | worker/page 6 | DW-01/03 | API/page | parallel lookup/cloaking, incremental persist | planned |
| no-email/review/ready branches | worker | DW-01 | job/review rows | no-email not review, evidence gate | planned |
| Worker send/stop/resume/retry | worker | DW-02/03 | durable runner | account snapshot, partial, no double send/count | planned |
| Cloaking list/dedupe/source history | queue/page 10 | CR-01/03 | owned cases/table | owner/day/full URL, stable ID | planned |
| confirm/not-cloaking/skip | sender/page 10 | CR-02/03 | review actions | evidence rules, clean normal draft, skip | planned |
| Cloaking direct send/partial retry | sender/queue | CR-02/03 | preview/send ledger | exact body, fingerprint, missing accounts only | planned |
| Evidence Review daily merge | page 12/worker | ER-01/02 | owned cases/page | all source jobs same owner updated | planned |
| Evidence Review upload/preview/send | page 12 | ER-01/02 | direct send | 1–3, while worker runs, failed evidence retained | planned |
| Link parse/group/check | page 8, `link_status` | LINK-01/02 | operation/page | input order/status/redirect/WHOIS Hold | planned |
| Link two exports | page 8 | LINK-01/02 | authorized export | exact categories/full URL/group | planned |
| Provider explicit sync/folders | provider module/page 9 | PR-01/02 | API/page | Inbox/Junk/evidence/Cloudflare Unicode/error isolation | planned |
| Provider filter/seen/thread reply | provider module/page 9 | PR-01/02 | message/thread workflow | mailbox+UID, In-Reply-To/References, exact evidence | planned |
| Lead explicit parse/per-row/batch | lead v24/page 15 | LT-01/02 | API/page | target/backend scope, cache displayed scope | planned |
| Lead report/evidence/cloaking/send | lead/shared sender | LT-01/02 | shared preview/review | caveats, English exact, Vietnamese read-only, sent lock | planned |
| Related Domains/Brand Scan | core/CLI/pages 2/3 | NAV-01 | CLI + hidden flags | CLI smoke; absent navigation | planned |
| Remove four menus | pages 4/5/11/13 | NAV-01 | no React route/menu | direct route absent, ledgers/drafts still work | planned |
| Legacy CLI commands | `phishing_toolkit.main` | QR-01, CD-01, NAV-01 | compatibility facade | check/related/brandscan/send import/smoke | planned |
| URLScan remains removed | scrub/gates | all report tasks | no DTO/action/template | source/response snapshot scan | planned |
| English external reports | formatters | all preview tasks | BE-created body | no Vietnamese UI labels/unverified claims | planned |
| SMTP uncertain outcome | email helper | DELIVERY-01 | attempt state | crash after SMTP before checkpoint, no blind retry | planned |
| SSRF/egress/rebinding | new web boundary | NET-01 | network policy | private/metadata/IPv6/redirect/subresource | planned |
| Legacy import/cutover | runtime files | MIG-01/02 | dry-run CLI/docs | idempotent/quarantine/no source mutation | planned |

## Intentional differences

- Auth/RBAC/owner scope and server sessions are new.
- Quick Report cache becomes owner + business-day scoped; the old seen file has
  no day and is not assigned an invented timestamp.
- Desktop `open_in_installed_chrome()` is replaced for web by HTTPS capability
  tasks; manual link/copy remains fallback.
- Web egress policy may reject internal/private targets as policy errors; this is
  not a phishing/link verdict.
- Four menus are removed only from web navigation/routes; internal services and
  history remain.

