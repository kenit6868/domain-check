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

UDR autofill (extension 3.1.1): Quick Report offers UDR Limited for catalog namespaces .hk.com/.hk.org/.ltd.hk/.inc.hk. The adapter fills reporter name/email, configured company name, target hostname, Phishing or Pharming, and the registry draft. Phone is not in the reporting profile and must be entered manually; missing company/phone keeps NEEDS_MANUAL. Honeypot and Send are untouched. Build the FE ZIP, update the unpacked extension in the same folder, explicitly Reload it, and reload the web page. No new migration or automatic submission.

CentralNic autofill (extension 3.1.2): existing CentralNic catalog forms route to https://centralnicregistry.com/contact/ (the www URL redirects to this origin). The adapter waits for the exact HubSpot form a89570f8-bec3-40be-a33b-10c9c21a09fe inside its host or open shadow root, fills first name plus remaining name words as last name, email, Report Abuse and registry draft. Review name splitting; a single-word name leaves last name manual. Required phone and CAPTCHA remain manual. Hidden context, CAPTCHA responses and Submit are untouched. No HubSpot host permissions, direct submission API, new registry recipients, migration or automatic restart are added. Rebuild/reload as for UDR; existing registry email remains available.

GoDaddy stabilization (extension 3.1.3, adapter 1.0.1): visible fields can precede Next.js hydration and the page mount resets form state. The adapter reacquires field nodes, fills empty fields only, and verifies values every 250 ms for at least 2 seconds (up to 5 seconds); success requires four consecutive matching samples. It preserves different nonempty operator values and reports partial fill on persistent resets/missing fields. Recheck now requires email and description as well as brand and URL. No background refill, attestation click or submission; update/reload the extension as above. The initialization race is reproduced using offline reset/replacement fixtures; live inspection was read-only without entering or submitting report data.

## Dynadot Phishing (extension 3.1.4)

Quick Report nh?n registrar Dynadot s? cung c?p task `dynadot_phishing` t?i `https://www.dynadot.com/report-abuse`. Adapter d?ng t?n field c?a form Phishing (`full_name_2`, `email_2`, `email2_2`, `domains_2`, `official_website_2`, `proxy_locations_2`, `domain_path_2`, `comments_2`); kh?ng d?a v?o thu?c t?nh Vue sinh t? ??ng. Domain l?y hostname, URL gi? route ??y ??. Hai tr??ng Official Website v? VPN/Proxy/user-agent ?i?n N/A theo y?u c?u ng??i v?n h?nh. Ch? t? ch?n m?t native option c? nh?n ch?nh x?c Phishing; n?u dropdown t?y bi?n, ch?n th? c?ng trong c?a s? ch? 30 gi?y ho?c d?ng ?i?n l?i trong popup sau ??. Ch?a x?c minh dropdown th?t v? k?t n?i browser kh?ng ??c ???c DOM; fixture ch? ki?m ch?ng native select v? form ???c cung c?p.

Build/deploy l?i FE, t?i ZIP 3.1.4 t? Quick Report, gi?i n?n ?? v?o c?ng th? m?c extension v? Reload th? c?ng. N?p code API khi tri?n khai; kh?ng c?n migration ho?c restart worker cho thay ??i n?y. Kh?ng t? c?p nh?t b?n c?i Chrome. CAPTCHA, t?p ??nh k?m v? Submit v?n th? c?ng; kh?ng l?u response CAPTCHA v?o fixture/t?i li?u.
### Registrar WHOIS `.africa.com` / `.hk.com` (2026-11-07)

- `.africa.com`: tra tên đăng ký tại https://whois.gatewaysrs.com/ qua HTTPS. Chỉ nhận Registrar Abuse Contact Email khi Domain Name khớp chính xác; không lấy email admin/registrant hoặc contact domain cha.
- `.hk.com`: backend tra tự động đúng tên đăng ký qua WHOIS cổng 43 tại `whois.udr.hk.com`. Trang https://whois.hk.com/ có CAPTCHA chỉ là fallback thủ công khi WHOIS lỗi/không khớp. Không cần thư viện mới. Giữ nguyên form registry UDR.
- Nếu bản ghi xác thực registrar là Marcaria.com International nhưng ô abuse trống, dùng `abuse@marcaria.com` theo [chính sách abuse chính thức](https://www.marcaria.com/ws/en/terms-and-conditions/policies/domain-abuse-complaint-escalation-policy). Đây là fallback theo registrar thực tế, không gán Marcaria cho mọi domain `.hk.com`.
- Tra trực tiếp ngày 07/11/2026: `kasumay.africa.com` → Registrar.eu, WHOIS có `abuse@registrar.eu`; `hammar.hk.com` → Marcaria.com International, WHOIS abuse trống, fallback chính thức `abuse@marcaria.com`. Chỉ truy vấn dịch vụ WHOIS; chưa truy cập website nghi vấn hoặc gửi report. Các fixture kiểm thử chỉ giữ trường cần thiết, không lưu thông tin registrant.
- Quick Report mới dùng policy `legacy-run-cdn-check-v3-private-whois`; giữ lịch sử và delivery. Workflow khác có thể còn dùng cache hiện tại. Không cần migration/build frontend. Chỉ nạp lại code khi worker hoàn tất job; lượt cập nhật này chưa restart dịch vụ.


## Verisign DNS Abuse — extension 3.1.5 (2026-11-10)

Quick Report cung cấp `verisign_dns_abuse` cho registry có form `https://verisign.my.site.com/DNSAbuse` theo catalog hiện tại (.com/.net/.name/.cc). Trang chuyển tới `/ReportAbuse/`; extension hỗ trợ cả hai đường dẫn trên đúng HTTPS host. Namespace private có registry riêng vẫn giữ routing riêng.

Adapter duyệt open shadow DOM của Salesforce Lightning, chọn Type of Abuse = DNS Abuse rồi DNS Abuse Category = Phishing. Domain dùng tên đã xác định trong check; URL giữ nguyên path/query/fragment. Corresponding Top-Level Domain chọn chính xác option theo đuôi cuối, không đoán khi thiếu option. Hai ô Date of First Observation và Registrar Notification Date mở lịch và bấm Today theo ngày địa phương của trình duyệt. Registrar That Was Notified lấy registrar của check nếu có; Your Email Address và Description lấy email/bản nháp tiếng Anh từ hồ sơ và formatter hiện tại. Form này không có ô tên/công ty ở bước đầu.

Giữ giá trị đã điền; giá trị khác task, field thiếu, ngày không khớp, TLD không hỗ trợ hoặc description vượt 3000 ký tự báo cần duyệt thủ công. Không tự cắt nội dung. Người dùng duyệt lại ngày quan sát và việc đã thông báo registrar trước khi bấm Next. Next, CAPTCHA, tệp và Submit luôn thủ công. Không đọc config.ini hay tự cập nhật profile Chrome.

Build FE tạo `frontend/dist/downloads/web-form-assistant.zip` bản 3.1.5. Giải nén vào cùng thư mục extension đang dùng, Reload thủ công và chấp nhận quyền cho host Verisign nếu Chrome yêu cầu. API cần nạp code mới khi triển khai; payload mới giữ mặc định rỗng cho task cũ. Không cần migration/restart worker. Kiểm thử: xem `docs/testing/verisign-adapter-2026-11-10.md`.


### Quick Report — Netcraft pending và QueuePool timeout

FE giữ toàn bộ card trên một trang sau bộ lọc cache/cũ, giữ số thứ tự ban đầu
và tổng tiến độ. Bố cục/padding/nội dung đối chiếu giữ như trước, không có nút mở
draft hoặc wrapper skeleton. IntersectionObserver chỉ bật query khi card gần
viewport (400px), sau đó giữ bật. Không có observer thì query được bật ngay.
Draft các nhóm general/CDN/registrar/registry tự tải khi cuộn tới, qua hàng đợi
tối đa hai request đồng thời. Các nhóm cùng check dùng chung query và mở form
extension không cần tải draft đối chiếu trước. Trạng thái Netcraft gộp qua GET
`/quick-reports/netcraft-statuses?check_ids=UUID&check_ids=UUID`, tối đa 50 ID.
Batcher tách theo QueryClient/owner, chạy lần lượt và chặn nút nếu thiếu trạng
thái hoặc API lỗi. Mỗi check vẫn có cache riêng để mutation/polling cập nhật.
API xác thực quyền/owner/workflow, giữ ngày và UNKNOWN recovery; không đọc body
preview/artifact hoặc gửi report trong GET gộp. Một ID không hợp lệ hoặc không
thuộc owner làm cả request bị từ chối, không trả kết quả một phần.
Fixture 100 reader đồng thời tạo 2 request trạng thái; draft tự tải tối đa 2
request cùng lúc, tổng số tùy số card đã xem (fallback không observer tải đủ
100 draft lần lượt). Chrome thường chỉ yêu cầu cho card gần viewport.
Đây là giới hạn theo client, không phải cam
kết năng lực phần cứng hoặc throttle toàn cục. Cần deploy BE trước/cùng FE.

Khi reload danh sách nhiều kết quả, FE đọc trạng thái Netcraft của từng check.
Hai GET Netcraft trước đây giữ kết nối xác thực trong transaction request rồi
mở thêm transaction độc lập để đọc trạng thái. Khi pool 5+2 đã đầy, các request
có thể giữ một kết nối và cùng chờ kết nối thứ hai đến timeout 30 giây; `/me`
và form-drafts bị ảnh hưởng, dù readiness sau đó vẫn trả 200.

Hai GET trạng thái hiện truyền session request vào QuickReportActions.get/find;
standalone callers vẫn có transaction riêng. Owner/day scope, row lock và phục
hồi SENDING hết hạn thành UNKNOWN giữ nguyên. Không tăng pool, không đổi grant,
session token hoặc cơ chế gửi/intent. POST preview/submit vẫn giữ transaction
riêng và checkpoint intent trước network như trước.

Không cần migration hoặc env mới. Phần cuộn/API gộp cần cập nhật cả BE và FE.
Sau cập nhật source, triển khai
theo runbook aaPanel khi công việc đang chạy đã hoàn tất; không restart worker
thật để kiểm thử. Reload Quick Report, thử mở task Google mới, xem `/me`,
Netcraft và form-drafts không còn treo 30 giây; không Submit report để thử.

### Extension 3.1.8 — ID chung cho team

Manifest public key cố định tạo ID `aaodigjnbngpggpgkphmeaakpfhehcpb`.
FE dùng ID này khi env trống; env khác vẫn ghi đè cho gói tùy biến. Quản trị sửa
ID cũ trong frontend/.env sang ID chung một lần rồi build/deploy web.
Không tự sửa env thật. Không lưu private key; public key không cấp quyền owner.
Không đổi manifest key giữa các phiên bản.

Kiểm thử: Load unpacked cùng ZIP 3.1.8 từ hai thư mục hoặc trên hai Chrome profile,
ID phải giống giá trị trên. Nếu Chrome giữ ID cũ, gỡ bản cũ rồi Load unpacked lại.
Device ID có thể thay đổi khi chuyển ID; dùng task mới, không gửi lại report cũ.
Kiểm tra handshake local/production như phần dưới, version phải là 3.1.8.
Cài mới vẫn cần Developer mode/Load unpacked; ZIP không tự cài. Cài một nút cần
phân phối qua store hoặc cơ chế quản lý Chrome phù hợp.

### Extension 3.1.7 — kết nối mặc định

Cài mới hoặc cập nhật extension rồi Reload, tải lại trang và bấm Kiểm tra lại.
Không cần Options cho `https://tool-check.camellrp.com`, `http://localhost:5173`
và `http://127.0.0.1:5173`. Device ID tự tạo ở lần kết nối hợp lệ đầu tiên;
nâng cấp giữ ID cũ. Origin cũ trong Options là địa chỉ bổ sung, không thay mặc định.
Origin khác vẫn cần cấp quyền và lưu trong Options. Mỗi task giữ riêng apiOrigin;
exchange dùng sender URL, không dùng origin trong payload. Host permissions cho
loopback bao phủ các cổng, nhưng worker chỉ cho phép exact origin cổng 5173 mặc định.

Kiểm thử thủ công:

1. Giải nén ZIP vào đúng thư mục extension đang cài, Reload để giữ ID. Chấp nhận
   quyền truy cập mới nếu Chrome hỏi. Không sửa Chrome profile.
2. Mở production, tải lại trang, Kiểm tra lại phải nhận 3.1.7 mà không mở Options.
3. Mở local cổng 5173 trong tab khác, Kiểm tra lại; cả hai môi trường cùng kết nối.
4. Mở task mới từ từng môi trường bằng dữ liệu thử; kiểm tra điền form và trạng thái
   về đúng nơi tạo task. Next/CAPTCHA/upload/Submit vẫn thủ công; không gửi report để thử.
5. Cài mới trên Chrome profile thử riêng để kiểm tra không cần Options. ID mới phải
   khớp frontend/.env của FE thử; thay ID cần build lại FE. Việc này không tự giải
   quyết phân phối ID unpacked giữa các máy trong team ở bản 3.1.7;
   bản 3.1.8 đã bổ sung key/ID chung như phần trên.

Production cần cập nhật source và build/deploy FE để cung cấp ZIP/nhãn mới.
Người dùng cập nhật extension riêng. Không cần migration hay đổi backend.

### Sửa Verisign trong extension 3.1.6

Sau chọn Phishing, field `phishingDetails` (Brand name or website being impersonated) xuất hiện và nhận `brand_name` từ hồ sơ. Bản 3.1.5 đã bỏ sót field động này. Background phải trả registrar/target_domain từ session storage cho `form-task-for-tab`; bản 3.1.6 đã sửa đường truyền này. Calendar cần tìm qua cả physical parent và shadow host vì Lightning synthetic shadow hiển thị thành light DOM trong môi trường cô lập của Chrome extension. Đã xác minh bốn field bằng dữ liệu mẫu trên form thật trong CDP isolated world. Không bấm Next hoặc gửi báo cáo.

Tải ZIP 3.1.6, giải nén vào cùng thư mục extension, Reload thủ công rồi mở task Verisign mới từ Quick Report; task cũ có thể chưa có registrar trong snapshot. Registrar chỉ được điền khi check có dữ liệu này. Bản sửa không đổi backend; backend vẫn cần đã nạp phần task payload của 3.1.5. Không cần restart worker.


### Phiên bản extension và khả năng tự điền trong Quick Report

Nhãn tải ZIP lấy từ import manifest JSON, không lấy biến define tại thời điểm Vite khởi động; HMR theo dõi manifest trong dev. Production vẫn cần build/deploy FE và tải lại trang để nhận bản web mới. ZIP tải qua đường dẫn có query version. Bản đang cài thấp hơn bản đi kèm web hiển thị cảnh báo cập nhật; phiên bản không hợp lệ không được coi là tương thích.

Kiểm tra tương thích theo provider tại `extensionCompatibility.ts`: luồng web fill-only tối thiểu 3.1.0; UDR 3.1.1, CentralNic 3.1.2, GoDaddy 3.1.3, Dynadot 3.1.4, Verisign 3.1.6. Form tương thích vẫn dùng được khi extension chưa phải bản mới nhất. Form không tương thích tắt nút tự điền, hiện version tối thiểu và liên kết mở thủ công; không tạo task. Mutation kiểm tra lại điều kiện trước gọi API. Quyền backend/owner/device/CSRF vẫn giữ nguyên; kiểm tra version FE chỉ phục vụ tương thích giao diện. Sau cập nhật extension và Reload, bấm Kiểm tra lại để FE nhận handshake mới và bật form tương ứng. Không tự cài/reload extension hoặc restart worker.


## Cấu hình env thống nhất local và VPS (2026-11-10)

Giữ cấu hình backend/hạ tầng trong `backend/.env`; cấu hình FE công khai trong `frontend/.env`, gồm `VITE_FORM_ASSISTANT_EXTENSION_ID`. Cả local Vite và deploy Docker dùng cùng vị trí, không chuyển ID sang backend khi lên server. Helper production chạy Compose với `--env-file .env --env-file ../frontend/.env` từ thư mục backend và kiểm tra cả hai file trước gọi Docker. File env vẫn bị loại khỏi build context; chỉ ID công khai được truyền vào build argument. Lệnh Compose thủ công cũng phải dùng cả hai env file. Đổi ID cần build lại frontend. Nếu từng khai báo ID trong backend env, chuyển giá trị đó sang frontend env rồi bỏ dòng trùng bằng thao tác của người vận hành; cập nhật source không tự sửa cấu hình thật. Shell environment vẫn có ưu tiên theo chuẩn Docker/Vite, nên tránh export ID cũ khi build.


## aaPanel reverse proxy

Xem [runbook aaPanel](aapanel-deployment.md). Helper thêm `--aapanel` để nạp `compose.aapanel.yaml` cuối cùng trong mọi bước. Web chỉ publish `127.0.0.1:8088:80`, nhận địa chỉ Caddy HTTP nội bộ; Nginx aaPanel quản lý chứng chỉ và chuyển tiếp Host đúng hostname công khai. DB/Redis không publish cổng host. Không đổi project name/volume/internal endpoint. Cần Compose 2.24.4+; bản không flag vẫn là standalone Caddy 80/443. Luôn dùng đúng ba file Compose cho ps/logs/exec/update. 8 test deploy đạt với fixture config render, gồm chỉ một port loopback và không có port DB/Redis; dry-run không gọi Docker. Chưa kiểm thử end-to-end trên aaPanel thật hoặc chạy container/migration trong lượt thay đổi này.
