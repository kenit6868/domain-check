# Kế hoạch triển khai web nhiều người dùng

2026-10-16 — Theo yêu cầu operator, triển khai gửi batch cho normal ready domains:
daily ledger summary, confirmed start, owned account scope, immutable plan/evidence,
SMTP intent/checkpoint/UNKNOWN recovery, failed-only retry, cooperative stop/resume,
persistent batch pacing and local-midnight reset. UI counts during precheck, final
eligible list and batch settings. Large bytes stay private artifacts, DB stores
metadata/ledger; Redis is not delivery truth. Worker type domain_worker_send/all.
Normal sending is enabled independently of the pending cloaking approval/send
migration; cloaking/evidence-incomplete cannot enter ordinary sending. This explicit
scope supersedes the earlier release sequencing gate below, not its isolation gates.
QA: FE 72/72, isolated PostgreSQL backend 195 passed/2 skipped, wheel 3/3;
lint/typecheck/build/compileall/pip check passed. Browser fixture desktop/mobile
verified counters, batch configuration, confirmed single POST and F5 restoration.
Mock SMTP verified per-recipient partial retry, stable Message-ID, uncertain intent
recovery, day reset and changed profile/evidence rejection. Legacy 415 retains
baseline 4 failures/7 errors. Idle local workers reloaded; no live SMTP performed.

2026-10-16 — Domain Worker flow correction: account selection trước precheck,
durable scope xuyên suốt job/case/source; ready-only normal list, no-email excluded,
dedicated cloaking/evidence routes. Remove per-row draft/exact preview UI (server
compatibility remains bound to source account scope). Normal evidence manual upload
validates all images then resolves source checks without account changes. Cloaking
page currently reads queue only; approve/send/full migration remain pending.
Artifact integrity root cause was relative storage path depending on process CWD;
normalize paths and recover exact bytes from alternate known root, no hash bypass.
QA: FE 70/70; backend isolated PostgreSQL 183 passed/2 skipped; wheel 3/3;
lint/typecheck/build/compileall/pip check passed. Browser fixture desktop/mobile
verified account scope, ready-only list and F5 without resubmission. Existing legacy
415 tests retain baseline 4 failures/7 errors. Idle workers restarted, health 200.

2026-10-16 — Chốt lại capture theo yêu cầu dùng logic legacy: web adapter gọi
`capture_normal_report_evidence()` bằng mặc định HTTP/DNS, DOM source/destination
rồi passive fallback. Bỏ override http_probe/network_guard, DNS pool và DNS helper
capture riêng. Giữ adapter chuyển artifact owned + kiểm manifest/hash; cấu hình
browser proxy là opt-in. Mốc này thay thế các ghi chú custom DNS/NET capture trước.
Đã capture đúng URL thành công hai ảnh bằng defaults và reload worker idle. QA:
FE 72/72; backend PostgreSQL riêng 178 đạt/1 skip, wheel 3/3; typecheck/lint/build/
compileall/pip check đạt. Legacy 415 giữ baseline 4F/7E.

2026-10-16 — Follow-up NET capture: URL thực tế vẫn lỗi sau khi cài Playwright vì
dnspython timeout trong khi system DNS Windows hoạt động. Default browser resolver
dùng getaddrinfo bounded 3 giây/pool 4, kiểm mọi public IPv4/IPv6 trước request;
private/mixed, timeout và DNS fail vẫn chặn. Capture đúng URL đã thành công hai ảnh
nguồn–đích; không coi smoke fixture trước đó là xác minh luồng thực tế.
QA: FE 72/72; backend 181 đạt/1 skip trên PostgreSQL sạch, wheel 3/3; typecheck/
lint/build/compileall/pip check đạt. Legacy 415 giữ baseline 4F/7E. Hai worker
idle đã restart, API live healthy và stderr worker rỗng.

2026-10-16 — Sửa vận hành Browser Evidence/RBAC: Playwright là backend dependency,
startup capture worker kiểm launch Chromium trước claim, lỗi từng URL chỉ persist
fixed code. Runtime thiếu Playwright đã được cài và smoke DOM 2 ảnh bằng loopback.
Admin sửa được permission/tên/mô tả như user theo yêu cầu mới; sync không ghi đè
lựa chọn của cả hai role. Lưu role tự upsert code permission đã đăng ký còn thiếu
DB, loại lỗi catalog drift. Không tự recheck URL cũ, không gửi SMTP/IMAP/report.
QA: FE 72/72; backend PostgreSQL sạch 179 đạt/1 skip, wheel packaging 3/3;
typecheck/lint/build/compileall/pip check đạt. Legacy 415 giữ baseline 4F/7E.
Chrome fixture xác nhận sửa/lưu quyền admin desktop/mobile; API live healthy,
hai worker đã restart khi idle và log stderr rỗng.

2026-10-16 — Domain Worker exact preview triển khai sau DW-01: ready-only owned API,
permission `domain_worker.preview`, account selection, server-side recipient plan,
shared legacy personalization/quality gate và immutable plain/HTML preview. Bind default
logo thành owned inline artifact, 1–3 ảnh + JSON manifest, check/result/manifest hash,
account/profile/evidence versions, revision và expiry 30 phút. React chọn account,
xem từng delivery, thumbnail/hash và GET restore qua F5. Chưa triển khai approve/send,
full enrichment hoặc Cloaking/Evidence Review web; không coi DW-02/03 hoàn tất.
Kiểm thử: frontend 71/71, backend PostgreSQL sạch 174 đạt/2 skip (packaging 3/3),
typecheck/lint/build/compileall/pip check đạt; legacy 415 giữ baseline 4F/7E.
Chrome fixture kiểm desktop/mobile 390px và F5, không external side effect.

DW-01 (2026-10-16): triển khai bước đầu Domain Worker web: POST/GET owned batch
precheck, stop/resume/retry, readiness, read-only advisory draft; durable Check/Job/
JobItem và ReviewCase/CaseSource hiện có, không migration. React bảng input order,
GET restore qua F5, trạng thái no-email/cloaking/evidence/ready. CLI có loại job riêng.
Draft email đối chiếu byte-for-byte với generator legacy sau full-URL normalization
cho Registrar/Registry/Hosting. Không thay bằng webform hay tự gửi thật.
Phần còn lại: xác minh/chọn ảnh Playwright cloaking trong web review, uploader
review thường, full enrichment, approval/delivery ledger/SMTP. Account scope/signature
override và exact preview cho ready đã được bổ sung ở mốc phía trên. Không coi DW-01
là hoàn tất migration Domain Worker.

UI density 2026-10-16: mở rộng thiết kế gọn của Quick Report sang AppShell,
Dashboard, Check Domain, mail và quản trị; giảm header/sidebar/table/form/dialog.
Check Domain dùng InvestigationDetails thay JSON mặc định; raw/profile giữ trong
details. Guidance browser trên, CDN/Registrar/Registry dưới. Chỉ đổi presentation.

> Cập nhật: 2026-10-15. Đây là tài liệu điều hành sống. PostgreSQL + Redis đã
> được chốt; không mở lại lựa chọn database. Mọi external side effect trong quá
> trình phát triển dùng mock/fixture/dry-run.

## 1. Hiện trạng đã xác minh

### Facts từ checkout

- Ứng dụng hiện tại là Python/Streamlit, 75 file Python và 32.916 dòng theo phép
  đếm line tại checkout này. Entrypoint là `streamlit_app.py`, CLI chính là
  `phishing_toolkit.py`; `domain_check.py`, `domain_worker.py` và `launcher.py`
  là các entrypoint/compatibility khác.
- Core dùng chung đã xác minh gồm `run_check()`, `run_cdn_check()`,
  `resolve_quick_report_recipients()`, `check_links()`,
  `prepare_lead_review()` và `send_lead_review()`. `lead_triage.MODULE_VERSION`
  hiện là 24.
- Persistence đang là JSON/CSV/folder dùng chung: `data/worker_jobs`,
  `cloaking_review`, `sent_log.csv`, precheck/no-email cache, Cloudflare ledger,
  Provider Replies cache/log, Lead Triage snapshot và evidence folders.
- Các module có default argument đã bind `LEDGER_PATH`/cache path; thay global
  sau import không đủ an toàn. Quick Report giữ executor/future/result trong
  `st.cache_resource`. Các path và lock hiện không có owner namespace.
- `config.example.ini` mô tả API/company/Cloudflare/cloaking và nhiều SMTP account,
  proxy, IMAP. Không đọc `config.ini` thật.
- Navigation Streamlit hiện vẫn có bốn menu sẽ bỏ. Related Domains/Brand Scan đã
  ẩn. Extension là MV3 và có tám adapter provider.
- `.agents/skills/developing-with-streamlit` đã bị xóa trước lượt này; không phục
  hồi. React không phụ thuộc skill đó. Skill takedown đã được đọc đầy đủ.
- Toolchain quan sát: Python 3.14.6, Node 24.18.0, npm 12.0.1, Docker 29.5.3.
  Runtime support matrix sẽ được pin riêng, không suy ra production support từ
  các phiên bản cài cục bộ.

### Baseline kiểm thử

Lệnh: `python -m unittest discover -s tests -v` trên Windows, Python 3.14.6.
Kết quả: 415 test, 24,336 giây, 4 failure + 7 error.

- 4 error do giả lập macOS `Path` trong `cloudflare_form_worker` (hai test, bốn
  subtest).
- 2 error stop/process group của Domain Worker.
- 1 error Lead Triage coverage gap.
- Failure: Domain Worker UI stop/restart/retry; Lead Triage shared-hosting
  formatter; Provider Replies all-folders statistics; Provider Replies UI exact
  attachment pair.

Đây là baseline đỏ cần giữ tách biệt. Task migration chỉ được `done` khi test mới
liên quan xanh và không tạo regression ngoài danh sách baseline; các lỗi baseline
không được sửa expected output chỉ để che sai lệch.

### Giả định cần kiểm chứng

- PostgreSQL 16+ và Redis 7+ là baseline triển khai ban đầu; phiên bản image và
  dependency cụ thể sẽ được đối chiếu tài liệu chính thức, pin trong lockfile.
- Một deployment nhỏ bắt đầu với một API, một general worker, một capture worker,
  một Cloudflare session worker; giới hạn concurrency là cấu hình.
- Artifact dùng private volume giai đoạn đầu; retention, tải dự kiến và kích cỡ
  volume chưa được cung cấp nên phải đo ở M6, không chặn M1.
- Telegram OIDC client/redirect và production domain dùng placeholder; live login
  không được gọi trong test.

## 2. Phạm vi và quyết định cố định

- FE: React, TypeScript strict, Vite, Tailwind, React Router, TanStack Query,
  React Hook Form/Zod và component primitives accessible.
- BE: FastAPI/Pydantic, SQLAlchemy/Alembic, PostgreSQL là durable source, Redis
  chỉ cache/signal/rate limit, artifact storage private qua port.
- 24 bảng nền tảng theo prompt, migration theo identity → config → operation →
  review/delivery → artifact/support. Bảng bổ sung chỉ qua ADR và traceability.
- Telegram OIDC Authorization Code + PKCE, opaque server session cookie, CSRF và
  origin check. User mới `pending`; bootstrap admin explicit.
- RBAC permission catalog nằm trong code; role là dữ liệu. Owner là `users.id`,
  actor và sender account là identity khác nhau.
- Bỏ route/menu Thống kê email, Thống kê tổng quát, Case Log, Report Drafts;
  không xóa ledger/draft/log/history.
- Thứ tự feature: Quick Report → Check Domain → Cloudflare Worker → Domain Worker
  → Cloaking Review → Domain Evidence Review → Link Status → Provider Replies →
  Lead Triage. Related/Brand Scan giữ core/CLI, feature flag ẩn.
- CLI và Streamlit legacy tồn tại qua compatibility facade trong chuyển tiếp;
  web multiuser tuyệt đối không dùng global file repository.

## 3. Dependency và release gate

```text
M0 plan/contracts/baseline
 └─ M1 DB + context + auth + RBAC + secrets + owned repositories
     ├─ M2 Quick Report + extension transport
     ├─ M2 Check Domain + artifact/evidence/preview primitives
     │   └─ M4 Domain Worker + Cloaking Review + Evidence Review
     ├─ M3 Cloudflare Worker (dùng routing M2, session RAM-only)
     └─ M5 Link Status → Provider Replies → Lead Triage
         └─ M6 migration rehearsal + hardening + release candidate
```

Gates bắt buộc:

1. Không mở API multiuser nếu DB-01, CTX-01, AUTH-01, RBAC-01, ISO-01 và SEC-01
   chưa đạt isolation A/B.
2. Không mở external send nếu PREVIEW-01, DELIVERY-01, authorization negative
   tests và uncertain-attempt recovery chưa đạt.
3. Theo yêu cầu operator, cho phép gửi batch domain thường đã ready có evidence;
   cloaking và thiếu ảnh vẫn bị chặn, không chờ hoàn tất direct-send cloaking web.
4. Không cutover dữ liệu thật/deploy public/live Telegram/live SMTP/IMAP/provider
   khi chưa có yêu cầu riêng và rehearsal M6 xanh.

## 4. Work breakdown

Trạng thái dùng `todo`, `in_progress`, `blocked`, `done`. `done` phải kèm bằng
chứng trong mục 10.

| Task ID | Mục tiêu / đầu ra | Module dự kiến | Depends on | Kiểm thử / done | Rủi ro / rollback | Trạng thái |
|---|---|---|---|---|---|---|
| M0-01 | Plan, baseline, facts/assumptions | `docs/implementation-plan.md` | - | mọi dependency có task; baseline ghi thật | revert docs | done |
| M0-02 | Ma trận old→new/page/action/invariant | `docs/feature-parity.md` | M0-01 | không thiếu menu/action | bổ sung mapping | done |
| M0-03 | Inventory file/cache/global/default path | `docs/storage-map.md` | M0-01 | mọi durable/temp source có owner/retention | read-only survey | done |
| M0-04 | ADR stack, ownership, side effects | `docs/architecture-decisions.md` | M0-01 | quyết định/alternative/consequence rõ | ADR mới thay vì sửa lịch sử | done |
| PKG-01 | Skeleton backend installable + settings | `backend/pyproject.toml`, `backend/src/domaincheck` | M0-01..04 | import, settings test, no secret default | xóa package mới | done |
| FE-01 | Vite/React/TS/Tailwind shell + quality scripts | `frontend/` | M0-01..04 | build/typecheck/lint/unit | rollback folder | done |
| FE-02 | Feature-based frontend architecture + shared Axios transport/query keys | `frontend/src/app`, `components`, `features`, `lib/http` | FE-01 | CSRF/credentials/error normalization/abort; no component-level HTTP; strict quality gates | compatibility exports only while features migrate | done |
| OPS-01 | Compose dev/test, healthcheck, example env | `compose.yaml`, `deploy/`, `backend/.env.example` | PKG-01 | services healthy; no runtime data mounted into static FE | profiles off by default | in_progress |
| OPS-02 | Tách cấu hình runtime theo package | `backend/.env.example`, backend settings, Compose, docs | OPS-01 | FastAPI và PostgreSQL cùng đọc backend env; root không cần `.env`; settings test khóa đúng path | revert env path and Compose env_file together | done |
| OPS-03 | Bảo đảm SQLAlchemy asyncio cài đủ runtime dependency | `backend/pyproject.toml`, setup docs | OPS-01 | clean venv installs greenlet and Alembic imports async engine | revert dependency extra with migration disabled | done |
| DB-01 | 24-table SQLAlchemy model + initial Alembic chain | backend db/migrations | PKG-01 | upgrade/downgrade on test PG; FK/index/unique | downgrade before real data | done |
| DB-02 | Repositories + UoW + optimistic version/lease | backend infrastructure/db, ports | DB-01 | transaction/claim race tests | feature flag DB adapter | done |
| CTX-01 | Immutable ExecutionContext and owner resolver | application/api dependencies | DB-01 | owner server-derived; no body override | deny web actions if missing | done |
| CACHE-01 | Redis namespaces/TTL-to-midnight/cache fallback | infrastructure/cache | CTX-01 | A/B, timezone rollover, flush loses no truth | bypass Redis | done |
| ART-01 | Private artifact port/filesystem adapter | ports/artifacts | DB-01, CTX-01 | path traversal, MIME/signature/size/hash, atomic batch | quarantine staged files | done |
| SEC-01 | Secret envelope encryption/key version/redaction | infrastructure/secrets/settings | PKG-01 | decrypt/rotate, responses/logs contain no secret | disable account on key error | done |
| AUTH-01 | Telegram OIDC mock flow + opaque sessions/CSRF | api/auth, infrastructure/auth | DB-01, SEC-01, CACHE-01 | issuer/aud/exp/state/PKCE/replay/open redirect negatives | disable provider route | done |
| AUTH-02 | Telegram production contract + diagnosable login page | OIDC client, auth API, frontend login | AUTH-01, FE-02 | HTTP Basic token exchange, config status, callback denial/error, no secret exposure | keep login disabled with actionable setup state | done |
| RBAC-01 | Permission catalog/seeds/enforcement | domain/authz, API dependencies | DB-01, AUTH-01 | view/action separation, default deny | seed rollback | done |
| RBAC-02 | Chuẩn hóa deploy-time RBAC seed và bootstrap đầu tiên | admin CLI, seed service, setup docs | RBAC-01, AUTH-02 | sync idempotent; roles tồn tại trước admin; bootstrap explicit theo stable subject | rerun sync; revoke bootstrap role assignment transactionally | done |
| RBAC-03 | Admin/user sửa được permission và label | permission catalog, seed/migration, admin service/API, React role editor | RBAC-02, ADMIN-01 | admin seed full lần đầu; user seed nghiệp vụ trừ users/roles; sync giữ cả hai; save tự sửa catalog drift; activation gắn user; bootstrap chỉ admin; đổi role revoke session | reset explicit thay bộ permission user | done |
| ADMIN-01 | User/role/session APIs + last-admin transaction | api/users/roles | RBAC-01 | concurrent last-admin, revoke/authz version | protected system roles | done |
| MAIL-01 | Owned mail/profile/proxy settings API | api/mail, config compatibility | SEC-01, RBAC-01, CTX-01 | masked PATCH, account owner/FK, transport schema | soft-disable account | done |
| MAIL-02 | SMTP/IMAP connection tests (explicit, mock) | core/mail/application | MAIL-01 | no auto-connect; timeout/TLS semantics | action disabled | done |
| ISO-01 | A/B isolation across repos/API/pool/worker | all owned repositories | CTX-01, RBAC-01, DB-02, CACHE-01, ART-01, MAIL-01 | direct/nested IDs, pool reuse, same URL/account | block multiuser release | done |
| JOB-01 | Durable job/item/operation claim/lease/heartbeat | workers/application/jobs | DB-02, ISO-01 | double claim, crash/restart, stop scope | stop worker; preserve rows | done |
| PREVIEW-01 | Draft artifact + revision/fingerprint/expiry | application/previews | ART-01, ISO-01, MAIL-01 | stale account/body/evidence returns 409 | expire plans | done |
| DELIVERY-01 | Delivery/attempt claim/idempotency/uncertain | application/delivery | PREVIEW-01, JOB-01 | two tabs, crash windows, stable Message-ID | stop on uncertain | done |
| QR-01 | Quick Report core adapter/API/persisted checks | api/quick_reports, compatibility | ISO-01, JOB-01 | legacy fixture parity; daily cache intentional diff | legacy facade remains | done |
| QR-02 | Quick Report React workflow, exact Netcraft preview/approval, nic.top XLSX | frontend / action API | QR-01, FE-02, EXT-01/02 | owned preview/intent/dedupe, literal full URL export, mock tests | Netcraft opt-in flag; live provider verification pending | done |
| EXT-01 | HTTPS capability task/device handshake | API form-tasks + extension | AUTH-01, RBAC-01, QR-01 | registration + one-time exchange, expiry/wrong owner/origin/provider/replay; token không vào provider URL | giữ local bridge compatibility; manual link/copy fallback | done |
| EXT-02 | Eight adapters connected via owned form tasks | extension adapters/popup | EXT-01 | server routing, public contact fields, fill-only coordinator, fixtures | retain legacy local bridge; live markup may change | done |
| UI-02 | Dark professional application shell and login experience | frontend app/layout/styles/foundation | FE-02, AUTH-02 | forced dark theme, desktop/sidebar/mobile nav, keyboard/390px/1024px, auth states | CSS-only rollback; no business policy in FE | done |
| CD-01 | Full Check Domain adapter/API/DTO | api/domain_checks, core/investigation | ISO-01, JOB-01 | fixture parity per source; submit-VT permission | legacy `run_check` facade | done |
| EVID-01 | capture/upload/evidence DTO and worker isolation | core/evidence/workers | ART-01, CD-01 | SSRF/terminal/hash/full URL/mutation | disable capture | partial: upload/worker done, capture gated pending NET-01 |
| CD-02 | Check Domain React result/report guidance | frontend check-domain | CD-01 | one target, owned read, legacy pure formatter, no auto-send | no evidence dependency | partial: result and read-only form guidance done |
| CF-01 | Durable Cookie Dashboard ledger/state machine | cloudflare application/repo | ISO-01, JOB-01, QR-01 | dedupe/order/UNKNOWN/ALREADY_SUBMITTED | stop session worker | todo |
| CF-02 | RAM-only Cookie handoff + owner/job affinity | cloudflare session worker | CF-01, SEC-01 | restart loses cookie; logs/DB/Redis scan clean | require new session | todo |
| CF-03 | Cloudflare React precheck/config/table/actions | frontend cloudflare-worker | CF-01, CF-02 | start/stop/resume/retry/session states | route feature flag | todo |
| DW-01 | Precheck v4 adapter + incremental persistence | domain worker application | EVID-01, JOB-01, ISO-01 | lookup/cloaking parallel, no-email, review enqueue | legacy worker remains | todo |
| DW-02 | Batch worker/checkpoint/stop/retry/account scope | workers/domain | DW-01, DELIVERY-01 | input order, partial retry, no double count/send | auto-send off | todo |
| DW-03 | Domain Worker React | frontend domain-worker | DW-01, DW-02 | F5, one progress source, action links | feature flag | todo |
| CR-01 | Owned cloaking queue/dedupe/source history | application/cloaking | DW-01, DB-02 | owner/day/full URL, terminal preservation | no migrate source delete | todo |
| CR-02 | Three dispositions, evidence, direct send | application/cloaking review | CR-01, PREVIEW-01, DELIVERY-01 | 2–4 images, not-cloaking clean, partial retry | send disabled | todo |
| CR-03 | Cloaking Review React stable-ID table | frontend cloaking-review | CR-02 | no KPI/filter, SENT no action, stale guard | feature flag | todo |
| ER-01 | Evidence Review case merge/direct send | application/evidence review | DW-01, PREVIEW-01, DELIVERY-01 | 1–3 images, concurrent worker, source jobs update | send disabled | todo |
| ER-02 | Domain Evidence Review React | frontend evidence-review | ER-01 | thumbnails/exact plan/retry missing only | feature flag | todo |
| LINK-01 | Link Status API/core adapter | api/link_checks, core/links | ISO-01, JOB-01 | all status/redirect/WHOIS semantics | legacy function remains | todo |
| LINK-02 | Link Status React/export | frontend link-status | LINK-01 | group/order/two exports/F5 | feature flag | todo |
| PR-01 | Provider Replies owned mailbox/thread storage/API | core/mail/provider replies | MAIL-02, ART-01, DELIVERY-01 | explicit sync, folder errors, Unicode, thread headers | IMAP actions disabled | todo |
| PR-02 | Provider Replies React | frontend provider-replies | PR-01 | exact attachment pair, translation read-only | feature flag | todo |
| LT-01 | Lead Triage v24 owned snapshot/API/shared review | core/leads/application | DW-01, CR-02, ER-01 | target/backend caveats, sent lock, parity | legacy page remains | todo |
| LT-02 | Lead Triage React | frontend lead-triage | LT-01 | parse explicit/per-row/batch/exact preview | feature flag | todo |
| NAV-01 | Remove four routes; keep hidden core flags | frontend router/navigation | QR-02..LT-02 | direct routes 404/forbidden; CLI smoke | restore routes only via flag | todo |
| NET-01 | Egress deployment and mail endpoint policy | infrastructure/networking | PKG-01, EVID-01 | mail exact allowlist/public IP; worker isolation | deployment policy | partial: capture uses shared legacy defaults by request; network isolation and full legacy check pending |
| MIG-01 | Legacy inventory/dry-run/import/quarantine tool | backend migration CLI | DB-01, ART-01, DELIVERY-01 | synthetic idempotency/source precedence | dry-run default | todo |
| MIG-02 | Rehearsal + rollback preserving delivery truth | docs/migration + tests | MIG-01 | apply twice, rollback, no SENT reset | no real source mutation | todo |
| DOC-01 | README/CLAUDE/technical/API/operations/AGENTS/skill | docs/root files | each behavior task | docs match tested state | no claims ahead of code | todo |
| QA-01 | Full BE/FE/E2E/security/restart suite | all | NAV-01, MIG-02, NET-01 | all new suites green; baseline deltas documented | block release | todo |
| LOAD-01 | Mock multiuser load and fairness | deploy/tests | QA-01 | workload/limits measured per user | tune config; no live side effects | todo |
| REL-01 | Backup/restore, RC checklist, cutover runbook | deploy/docs | QA-01, LOAD-01 | restore isolated env, rollback ledger-safe | no production apply | todo |

Không có dependency tham chiếu tới task không tồn tại. Các task feature không
được đánh dấu done chỉ vì route/UI xuất hiện; phải có persistence, authz, parity
và recovery test tương ứng.

## 5. Schema và migration order

### 24 bảng nền tảng

1. Identity/RBAC: `users`, `auth_identities`, `sessions`, `roles`, `permissions`,
   `user_roles`, `role_permissions`.
2. Config: `user_settings`, `sender_accounts`.
3. URL/operation: `targets`, `checks`, `jobs`, `job_items`.
4. Review/send: `review_cases`, `case_sources`, `preview_plans`, `deliveries`,
   `delivery_attempts`.
5. Evidence: `evidence_sets`, `artifacts`.
6. Operations: `cloudflare_records`, `review_snapshots`, `audit_events`,
   `migration_runs`.

Migration order: enums/extensions-independent primitives → identity/catalog →
config → targets/checks/jobs → artifacts/evidence → review/preview/delivery →
Cloudflare/snapshot/audit/migration. UUID primary keys; UTC timestamptz; owned
entities có `owner_user_id`, version và timestamps. Event/side-effect rows có
`actor_user_id`. JSONB chỉ cho sanitized variable payload, không thay cột dùng
filter/constraint.

Owner constraints dùng composite unique/FK khi quan hệ owned; repository luôn
nhận context. RLS là lớp defense-in-depth sau khi test runtime role/pool reset,
không thay authorization. Runtime role không superuser/BYPASSRLS.

Các index/unique quan trọng:

- target: owner + identity_version + canonical full URL identity;
- review: owner + case_type + business_day + canonical full URL key;
- job item: job + item identity; giữ input index;
- delivery: owner + sender account + workflow business key;
- form/idempotency receipt: owner + action + key, kèm request hash;
- auth identity: issuer + subject; role/permission key/code unique.

Preview body/MIME plan lưu dưới draft artifact private; DB giữ reference,
fingerprint, revision, expiry, account/config versions, recipients và approval.
Form task cần durable metadata/capability hash/expiry/one-time state nhưng không
secret payload trong URL/log. Integration/mail secrets nằm encrypted fields hoặc
secret reference trong config tables; không nằm jobs/snapshots.

## 6. API/permission contract plan

Prefix `/api/v1`; OpenAPI sinh TypeScript client. Mọi lỗi có `code`, `message`,
`field_errors`, `request_id`; không raw exception/path/config. Long operation trả
202 + ID. Owned resource ngoài scope trả 404 khi phù hợp; thiếu action 403;
idempotency conflict/stale preview 409.

| Nhóm | Routes chính | Permission/gate |
|---|---|---|
| Auth | `/auth/telegram/start|callback`, `/me`, `/auth/logout` | state/PKCE/session/CSRF |
| Admin | `/users`, `/roles`, `/permissions` | users/roles action + last-admin transaction |
| Mail | `/mail-accounts`, test SMTP/IMAP, `/settings/*` | own view/manage/test; secret masked |
| Parse | `/targets/parse` | feature view; no network/write target |
| Quick | `/quick-reports/checks/*`, form-task, netcraft, export | view/check/open/submit/export/clear |
| Domain | `/domain-checks/*`, evidence, preview | view/check/VT/capture/upload/preview/send |
| Worker | `/domain-jobs/*` | view/precheck/start/stop/retry/clear |
| Reviews | `/cloaking-cases/*`, `/evidence-review-cases/*`, `/previews/*` | view/upload/approve/preview/send/skip |
| Cloudflare | `/cloudflare/prechecks|jobs|session/*` | view/precheck/start/resume/stop/retry |
| Links | `/link-checks/*` | view/check/export |
| Later | `/provider-replies/*`, `/lead-triage/*` | independent sync/check/preview/send |
| Artifact/cache | `/artifacts/{id}`, `/cache/clear` | owner + feature/action |

Mỗi side effect có permission, explicit action/confirmation, durable intent/claim,
idempotency key và recovery state. SMTP uncertain không tự retry. Cookie route tắt
body logging, nhận handoff authenticated và chỉ trả session handle.

## 7. Kế hoạch UI

Routes: `/login`, `/pending`, `/quick-report`, `/check-domain`,
`/cloudflare-worker`, `/domain-worker`, `/cloaking-review`,
`/domain-evidence-review`, `/link-status`, `/provider-replies`, `/lead-triage`,
`/settings/mail`, `/admin/users`, `/admin/roles`.

`AppShell`, `PermissionGate`, `RawTargetInput`, `TargetTable`, `JobProgress`,
`AccountSelector`, `EvidenceGallery/Uploader`, `ReportPreview`, `DeliveryLedger`,
`ConfirmSendDialog`, `CopyButton`, `ErrorNotice` là component dùng chung.
Server state theo owner-aware query key; logout hủy request, revoke blob URL và
clear cache. Poll 2–3 giây chỉ khi active, giảm ở hidden tab, luôn refetch snapshot
khi reconnect. Editable form không bị query overwrite; stale detail response có
resource/revision guard.

Mỗi page có empty/loading/error/partial/success, F5 recovery, 403 explanation,
keyboard/focus/ARIA, 1440/1024/390 px. Evidence không auto-open URL. Extension
flow hiển thị installed/disconnected/version/task status và manual fallback;
acknowledgement mới được báo autofill thành công.

## 8. Dữ liệu, migration và cutover

- Tool import local có `--dry-run` mặc định, source root explicit, legacy owner
  explicit, checkpoint/fingerprint/version, quarantine record không map được.
- Dùng precedence `_runtime_path()` và phát hiện cả root/data; không merge mù.
- Synthetic fixtures phủ SENT/PARTIAL/UNKNOWN/no-email/review/evidence mismatch,
  không copy runtime thật/secret.
- Apply idempotent, không sửa source, validate path/hash. `config.ini` chỉ qua CLI
  local, không upload API/print secret.
- Cutover từng scope sau khi dừng legacy writer. Rollback giữ DB delivery truth và
  reconcile; không quay về snapshot CSV để gửi lại.
- Dev/test DB và artifact root riêng. Không apply migration/runtime import thật
  trong đợt phát triển này khi chưa có yêu cầu vận hành riêng.

## 9. Kế hoạch test và security

- Legacy: characterization + full unittest, so exact subject/body/hash/bytes với
  clock/seed/config fixture; intentional diffs được whitelist rõ.
- BE: unit/domain, API authz, PostgreSQL integration, Redis restart, worker/process
  restart, artifact mutation, SMTP crash windows, Cloudflare response fixtures.
- A/B: cùng URL/ngày, direct/nested foreign IDs, cache/clear/stop/retry, sender,
  artifact/export/download/subscription; connection pool/thread reuse A→B.
- Concurrent: last admin, two preview sends, two job claims, worker vs review,
  snapshot revision, stop/retry, disabled sender/role mid-job.
- FE: Vitest/RTL, generated client contract, Playwright E2E login-pending-activate,
  seven priority pages, later pages, F5/logout/stale response/responsive/keyboard.
- Extension: wrong origin/owner/provider, expired/replay task, refresh non-consume,
  eight adapter fixture tests, no submit/CAPTCHA/arbitrary proxy.
- Network: loopback/private/link-local/metadata/IPv6/redirect/rebinding/subresource;
  mail egress allowlist policy separate.
- Load definition trước chạy: mặc định rehearsal 20 concurrent users × 25 mock
  URLs, tối đa 2 mock jobs/user, 10 phút; đo p50/p95 API, queue wait/error theo
  user, starvation và pool saturation. Ngưỡng ban đầu: không cross-owner/error
  mất dữ liệu, <1% unexpected error, p95 read API <500 ms ở máy test; điều chỉnh
  sau khi ghi CPU/RAM/DB pool. Không gọi provider thật.

## 10. Triển khai, vận hành và bằng chứng tiến độ

Topology dev/small deploy: reverse proxy HTTPS cùng origin → static FE + API;
PostgreSQL, Redis, private artifact volume; process API/general worker/capture
worker/Cloudflare session worker tách. Health: liveness không phụ thuộc provider,
readiness kiểm DB và dependency nội bộ. Observability dùng request/operation ID,
allowlist metadata; URL token/mail/secret/Cookie/body không vào log/metric label.
Backup DB + artifact nhất quán, restore ở environment tách trước RC.

| Ngày | Task | Bằng chứng / quyết định | Bước tiếp theo |
|---|---|---|---|
| 2026-10-15 | M0-01 | đọc toàn bộ `.agents`; baseline 415 / 4F / 7E; inventory globals/config mẫu | hoàn tất M0-02..04, validate traceability |
| 2026-10-15 | M0-01..04 | 50 task ID, không dependency treo; parity đủ 9 workflow + 4 menu bỏ; `git diff --check` sạch | PKG-01 rồi DB-01 |
| 2026-10-15 | PKG-01, DB-01 | 24-table metadata; PostgreSQL 17 upgrade/check/downgrade sạch; 4 foundation tests | DB-02/CTX-01 |
| 2026-10-15 | FE-01 | npm lock 0 vulnerability; typecheck/lint/2 Vitest/build đạt | giữ feature routes đóng đến auth/API |
| 2026-10-15 | SEC-01 | AES-GCM owner-bound, key version/rotation và redacted repr test đạt | dùng trong MAIL-01 |
| 2026-10-15 | DB-02/AUTH-01/RBAC-01 | A/B repository + composite FK đạt trên PG; OIDC PKCE/state/browser/nonce/JWT, pending session, `/me`/logout primitives, authz invalidation và idempotent catalog seed đạt; admin APIs/Redis route integration còn làm | ADMIN-01 và auth negative API suite |
| 2026-10-15 | Quality gate hiện tại | backend 16 test đạt (5 DB integration skip khi không có URL); DB run gần nhất 15/15; FE typecheck/lint/2 test/build đạt; compileall, pip check, diff check đạt | tiếp tục ADMIN-01, MAIL-01, CACHE/ART/ISO |
| 2026-10-15 | RBAC-01, ADMIN-01 | API user/status/role/revoke-session và catalog role CRUD có permission + CSRF; system role được bảo vệ; audit allowlist; bootstrap theo Telegram subject; PostgreSQL 21/21 gồm concurrent last-admin; React user/role pages dùng API thật | MAIL-01 rồi CACHE-01/ART-01/ISO-01 |
| 2026-10-15 | Quality gate sau ADMIN-01 | backend 21 test đạt trên PostgreSQL tạm; backend không DB 12 đạt + 9 skip; FE typecheck/lint/4 Vitest/build đạt; full legacy giữ đúng baseline 415 test / 4 failure / 7 error; không gọi Telegram/SMTP/IMAP/provider thật | triển khai mail settings bằng secret envelope |
| 2026-10-15 | MAIL-01 | migration 0002 thêm secret metadata + unique default/account owner; API profile/SMTP/IMAP/proxy/signature không trả secret; PATCH bỏ secret giữ ciphertext; React page dùng API thật | MAIL-02 mock connection tests, CACHE-01/ART-01 |
| 2026-10-15 | CTX-01 | immutable actor/owner/operation context; auth dependency lấy actor từ opaque session; admin resource ID và mail owner không nhận từ body; negative owner tests đạt | mở rộng cùng pattern cho mọi feature ở ISO-01 |
| 2026-10-15 | Quality gate sau MAIL-01 | PostgreSQL 24/24, Alembic upgrade/check/downgrade/upgrade đạt; A/B cùng sender độc lập; ciphertext owner-bound; FE strict gate và 5 component test | tiếp tục không mở kết nối mail cho đến action MAIL-02 explicit |
| 2026-10-15 | MAIL-02 | POST test SMTP/IMAP yêu cầu `mail_accounts.test_own` + CSRF; SMTP chỉ TLS/login, IMAP chỉ TLS/login/logout; mock chứng minh không send/read mailbox; lỗi sanitize và audit metadata allowlist | CACHE-01; NET-01 vẫn chặn production egress |
| 2026-10-15 | Quality gate sau MAIL-02 | PostgreSQL tạm 31/31; backend không DB 18 đạt + 13 skip; FE typecheck/lint/6 Vitest/build đạt; không mở kết nối mail thật | CACHE-01 rồi ART-01/ISO-01 |
| 2026-10-15 | CACHE-01 | Redis key scope owner/feature/day/bucket/version + SHA-256 identity; TTL đúng local midnight/DST; get/set fail-open, clear lỗi trả 503; clear dùng SCAN/UNLINK và permission theo feature | ART-01 rồi ISO-01 |
| 2026-10-15 | Quality gate sau CACHE-01 | PostgreSQL 17 + Redis 7 tạm: 39/39, Alembic check sạch; Redis A/B, flush và restart không persistence đạt; legacy giữ baseline 415 test / 4 failure / 7 error; container test đã xóa | ART-01 |
| 2026-10-15 | ART-01 | Port + filesystem adapter private; key chỉ từ UUID server, PNG/JPEG được decode và kiểm MIME/signature/size/dimension/hash trước khi ghi; batch staging rollback toàn bộ; download kiểm owner + permission feature + integrity, không trả storage path/tên gốc | ISO-01 rồi JOB-01/PREVIEW-01 |
| 2026-10-15 | Quality gate sau ART-01 | 17/17 test tập trung; PostgreSQL 17 tạm chạy 48 test: 47 đạt + 1 Redis integration skip, Alembic check sạch; A/B artifact trả 404, sai permission trả 403, mutation/hash/path traversal/partial commit bị chặn; legacy giữ baseline 415 test / 4 failure / 7 error; container fixture đã xóa | ISO-01 |
| 2026-10-15 | DB-02 | OwnedRepository cố định owner khi khởi tạo; get/list/count/update/delete không nhận owner override; UoW commit/rollback; job claim `SKIP LOCKED`, reclaim lease hết hạn và heartbeat atomic bằng token + expected version | ISO-01 |
| 2026-10-15 | ISO-01 | A/B cùng URL/account độc lập; API account/artifact ngoài owner trả 404; schema test bắt mọi owned-to-owned FK mang owner; pool size 1 tái dùng cùng backend connection không rò dữ liệu; stop job/cache/artifact đã scope theo owner | JOB-01 rồi PREVIEW-01 |
| 2026-10-15 | Quality gate sau DB-02/ISO-01 | PostgreSQL 17 + Redis 7 fixture: backend 57/57; race claim, lease, UoW, API 404, pool reuse, nested FK đạt; Alembic check, compileall, pip check sạch; không đổi legacy module nên giữ kết quả baseline gần nhất 415 test / 4 failure / 7 error | JOB-01 |
| 2026-10-15 | JOB-01 | Job service tạo membership có thứ tự, snapshot account scope, stop/resume và retry đúng failed subset; worker start/checkpoint từng item bằng lease token + optimistic version; lease chết giữa item hoặc stop được phục hồi thành `UNKNOWN`, không tự retry | PREVIEW-01 |
| 2026-10-15 | Quality gate sau JOB-01 | 12/12 test job tập trung và backend PostgreSQL 17 + Redis 7 đạt 64/64; Alembic check, compileall, pip check sạch; legacy giữ đúng baseline 415 test / 4 failure / 7 error; không chạy side effect thật | PREVIEW-01 |
| 2026-10-15 | PREVIEW-01 | Exact delivery payload nằm trong JSON artifact private; DB giữ revision/fingerprint/expiry/account+profile version/recipient/artifact refs. Advisory lock cấp revision; approve và send-boundary validation chặn revision cũ, account/profile/evidence đổi, artifact mutation/expiry và cross-owner reference | DELIVERY-01 |
| 2026-10-15 | Quality gate sau PREVIEW-01 | 11/11 test artifact/preview tập trung; PostgreSQL 16 + Redis 7 backend 69/69; Alembic check xác nhận schema 0001/0002 đã đủ, không cần migration mới; compileall, pip check, diff check đạt; legacy giữ baseline 415 test / 4 failure / 7 error | DELIVERY-01 |
| 2026-10-15 | DELIVERY-01 | Server business identity tạo delivery key không phụ thuộc preview/hash; stable Message-ID; row claim + durable intent trước side effect; checkpoint SENT/FAILED/UNKNOWN; retry chỉ FAILED; expired intent recovery thành UNKNOWN. Preview mới chỉ rebind pending/failed. Refresh row sau `FOR UPDATE` sửa race identity-map giữa hai session | QR-01 |
| 2026-10-15 | Quality gate sau DELIVERY-01 | PostgreSQL 17 + Redis 7 backend 76/76, gồm hai session claim, owner isolation, revision rebind, stable Message-ID, failed retry và crash recovery; schema 0001/0002 đã đủ, Alembic check/compileall/pip check/diff check đạt; legacy giữ đúng baseline 415 test / 4 failure / 7 error; không gọi SMTP/IMAP/provider/capture thật | QR-01 |
| 2026-10-15 | QR-01 | `POST/GET /quick-reports/checks` tạo/đọc operation owned; target/check/job/item bền vững giữ full URL và input order; worker process gọi compatibility `run_cdn_check`, checkpoint từng item, result sanitize; Redis cache owner/day/engine ghi provenance và không thay DB history | QR-02, EXT-01, CD-01 |
| 2026-10-15 | Quality gate sau QR-01 | 6 test Quick Report gồm OpenAPI, canonical full URL/workflow key, compatibility call boundary, sanitizer, durable worker/order/duplicate và A/B daily cache; backend PostgreSQL + Redis 82/82; Alembic check/compileall/pip check/diff check đạt; legacy giữ baseline 415 test / 4 failure / 7 error; không gọi network/provider thật | QR-02 và EXT-01 |
| 2026-10-15 | FE-02 | Source tách `app/components/features/lib`; Axios 1.20.0 là HTTP boundary duy nhất, cùng-origin credential + CSRF interceptor + error DTO; auth/query composition tách khỏi page; admin/mail routes được chuyển vào feature folders | tiếp tục dùng pattern này cho mọi page mới |
| 2026-10-15 | QR-02 (in progress) | React `/quick-report` có lọc local explicit, permission view/check, POST operation thật, polling terminal-aware, result cards DTO và session recovery key theo user; logout clear state. Form-task/Netcraft/nic.top chưa có API nên khóa rõ, không giả lập | EXT-01/02 và API action/export để hoàn tất QR-02 |
| 2026-10-15 | Quality gate FE-02/QR-02 | frontend strict typecheck + ESLint + 10 Vitest + production build đạt; test Axios CSRF/credential/error, filter không tự request, DTO render và F5 không POST lại; npm audit 0 vulnerability | chạy compileall/diff check rồi EXT-01 |
| 2026-10-15 | EXT-01 | API đăng ký extension device theo owner, tạo capability task từ check Quick Report đã hoàn tất, exchange một lần và đổi sang status token; extension v3 kiểm exact app origin, provider origin và chỉ giữ payload/token trong `chrome.storage.session`. React gọi API bằng Axios, poll status và có manual fallback; Google/Microsoft luôn `fill_only`. Capability/token không nằm trong provider URL, DB hoặc localStorage | EXT-02 chuyển sáu adapter còn lại; QR-02 còn Netcraft/nic.top |
| 2026-10-15 | Quality gate EXT-01 | PostgreSQL 17 + Redis 7 cô lập: backend 87/87; form-task/extension tập trung 15/15; frontend 11/11 + typecheck/lint/build; syntax check ba JavaScript extension đạt. Replay, wrong device, wrong web origin, expiry, owner read và manual fallback dùng fixture/mock, không mở form hoặc submit thật. Legacy giữ baseline 415 test / 4 failure / 7 error | EXT-02 |
| 2026-10-15 | AUTH-02 | Sửa Telegram token exchange đúng HTTP Basic theo contract chính thức; dev callback/cookie cùng origin `127.0.0.1:5173`; settings đọc `.env` root nhưng test có thể tắt; status API không secret, callback denial/failure quay về login với mã allowlist | cần Client ID/Secret + Allowed URL thật để live verify; không tự gọi login live |
| 2026-10-15 | Quality gate AUTH-02/UI-02 | Backend PostgreSQL 17 + Redis 7 đạt 90/90; frontend 12/12 + typecheck/lint/build; compileall/pip check/diff check đạt. Playwright local screenshot đã kiểm ở 1440×1000 và 390×844. Legacy giữ baseline 415 test / 4 failure / 7 error; không gọi Telegram thật | live OIDC cần operator cung cấp Client ID/Secret và Allowed URL |
| 2026-10-15 | UI-02 → UI-03 | Thay forced-dark bằng light/dark dùng Radix Themes 3.3.0; preference lưu browser, boot trước render, Tailwind class dark; shared button/dialog/theme toggle và semantic palette cho form/table/alert; route lazy loading. FE 16/16 + typecheck/lint/build; Chrome desktop/mobile 390×844, dialog mail/role, Esc/return focus và reload preference đã kiểm | Tiếp tục QR-02; không đổi backend policy; CLAUDE.md không đọc/cập nhật theo chỉ dẫn cũ |
| 2026-10-15 | UI-04 | Modern indigo–violet visual system: primary/active nav gradient, header/card depth, icon tiles/avatar, Quick Report input groups/action bar; hover/focus/disabled/reduced motion. FE 16/16 + typecheck/lint/build; Chrome desktop/mobile 390×844 light/dark không tràn ngang; chỉ lọc fixture cục bộ | Giữ API/business policy, tiếp tục QR-02 |
| 2026-10-15 | OPS-02 | Chuyển nguồn cấu hình FastAPI từ root sang `backend/.env`; mẫu chuyển thành `backend/.env.example`; Compose lấy password PostgreSQL từ cùng backend env, còn artifact mặc định nằm trong `backend/.runtime`. Frontend không nhận secret backend | focused settings test đạt; backend unit 91 test đạt/45 DB skips; compileall, pip check, Compose config và diff check đạt |
| 2026-10-15 | OPS-03 | SQLAlchemy 2.1 đổi sang extra `sqlalchemy[asyncio]` để cài `greenlet` theo contract upstream | cài editable trong Python 3.14 venv thành công (`greenlet 3.5.6`); PostgreSQL/Redis healthy; Alembic nâng `0001` → `0002` và báo head; pip check đạt |
| 2026-10-15 | AUTH-01/RBAC-02 | Live Telegram callback tạo đúng một user pending; thêm deploy command `sync-rbac`, chạy lặp vẫn giữ 2 system roles/56 permissions/36 bindings. Bootstrap đúng stable subject kích hoạt user và gán `{admin,user}`; test app factory nhận Settings cô lập để backend tests không đọc `backend/.env` thật | logout/login lại để nhận authz version mới; tiếp tục QR-02 rồi EXT-02 |
| 2026-10-15 | RBAC-03 hoàn tất, cập nhật seed user | Admin 56/56; user mới mặc định 51 quyền nghiệp vụ trừ users/roles theo yêu cầu mới; sync giữ lựa chọn, reset explicit thu hồi phiên thành viên. DB dev đã xác minh 51 quyền. Backend PostgreSQL/Redis riêng 92/92; compileall/pip check đạt. Legacy 415 test giữ baseline 4 failure + 7 error | Tiếp tục QR-02 rồi EXT-02; CLAUDE.md không đọc/cập nhật theo chỉ dẫn phiên trước |

| 2026-10-16 | QR-02 / EXT-02 | Exact Netcraft approval, durable intent ledger migration 0004, owner/day/full URL dedupe, FAILED explicit retry, UNKNOWN locked; nic.top literal XLSX; eight provider form tasks, fill-only | Next: CD-01 / Check Domain evidence |
| 2026-10-16 | Validation | PostgreSQL 17 + Redis 7 backend 104/104; provider-focused 6/6; FE 17/17 + typecheck/lint/build; legacy baseline 415 tests / 4 failures / 7 errors unchanged; compileall/pip check; dev migration and Alembic check clean | No live provider/SMTP/IMAP; Netcraft disabled by default; CLAUDE.md excluded per prior instruction |

## 11. Câu hỏi mở không chặn

Compatibility import fix 2026-10-16: reproduced completed operation GET 500
from backend cwd (ModuleNotFoundError phishing_toolkit). Hatch dev-mode-dirs
and explicit wheel force-include package four compatibility modules without
secret config/runtime. Reinstalled dev backend and reloaded API; existing six
results and draft preview verified in Chrome, no new probes. Regression uses
subprocess outside repo and isolated extracted wheel with .pth disabled. Full
backend from backend cwd 119/119; compileall/pip check/diff check clean. No
Python business logic changed, so legacy results remain prior baseline 415 /
4F / 7E. CLAUDE.md unchanged per prior instruction. Packaging validates wheel
from repository checkout; this does not complete production deployment gates.

Registrar/registry parity audit 2026-10-16: direct legacy table routing (23
registrar forms / 76 registry entries / 48 registrar email rules), manual form
links plus owned draft preview/copy, XYZ family and CentralNic com.co adapter
correction. Legacy Quick Report only displays email contacts; SMTP belongs to
other workflows. Regression tests cover all tables, no routing network, full
URL/owner draft, cross-owner 404/permission 403 and frontend manual form preview.
Backend isolated 117/117; FE 33/33 + typecheck/lint/build; legacy baseline 415 /
4F / 7E; compileall/pip check clean. No live provider form or mail action.
Detailed scope and operator tests: docs/quick-report-routing-audit.md. Prior
done markers do not mean full web migration or production gates are complete.

Quick Report worker operation 2026-10-16: diagnosed queued/pending with no worker
process and missing legacy dependencies in backend venv. Added package dependencies,
preflight before claim, heartbeat during slow probe and Windows worker launcher.
Started dev worker, recovered only selected operation's four ModuleNotFoundError
items using JobService retry; all four now completed with persisted results. No
SMTP/IMAP/provider submission. User's new input kept intact. Backend isolated
112/112, FE 32/32 + typecheck/lint/build, compileall/pip check; legacy baseline
415 / 4F / 7E unchanged. Legacy packaging/evidence migration gates remain; this
does not declare full Check Domain/evidence workflow complete. CLAUDE.md unchanged.

Handshake correction 2026-10-16: normalize legacy `Error:` prefix; external
worker serializes error.message. Runtime missing receiving end/channel closed
have distinct guidance. Local Chrome retry verified current extension responds
with origin_not_allowed; operator must save exact origin in new extension ID's
Options. FE 31/31, lint/build; legacy 415 retains 4F/7E baseline; JS syntax and
compileall pass. No forms/submissions or permission changes performed.

Extension connectivity 2026-10-16: env public ID validated with Vite and browser
read-only probe confirmed missing runtime messaging API. FE separates missing
ID/invalid ID/browser API/origin rejection/timeout, retry loading+toast and stale
ping sequence guard. FE 30/30 + lint/build, compileall/diff check; local Chrome
verified diagnostic and retry feedback. No provider form opened. Chrome internal
extension management is blocked by browser tool URL policy; operator must verify
the installed v3 folder/ID and reload it. CLAUDE.md unchanged per prior request.

Mail save diagnosis 2026-10-16: dev không có account, thiếu encryption key;
khởi tạo key cục bộ không in giá trị, AES-GCM roundtrip đạt và dev reload.
API tách missing key 503, duplicate identity 409 và connection preconditions;
chỉ allowlist message, không SQL/credential. Backend PostgreSQL/Redis 110/110,
legacy baseline 415 / 4F / 7E; compileall/pip check đạt. Không lưu mail người
dùng, SMTP/IMAP hoặc provider thật. CLAUDE.md giữ nguyên theo chỉ dẫn cũ.

FE feedback 2026-10-16: cài Sonner 2.0.8, shared MutationCache notification,
themed/dismissible toaster, explicit confirmation cho thao tác admin/mail,
pending buttons, read-error retry, offline banner và render recovery screen.
Quick Report có progress và terminal transition toast, không toast polling/F5.
Logout chỉ clear sau success; /me network failure có retry. Frontend 24/24,
typecheck/lint/build và audit 0 vulnerability; compileall/pip check đạt. Chỉ FE,
không chạy lại backend/legacy suite vì không đổi nghiệp vụ hoặc Python source.
Chrome automation mất kết nối, chưa visual verify live. CLAUDE.md giữ nguyên.

Profile gate 2026-10-16: Quick Report chỉ mở workflow sau readiness hồ sơ owner
(brand_name, contact_name, valid contact_email); API check/task/preview/submit/
export enforce độc lập và trả 409 khi thiếu. Backend PostgreSQL/Redis cô lập
106/106; frontend 18/18, typecheck/lint/build; compileall đạt. Full legacy giữ
baseline 415 test / 4 failures / 7 errors. Không gọi provider/SMTP/IMAP thật;
không cần migration mới. CLAUDE.md không đọc/cập nhật theo chỉ dẫn phiên trước.

- Production domain, Telegram client ID/secret và bootstrap subject chưa có: dùng
  placeholder + mock issuer; cần trước deploy/live-login, không trước M1 code.
- Tải/retention/SLA/server resource chưa có: giữ cấu hình, đo workload mục 9,
  không tuyên bố scale production trước LOAD-01.
- Object storage S3 endpoint chưa chốt: filesystem private là adapter đầu tiên;
  port không khóa thiết kế.
- RLS activation phụ thuộc test pool/runtime role; owner filter + composite FK là
  bắt buộc ngay cả khi RLS chưa bật.



Quick Report UI refinement 2026-10-16: compact infrastructure/contact rows,
hide Cloaking HTTP display, ordered provider toolbar, one extension action per
supported form and channel-specific manual draft dialogs. FE regression checks
cover button ordering, GoDaddy provider selection, no duplicate links, offline
locking and registrar/registry draft separation. No backend behavior change.

2026-10-16 follow-up: three aligned report groups with inline draft comparison,
explicit autofill labels and compact shared infrastructure/contact row. Optional
include_providers query extends owned read-only draft endpoint; no DB migration,
new network action or dependency. Microsoft has no text field; Netcraft retains
exact approval preview. Frontend and backend regression validation included.

2026-10-16 dense UI follow-up (supersedes prior layout): full-width general-provider
block followed by CDN/registrar/registry columns; direct manual-form links, inline
drafts and reduced spacing. Netcraft uses one explicit click to create server exact
preview and submit, no confirmation modal. Backend durable ledger and send gates
remain unchanged; mock-only tests verify no auto-send and no duplicate click send.

2026-10-16: hide unavailable routing groups before draft loading; responsive columns
fit only present groups. Highlight report actions in blue. Resolve local Netcraft
submit-disabled error by explicit backend opt-in and process restart; default
example remains false. Validate by GET status and mocked send tests, no live submit.

2026-10-16: nic.top XLSX export removed from Quick Report frontend at operator
request. Backend endpoint retained for compatibility; no workflow dependency on
this export. Normal registry actions and check results unchanged.

2026-10-16 cache UX completed: cache/network/unresolved counts, cached results
hidden by default with toggle and all-cache guidance, owner/day clear action via
existing permission/CSRF API. Clear preserves durable results and requires explicit
new operation to probe again. FE fixtures cover 20 cached + 20 fresh, toggle no
POST, clear exact feature payload, permission, busy/error and all-cache behavior.

2026-10-16 correction to cache UX: always-visible compact input and persistent daily
history via owned GET quick-reports/daily. Latest unique workflow URL rows come from
PostgreSQL, survive new batches/refresh/cache clear, default show old and new. Counts
separate historical rows from network checks in latest operation; actual Redis hit
badge unchanged. DB A/B/day/latest/permission tests and FE 10+2/F5 scenario added.
This supersedes operation-only counts and default hiding in earlier cache UX notes.

2026-10-16: distinguish orphan historical pending/running checks as not_queued in
daily DTO via owner-scoped job membership. No automatic requeue or history write.
UI replaces indefinite waiting with missing-job explanation and re-entry guidance.
Regression covers orphan versus linked pending and read-only projection.

2026-10-16: daily history retained but old cards default hidden on each new batch.
Cache-clear success resets visible operation/session pointer; error does not.
Operator flow 10+10 -> only latest10 -> clear empty -> recheck20 shows20 is covered
by mock FE integration test. No backend mutation/API change beyond existing clear.

2026-10-16: fix daily/current merge ordering in React. Current operation input order
sets card order and numbering; historical-only entries follow. Sequential worker
behavior unchanged. Regression verifies reversed daily response cannot reorder it.

2026-10-16 AUTH/UI refinement: dedicated /login; authenticated dashboard at /;
protected route 401 redirect; authenticated login redirect; logout to login;
callback success to / and callback errors to login. Pending gate and RBAC retained.
New responsive cyan/blue Telegram card with light/dark themes. Router and callback
regressions cover anonymous deep links, dashboard permissions, pending, network
outage and safe error codes. No live Telegram login, SMTP or report during tests.

2026-10-16 CD-01: owned POST/GET Check Domain operations preserve full URL;
durable `check_domain` worker runs shared `run_check` without legacy
CSV/draft/evidence writes or Redis reuse. DTO whitelists investigation sources;
VirusTotal submit is explicit with separate permission and backend key.
Backend 71 passed/59 skipped (isolated PostgreSQL unavailable), focused 7 passed/
1 skipped; legacy 415 retains 4 failures/7 errors baseline. Compileall and pip
check passed. No real URL probe, mail, provider submit or Browser Evidence.

2026-10-16 CD-01 single-target correction: POST accepts one `target` string,
response projects one `check`, and service creates exactly one job item. Quick
Report and Domain Worker remain multi-URL workflows. No live check or send.

2026-10-16 EVID-01: owned manual upload (1–3 PNG/JPEG), evidence set DTO and
existing integrity-checked artifact download. Browser capture is a separate
durable job that validates legacy screenshots/manifest in isolated staging and
stores only allowlisted metadata plus immutable artifact hashes. Initial host
A/AAAA addresses are checked for public IPs; redirect/DNS rebinding still require
NET-01 egress policy, so capture flag remains off. Upload remains available.
Backend 75 passed/60 skipped (isolated PostgreSQL unavailable), legacy 415 retained 4
failures/7 errors baseline; compileall/pip check passed. No real URL probe.

2026-10-16 NET-01 app boundary: per-hop HTTP probe with optional proxy,
Playwright route guard checks every outbound request, public A/AAAA rechecked
per URL, private IPv4/IPv6 and unsafe ports denied. Capture requires an explicit
enable flag and stays disabled by default. Direct mode needs network isolation against
DNS rebinding; an optional proxy must pin destination IP at connect.
Mail connectivity test is disabled by default; when enabled, exact host:port
allowlist and public DNS checks apply, with external firewall still required.
Full legacy Check Domain worker egress and deployed network isolation validation
remain open. Backend 83 passed/60 skipped; legacy 415 kept 4 failures/7 errors.

2026-10-16 Browser capture direct mode: removed mandatory proxy configuration
from capture API and dedicated worker; retains public URL checks per HTTP hop and
Playwright request. Optional proxy remains supported. No live suspicious URL was
opened. App DNS checks cannot close the connect-time rebinding window.

2026-10-16 CD-02 correction: React `/domain-check` mirrors legacy one-target
input and per-source result, stores owned operation ID per user for reload, and
shows read-only report form guidance built from legacy pure formatters. Removed
capture/upload controls from this page after clarifying that evidence is optional
and no report send is in scope. Browser capture backend remains a separate optional
endpoint; it is headless on the server and does not open local Chrome. No live
target check, capture, SMTP or provider submission. FE 59/59, backend 87 passed/
60 skipped; typecheck/lint/build/compileall passed. Formatter smoke generated
seven guidance entries without network access.

2026-10-16 CD-02 registrar/UI correction: separated registrar recipient lookup
from optional legacy draft file generation so web checks keep recipient metadata.
Read-only guidance adds an email draft only for a valid registrar recipient,
including To/Subject, when no registrar-only webform takes priority. The email
uses the legacy domain + UTC day variant pool; webforms keep their own variant
pool. React presents results
directly in smaller type and groups guidance into browser, CDN, Registrar and
Registry; long draft text expands on demand. No SMTP or provider submission.
Focused backend guidance 10 passed/1 skipped; real formatter smoke passed;
legacy full suite remained at 415 tests with the known 4 failures/7 errors.

2026-10-16 CD-02 routing correction: registry report guidance now uses the
checked `registry_contact`, with separate legacy webform and email formatters.
Both channels are shown when both contact methods exist; registrar email and
webform likewise keep their distinct legacy variant pools. No external send.
