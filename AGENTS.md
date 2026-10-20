# Domain Check Tool — hướng dẫn dự án

## Phạm vi

Bản được hỗ trợ chỉ gồm backend FastAPI/worker và frontend React. Người dùng đã xác nhận các tính năng cần thiết đều có trong web; ứng dụng Streamlit/desktop/CLI cũ và chức năng chỉ có ở đó đã được gỡ. Không giữ hoặc khôi phục một bản ứng dụng cũ song song.

## Bản đồ

- backend/src/domaincheck/{api,application,core,domain,infrastructure,workers}: web và worker.
- backend/src/domaincheck/engine/: 7 engine chia sẻ được backend sử dụng; import đầy đủ namespace, không bare import/root wrapper/compat.
- backend/tests/: toàn bộ unittest hiện tại, gồm characterization Browser Evidence/cloaking và fixture nội dung mail.
- backend/migrations/, backend/scripts/, backend/run_worker.bat, backend/compose.yaml: vận hành.
- frontend/src/, frontend/tests/, frontend/extensions/: React và Web Form Assistant; Vite tạo ZIP.
- docs/technical-guide.md và docs/testing/: vận hành và bằng chứng kiểm thử.
- config.ini/data/runtime/build/dist cũ local là dữ liệu giữ nguyên, không phải source hay cấu hình của web.

## Lệnh

Từ backend:
```powershell
.venv/Scripts/python.exe -m pip install -e ".[test]"
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe -m pip check
```

Từ frontend: npm ci; npm test -- --maxWorkers=2; npm run build; npm run lint.
Từ root: python -m compileall -q backend/src backend/tests.
Hạ tầng: docker compose -f backend/compose.yaml up -d.
Worker: backend/run_worker.bat --job-type all; runner Cloudflare: backend/scripts/run_cloudflare_worker.bat.

## Quy tắc

- config.ini là secret giữ nguyên tại chỗ: không đọc giá trị để hiển thị, sửa, commit, copy vào artifact hoặc upload. Web dùng backend/.env và cấu hình mail owned trong DB. Không xóa/chuyển data, reports, logs, artifact hay Chrome profile ngoài phạm vi explicit.
- Không gửi email/report, truy cập IMAP hoặc probe URL nghi vấn thật nếu chưa được yêu cầu; test bằng mock/dry-run/fixture.
- Chỉ mục đích phòng thủ; không thêm khai thác, thu thập credential hay né tránh.
- Mọi request có timeout; API tin cậy dùng TLS verification.
- Auth/RBAC/CSRF và owner/day/account scope giữ tại backend; FE guard bổ sung trải nghiệm, không thay kiểm tra server.
- Không đổi engine/policy version đang bind dữ liệu lịch sử khi chỉ di chuyển code.
- Engine không đọc config.ini hoặc tự tạo/migrate JSON/CSV runtime. Không tái thêm root import fallback.
- SMTP dùng exact preview/evidence/profile, intent/checkpoint và ledger; UNKNOWN/sent không resend mù. Sent copy không được retry SMTP.
- Cloaking phải HTTP đa profile trước, capture thụ động; case cần duyệt không gửi tự động. Normal Browser Evidence và cloaking evidence tách biệt.
- Extension nằm trong FE; bản sao chrome_extension/ ignored có thể giữ để profile đang cài không đổi ID. Đây là bản sinh của extension hiện tại, không source/app cũ.
- URLScan không được sử dụng. Draft tiếng Anh; không leak body/token/Cookie/password trong log/audit.
- Seed đăng ký catalog dưới lock, không reset/grant quyền cũ trừ explicit rollout. Đổi quyền giữ phiên hợp lệ, không hồi sinh phiên đã revoke/expire.
- Khi làm nghiệp vụ check/report/SMTP/IMAP/evidence/worker, đọc .agents/skills/phishing-takedown-tool/SKILL.md.
- Không khởi chạy/restart worker đang có job để kiểm thử.

## Hoàn thành

Chạy test trực tiếp và toàn bộ backend unittest cho thay đổi engine/worker/mail/parser; dùng PostgreSQL/Redis cô lập và wheel mới cho refactor kiến trúc. Chạy FE test/build/lint khi ảnh hưởng giao diện/build; compileall và pip check phù hợp. Báo rõ test fail/skip, không che baseline. Cập nhật README khi đổi cách dùng, CLAUDE khi đổi invariant, technical guide khi đổi vận hành và skill khi workflow thay đổi. Không đưa secret vào tài liệu.

## Trạng thái thay đổi gần đây

- 2026-10-20 — Cloudflare Worker chống trùng kết quả đã gửi theo owner/ngày/cache epoch.
  Sang ngày mới hoặc xóa cache hôm nay cho phép check mới và xác nhận gửi lại;
  giữ nguyên lịch sử. UNKNOWN/SUBMITTING vẫn khóa xuyên ngày/reset để đối soát.
  Migration 0007 thêm cache epoch; không chạy migration hoặc restart worker thật.

- 2026-10-18 — Gỡ ứng dụng cũ theo phạm vi web hiện có; 7 module đang dùng vào domaincheck.engine; bỏ root wrappers/compat/Streamlit/CLI desktop/PyInstaller/test legacy. Giữ 50 characterization browser/cloaking và fixture nội dung mail. Extension 3.1.0 bỏ bridge local/auto-submit, giữ task web fill_only. Backend PostgreSQL/Redis sạch + fresh wheel 374/374, frontend 154/154; build/typecheck/lint/compileall/pip check đạt. Hai cây source build độc lập, wheel khớp SHA; không đổi DB production, secret/runtime hoặc profile. Báo cáo docs/testing/web-only-cutover-2026-10-18.md.

## Recent change ? 2026-10-18

Added backend/run_all_workers.bat: one launch starts all-job worker and Cloudflare runner in minimized consoles; dry-run starts no processes. API/FE remain separate; do not launch twice or interrupt active jobs. Validated dry-run and compileall; no real jobs started.

## Recent change ? unified backend startup, 2026-10-18

Standard BE command: python -m domaincheck from backend, or backend/run_backend.bat. Supervisor owns API/all-job worker/Cloudflare runner; API-only reload, local singleton guard, fail-fast child exits and signal-based shutdown without forced kill. Raw uvicorn is API-only for separate deployments. Do not run standalone workers alongside supervisor or restart active deliveries. Tests use mocked child processes and dry-run; no real queued jobs started.

Validation: launcher 6/6, full backend 380 tests OK with 173 skipped; fresh-wheel packaging 3/3 and standalone launcher dry-run passed. compileall/pip check passed. Details: docs/testing/backend-supervisor-2026-10-18.md.

## Recent change — team worker capacity, 2026-10-18

Source startup remains python -m domaincheck, now with per-type worker processes. Bounded slots default 2/2/2/2/1 (Quick Report/Check Domain/precheck/send/evidence); evidence requires capture enabled. `all` includes evidence with independent loops. Each turn handles 1 URL and requeues pending work; fairness uses the owner's last-service history across batches. Cloudflare runner bounds concurrent per-URL steps to 2 and releases capacity while paused/delayed. Claims use a short advisory transaction lock and migration 0006 queue indexes; drain preserves current-item checkpoints. DB pools are bounded at 5+2 per process.

Production: backend/compose.production.yaml + compose.yaml, backend/Dockerfile, frontend/Dockerfile/deploy/Caddyfile, and backend/scripts/deploy_team.py (deploy.bat). Separate services restart independently; DB/cache/private artifact volume and original project names are preserved. No live deploy/data copy is authorized by a source change alone. health/ready checks DB/cache; authenticated health/workers returns permission-filtered group/state/count via Redis TTL, with a FE warning. Deployment and limits: docs/team-deployment.md. Worker slots are not a global throttle for direct review/mail/capture actions; no live-provider throughput claim.

Runtime httpx is now a required package dependency (OIDC uses it), not just a test extra. Linux planning baseline for up to 30 people is 8 vCPU/32 GB RAM/500 GB SSD, subject to real latency/evidence-retention measurements; see the team guide. No hardware-capacity guarantee is derived from mock probes.

Final validation: clean PostgreSQL/Redis + fresh wheel backend 402/402 (zero skipped), FE 160/160, build/typecheck/lint/compileall/pip check passed. Linux container startup, worker restart isolation, Redis outage/readiness recovery and desktop/mobile fixture passed; no live sends or deployment. Report: docs/testing/team-capacity-2026-10-18.md.

- 2026-10-18 — Chuyển hướng dẫn deploy team và báo cáo kiểm thử tải sang tiếng Việt;
  giữ nguyên lệnh, tên cấu hình, số liệu và giới hạn kiểm thử. Chỉ đổi tài liệu,
  không thay đổi code hoặc cấu hình runtime.

- 2026-10-18 — Bổ sung quy trình cài mới, seed và bootstrap admin vào hướng dẫn
  deploy: catalog 54, admin 54/user 47; đăng nhập pending trước rồi CLI cấp admin.
  Chỉ làm rõ hành vi hiện có, không đổi grant hoặc seed dữ liệu thật.

- 2026-10-18 — Thêm docs/dev-guide.md cho Windows/Linux: cấu hình và tạo khóa
  lần đầu, migration, bootstrap admin, lệnh chạy hằng ngày và cập nhật. Bổ sung
  lệnh kiểm tra/log/dừng/mở lại/restart worker và cập nhật production vào
  docs/team-deployment.md; README liên kết runbook. Chỉ đổi tài liệu, không chạy
  dịch vụ, migration hoặc sửa secret/runtime.
