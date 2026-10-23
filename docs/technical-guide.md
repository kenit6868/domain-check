# Web operation guide

The supported source trees are backend/ and frontend/. There is no desktop/Streamlit application, legacy JSON worker, root Python CLI or PyInstaller build. The implemented web menus define product scope.

## Install and run

Hướng dẫn lệnh từng bước bằng tiếng Việt: [môi trường dev Windows/Linux](dev-guide.md)
và [deploy, cấp admin, xem log và vận hành production](team-deployment.md).

Install backend as a package with its test extra, configure backend/.env from backend/.env.example and run Alembic migrations before startup. Run python -m domaincheck from the backend environment to start API and required workers together. Raw uvicorn and the individual worker scripts are only for explicitly separated deployments. Build/deploy frontend/dist including downloads and favicon. Never copy local config.ini into a release.

Use docker compose -f backend/compose.yaml for local PostgreSQL/Redis. Its domaincheck project name and volume names are unchanged by moving the compose file; do not start a second stack with different names or delete existing volumes as a cleanup step.

API/worker must share DB, private artifact storage and package release. Configure SMTP/IMAP allowlisted endpoints, browser capture/Chromium and origins explicitly. Do not enable real delivery as part of a smoke test. A deploy requires safe process replacement; do not interrupt an active worker or reset an uncertain delivery.

When replacing a wheel built with the same package version, explicitly force-reinstall that reviewed wheel in the target venv (with --no-deps when its pinned dependencies are already installed), then replace idle API/worker processes together. This removes formerly installed top-level force-include modules through the package installer rather than manually deleting site-packages. The refactor itself does not reinstall a live worker environment.

## State and permissions

The database owns user/role/mail settings, jobs, reviews, previews, evidence and delivery ledger. Engine/policy strings that contain legacy identifiers remain unchanged to preserve historical bindings. Engine code is now package-owned and cannot rely on root paths, config.ini, automatic file-cache migration or bare-module imports.

API startup seeds the permission catalog under the existing transaction/advisory lock. Old roles retain their explicit grants; unknown permissions and historical data are not broadly deleted. Session permission edits preserve valid sessions only; revoked/expired/stale sessions remain invalid. Personal/system dashboard and audit scopes require their existing permissions.

Domain Worker workspace restores only today's work according to backend account timezone. Prior batches remain history/read-only; new-day work requires precheck again. Do not delete jobs to clear the UI. Active locks/checkpoints survive the day boundary until safe stop/recovery.

Domain Worker accepts browser fragment routes such as `https://example.com/wap/#/register`.
The full route is retained for URL identity, precheck, review and browser navigation;
existing evidence URL secret redaction still applies. The 2026-10-21 input fix needs
no migration or frontend build. Source workers must load the updated code: let active
jobs finish, stop the supervisor gracefully and start it again with `python -m domaincheck`.

## Evidence and delivery

HTTP cloaking compares profiles before isolated passive browser verification. Confirmed cloaking needs validated approved evidence; no-email cases never enter review. Normal Browser Evidence and cloaking evidence are separate. Verified navigation remains the existing opt-in constrained browser flow. Never enter credentials, submit forms or download files during capture.

Exact preview binds account, recipient, profile, MIME content, evidence manifest and hashes. SMTP follows the existing TLS/allowlist/timeout rules; checkpoint every delivery and keep UNKNOWN separate from failed. Retry only missing/eligible failed deliveries. Sent-copy failure does not authorize resending SMTP. Provider accepted/HTTP accepted/takedown outcomes are distinct.

Provider Replies synchronization is explicit and account-scoped; parser/formatter live in domaincheck.engine.provider_replies, while IMAP/SMTP and durable state stay in application services. No legacy JSON mail cache or sender is supported. Audit metadata is allowlisted and excludes secrets/bodies.

## Extension

Source: frontend/extensions/cloudflare-profile-worker. Version 3.1.0 removes the old local bridge and automatic-submit branch, retaining exact-origin token exchange and fill_only tasks. Vite emits the downloadable ZIP and embeds manifest version; bump manifest.version and rebuild FE when releasing extension changes. The browser can download/install or replace files and explicitly Reload. Never install, reload, change profiles or submit automatically.

Cloudflare Worker cache: migration `0007_cloudflare_cache_epoch` adds a separate cache epoch to records and their uniqueness constraint, preserving existing history with the empty epoch. Apply `alembic upgrade head` from backend before starting updated API/runner; stop only when active jobs have drained. No live migration or worker restart is performed by a source change. Accepted-result dedupe is scoped to owner, account-local business day and current cache epoch. Clearing today's cache rotates the epoch under the owner lock and prevents sending cleared batches; subsequent precheck creates fresh records without changing old payloads/results. UNKNOWN/SUBMITTING remain protected across epochs/days. Cloudflare can independently reject a duplicate report. Downgrade refuses conflicting historical rows rather than deleting them.

An existing profile may depend on chrome_extension/ as its unpacked installation path. python backend/scripts/sync_installed_extension.py refreshes that ignored generated copy to keep its ID/path working. It is not old product source. New installs can use the FE source or ZIP; confirm extension ID and exact Application origin.

## Tests and safe release

Run backend unittest with isolated PostgreSQL/Redis and a freshly built wheel; run FE tests/build/lint, compileall and pip check. Packaging tests assert domaincheck.engine contents and absence of root aliases/config.ini/compat/desktop code. Engine tests verify no config reads/runtime writes on import, and draft/HTML fixtures preserve pre-cutover content.

Data and secrets retained at root are not source to delete. Historical docs/testing reports describe their original versions and do not override current instructions. After review, deploy the complete package/FE build; a source cleanup alone is not a production restart or DB migration.

## One-click workers on Windows

Double-click backend/run_all_workers.bat once to start both the all-job worker and Cloudflare runner in minimized consoles. Do not additionally start individual worker shortcuts. Open their taskbar windows for logs; stop only when idle. API/FE and PostgreSQL/Redis must already be running. This launcher does not supervise, restart or deduplicate processes. Pass --dry-run to print commands without launching real workers.

## Standard backend startup (supersedes separate local worker startup)

From backend run .venv/Scripts/python.exe -m domaincheck, or double-click backend/run_backend.bat. This runs API + per-type workers + Cloudflare runner with inherited logs and environment under one development supervisor. PostgreSQL/Redis and FE remain separate. Pass --reload for API-only development reload; restart the stack when worker code changes. A child exit stops the development stack with nonzero status. Ctrl+C/SIGTERM signals child groups and waits without forced termination. Guard port 8790 prevents duplicate supervisors on the same host (override --supervisor-port consistently if needed). It cannot detect workers launched manually: stop those when idle first. No production processes are restarted by this code change. --dry-run prints commands only. Raw uvicorn and individual worker launchers remain for explicit separate-service deployment, never combined with this supervisor.

Production team deployment now has independent API/worker/Cloudflare/FE services under compose.production.yaml, migration-before-start, graceful worker drain, private artifact storage and worker health. Use [the team deployment guide](team-deployment.md) as the current production procedure; the earlier worker-only shortcuts are optional tools for explicitly split local deployments. Existing host artifacts must be migrated explicitly before switching to the new volume. No automatic copy or live cutover is performed by source changes.

## Xóa cache không có email — Domain Worker

Sau khi bổ sung địa chỉ email liên hệ cho domain, bấm **Xóa cache không có email**
rồi chạy **Precheck danh sách**. Backend bỏ hiệu lực kết quả không có email của
chính người dùng trong ngày theo timezone tài khoản. Không bấm xóa thì giữ cách
lọc cũ. Không tự tạo job hay gửi email khi xóa.

API: `POST /api/v1/domain-worker/cache/clear`, quyền `domain_worker.clear_cache`
đã có trong catalog; kiểm tra CSRF và khóa batch tại backend. Trả 409 nếu batch
đang chạy, tạm dừng hoặc còn chờ gửi. Hoàn thành/kết thúc batch trước khi xóa.
Kết quả cũ vẫn nằm trong lịch sử; sent/unknown/sending và evidence không bị xóa.
Không cần migration; nạp code BE/worker sau khi job đang có hoàn tất và build FE
khi triển khai. Không thay grant tự động.

## Registrar và email abuse — cập nhật 2026-10-23

WHOIS chỉ dùng trường `Registrar Abuse Contact Email` cho email báo cáo; không
suy luận email admin, kỹ thuật hoặc registrant là email abuse. Quick Report ưu
tiên contact WHOIS được xác định rõ, rồi RDAP, cuối cùng bảng email tĩnh. Nếu
RDAP bổ sung registrar, kết quả và form báo cáo đều dùng tên tìm được đó.

Các tên trong `.radio.am`/`.radio.fm` đăng ký qua nhiều registrar khác nhau.
Backend tra đúng tên cấp ba tại `whois.centralnic.com:43`, kiểm tra domain trong
response; không dùng WHOIS của `radio.am` để chọn contact cho một tên bên dưới.
Nếu không tìm thấy đúng bản ghi thì không suy luận contact từ domain cha.
Truy vấn có deadline 8 giây và response tối đa 1 MiB, không theo referral tùy ý.

Check Quick Report mới dùng engine/cache policy
`legacy-run-cdn-check-v2-registrar-abuse`, vì vậy không dùng lại Redis result từ
policy v1. Lịch sử, check cũ, preview và trạng thái đã gửi/chưa rõ kết quả gửi
không bị sửa hoặc tự chạy lại. Muốn cập nhật một kết quả cũ, nhập lại URL và bấm
Kiểm tra sau khi API/worker đã nạp code mới. Job đã tạo trước cập nhật vẫn giữ
engine binding cũ; không restart khi đang có job. Không cần migration hoặc build
FE cho bản sửa này. Domain Worker vẫn giữ quy tắc cache/review/delivery riêng;
bản sửa không tự reset batch hay gửi lại email.

## Link Status và đuôi đăng ký riêng — cập nhật 2026-10-23

Link Status dùng Public Suffix List offline kèm phần PRIVATE. Ví dụ:

| URL/host | Tên tra đăng ký |
| --- | --- |
| `www.ten.radio.am/path` | `ten.radio.am` |
| `login.ten.africa.com/path` | `ten.africa.com` |
| `ten.uk.com` | `ten.uk.com` |
| `www.example.co.uk` | `example.co.uk` |
| `radio.am` | `radio.am` — bản thân domain gốc |

Namespace private có WHOIS route được hỗ trợ sẽ được hỏi trực tiếp ở tên đăng
ký, tránh python-whois tự rút thành domain cha. Response phải có Domain Name
trùng khớp trước khi dùng `clientHold`/`serverHold`. Không hỏi RDAP `.am`/`.com`
của IANA để kết luận cho registration private. Namespace chưa có route, lookup
lỗi hoặc response sai tên trả registration `UNKNOWN`, không suy luận hold hoặc
chưa đăng ký từ domain cha. Các route là danh sách cố định trong
`core/link_status.py`, không tự theo WHOIS referral bất kỳ.

DNS và HTTP vẫn kiểm tra hostname/URL gốc cùng các guard private IP, mixed DNS,
redirect và timeout. Website hoạt động được vẫn có thể hiện HTTP OK khi trạng
thái đăng ký chưa xác định; các quan sát này độc lập.

Không cần migration, build FE hoặc reset cache. Nạp lại API khi triển khai rồi
bấm kiểm tra lại; Link Status không lưu kết quả giữa các lượt. Không restart
worker đang có job chỉ để thử thay đổi này.

Link Status có hai tầng subprocess: tiến trình kiểm tra tổng và WHOIS fallback.
Cả hai gọi module cố định `domaincheck.core.link_status`. Bản dùng `__name__`
trước đó có thể gọi nhầm `python -m __main__ whois`, khiến WHOIS luôn UNKNOWN
trong lượt kiểm tra tổng dù tra riêng có dữ liệu. Nạp lại API với bản sửa rồi
bấm kiểm tra lại; không cần đổi timeout HTTP hoặc restart worker để xử lý lỗi
gọi module này. Nếu WHOIS hợp lệ nhưng HTTP timeout, hai trạng thái vẫn được
hiển thị độc lập.
