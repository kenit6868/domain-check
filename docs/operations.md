# Vận hành foundation web

Sau SMTP accepted, worker lưu bản sao Sent bằng helper legacy chung; Gmail tự lưu.
IMAP dùng account owned và endpoint allowlist, timeout 30s; status copy tách khỏi
SMTP. Nếu Sent lỗi, không retry SMTP của delivery `sent`. Phục hồi chỉ APPEND bản
sao từ immutable original plan sau khi tra Message-ID trong Sent, không gửi report
lại hoặc sửa sổ gửi thành chưa gửi. Không quét Inbox/body khác để phục hồi Sent.

Domain Worker normal batch send: sau precheck hoàn tất, chọn domain/batch và phút
nghỉ, xác nhận rồi Bắt đầu gửi batch. Worker `all` hoặc `domain_worker_send` phải chạy
thường trực; `domain_worker_precheck` chỉ kiểm tra. Endpoint SMTP phải thuộc
`DOMAINCHECK_MAIL_EGRESS_ENDPOINTS`; secret key giải mã tài khoản phải có ở worker.
Không cần bật cờ connectivity-test để gửi: đó là cờ cho nút kiểm tra SMTP/IMAP riêng.
Stop hoàn tất domain hiện tại, resume pending, retry chỉ failed. Unknown giữ để
đối soát, không tự retry. Checkpoint/ledger DB tồn tại qua restart/Redis loss.
Nghỉ batch lưu next_run_at; không sleep worker. Ngày mới phải precheck mới; batch
hôm qua không được gửi tiếp. Dedup ngày/domain/account/kênh/email nhận, không phải
bỏ email nhà cung cấp cho mọi domain. Không dọn DB/runtime/audit chỉ vì sang ngày.

Domain Worker web chọn sender accounts trước precheck, giữ scope trong Job và queue
sources. Main list chỉ nhận ready; mở `/cloaking-review` hoặc `/domain-evidence-review`
cho nhóm cần xử lý riêng. Evidence Review upload 1–3 ảnh đưa các check nguồn về ready,
không gửi mail; cloaking queue chưa có approve/send web. Precheck không có draft/preview
từng dòng. Lượt cũ không có scope phải chọn account và precheck mới.
`DOMAINCHECK_ARTIFACT_ROOT` tương đối được anchor tại root workspace/package, không
phải CWD process. API/worker cần cùng path/volume. Khi phục hồi artifact lệch thư mục,
chỉ copy đúng object key và bytes khớp size/SHA-256; không reset/bỏ integrity guard.

Worker chỉ mở một lần, giữ polling để nhận các job tiếp theo; `.bat` không phải
thao tác theo từng job. Deploy dùng API và worker service riêng cùng backend
package, restart policy và private artifact volume chung. Compose hiện chỉ có
DB/Redis, chưa deploy đầy đủ; xem `docs/deployment-topology.md`.

Worker Browser Evidence cần Playwright trong đúng backend venv (đã khai báo trong
pyproject) và Chromium: `.\backend\.venv\Scripts\python.exe -m playwright install chromium`.
Khi capture bật, startup kiểm tra launch browser trắng trước khi claim, không mở URL.
Chẩn đoán capture chỉ lưu mã whitelist, không lưu raw exception/credential/path.
Nếu gặp thiếu runtime, cập nhật package/Chromium, restart worker khi idle và precheck
lại URL lỗi; kết quả cũ vẫn được giữ để audit.
Capture web dùng trực tiếp hàm chung legacy `capture_normal_report_evidence()`:
HTTP/DNS mặc định, DOM destination rồi passive fallback. Không thêm resolver pool,
http_probe hoặc network_guard riêng. Adapter chỉ validate output và lưu artifact.
Logo mặc định WIN được đóng gói trong backend; nút áp dụng ở cấu hình mail tạo
artifact thuộc owner, không đọc config.ini, không truyền local path vào draft.

DW-01: `/domain-worker` precheck owned tối đa 100 full URL; gửi thường đã có batch riêng.
Chạy `run_domain_worker_precheck.bat` từ root hoặc CLI với
`--job-type domain_worker_precheck`; worker `all` phiên bản mới cũng nhận loại này.
Không restart worker đang chạy job; cần cài lại backend venv khi cập nhật helper
legacy. F5 chỉ GET job cũ, không enqueue lại. Dừng là cooperative, pending được giữ
để resume; chỉ failed được retry, unknown không tự chạy lại.
HTTP cloaking/lookup chạy song song; không email bị loại, cloaking còn chờ review
và passive browser verification. Capture thường dùng flag hiện có và hàm legacy chung;
chỉ artifact validated mới ready, nếu tắt/lỗi thì evidence_review. Không bật flag
chỉ để kiểm thử. Không hiện draft/preview riêng ở precheck. Queue thiếu ảnh nhận
upload, trở về ready rồi gửi trong batch; cloaking queue chưa approve/send web.

Khi sửa module compatibility ở root (đặc biệt formatter trong `phishing_toolkit.py`),
cài lại backend bằng `.\backend\.venv\Scripts\python.exe -m pip install --no-deps -e ./backend`
rồi restart API/worker khi không có job đang chạy. Hatch force-include sao chép
module vào site-packages cả trong editable install; reload mã `src` không tự cập
nhật bản sao đó. Với lỗi report-guidance, chỉ tải lại mẫu từ kết quả đã lưu.
Kiểm tra `python -m unittest tests.test_compatibility_packaging -v` bằng Python
backend từ thư mục `backend` để phát hiện formatter bị cũ.

## Thành phần hiện có

- FastAPI package tại `backend/`, health endpoint và auth primitives.
- PostgreSQL 24 bảng qua Alembic; Redis dành cho OIDC state/cache, không chứa
  delivery truth.
- React/Vite/Tailwind tại `frontend/`; có login/pending shell, hai trang quản trị
  user/role, Quick Report check và form-task Google/Microsoft; chưa có workflow gửi thật.
- Compose hiện khai báo PostgreSQL/Redis cho dev. API/worker/reverse proxy và
  artifact volume sẽ được thêm trong OPS-01, chưa phải production topology.

Quick Report có worker process thật trong package backend. Sau khi API và database dev
đã cấu hình, `domaincheck-worker --once` xử lý tối đa một operation hoặc
`domaincheck-worker --poll-seconds 2` polling liên tục. Worker gọi network investigation;
trong test chỉ dùng fake adapter. Không chạy process này với target thật trước NET-01/
egress policy và phê duyệt vận hành.

CD-01 nhận đúng một target/full URL và trả một check; job nội bộ có một item.
React `/domain-check` giữ ID operation theo user trong sessionStorage để tải lại
trạng thái, không tự chạy check mới. Evidence upload/capture dùng endpoint owned;
capture cần flag và worker riêng nhưng không có trên trang React Check Domain.
Trang này hiển thị form mẫu và nội dung để copy từ endpoint report-guidance;
registrar có mẫu email ngẫu nhiên ổn định theo domain + ngày nếu tìm được địa chỉ
nhận hợp lệ và registrar không thuộc nhóm chỉ nhận webform. Nhóm chỉ nhận webform
dùng mẫu webform riêng. Thông số check hiện
trực tiếp, mẫu dài có thể mở để copy; form chia bốn nhóm browser/CDN/registrar/
registry. Không bắt buộc ảnh, không submit form hoặc gửi mail.
Registry có webform và email thì hiện hai mẫu khác nhau theo contact đã check.
CD-01 dùng cùng process worker với loại job `check_domain` (mặc định worker xử lý
cả hai loại; `--job-type quick_report|check_domain` để giới hạn). API tạo job trả
202, GET owned trả kết quả đã lọc; worker không dùng Redis cache cho full check.
Env backend tùy chọn `DOMAINCHECK_VT_API_KEY`, `DOMAINCHECK_GSB_API_KEY`; submit
VirusTotal cần cờ request explicit và permission riêng. Full check web bỏ ghi
CSV/draft/evidence vào thư mục legacy. Chỉ kiểm thử bằng adapter mock khi chưa có
egress policy cho URL nghi vấn; chưa có thao tác gửi web.

EVID-01 bổ sung upload 1–3 ảnh PNG/JPEG trực tiếp vào private artifact store và
capture Browser Evidence qua job riêng. Capture dùng thư mục staging tạm, chỉ
chuyển ảnh đã qua manifest/hash validation vào evidence set owned; terminal page
hoặc capture lỗi không tạo evidence. Mặc định flag
`DOMAINCHECK_BROWSER_CAPTURE_ENABLED=false`; bật explicit cho worker riêng.
Adapter dùng HTTP/browser mặc định của hàm legacy, không có DNS guard riêng.
Direct capture không cần proxy; network isolation thuộc cấu hình deploy worker.
Worker `--job-type browser_evidence`
phải chạy ở process riêng, chỉ nhận job capture khi flag bật; worker mặc định
không claim capture. Playwright Chromium phải có trong môi trường worker.
Upload thủ công vẫn dùng được khi capture tắt. Không bật flag để thử URL thật
trong môi trường dev chưa có chính sách egress.

Theo yêu cầu tái sử dụng legacy, capture web đã bỏ override NET-01 DNS/probe/route
guard trước đây. Có thể đặt `DOMAINCHECK_BROWSER_EGRESS_PROXY_URL` cho browser
context khi cần; HTTP probe vẫn là mặc định của hàm chung. Mail connectivity test
được khóa mặc định; để bật, cần firewall egress riêng và
`DOMAINCHECK_MAIL_EGRESS_ENDPOINTS` exact host:port. Tester từ chối endpoint
ngoài allowlist, IP không public và proxy mail không allowlisted.

## Web Form Assistant qua HTTPS

Build frontend với `VITE_FORM_ASSISTANT_EXTENSION_ID` là ID công khai của extension
đã load. Trong **Options** của extension, operator đặt đúng origin ứng dụng; production
chỉ nhận HTTPS, HTTP chỉ dành cho localhost. Extension xin host permission cho origin
đó, sinh device ID cục bộ và chỉ nhận message từ exact origin đã cấu hình.

`DOMAINCHECK_FORM_TASK_TTL_SECONDS` mặc định 300, giới hạn 60–900 giây. Task và trạng
thái nằm trong PostgreSQL; Redis không giữ capability. Raw capability chỉ đi FE →
extension trong message một lần, extension đổi lấy status token rồi giữ payload tạm ở
`chrome.storage.session` của tab. Restart extension/tab làm mất payload tạm và operator
tạo task mới; không đưa token vào URL form để phục hồi. EXT-01 chỉ bật Google và
Microsoft ở React; cả hai fill-only, mọi CAPTCHA/submit vẫn thủ công.

Private artifact filesystem đã có adapter nhưng volume/backup production chưa hoàn
tất. `DOMAINCHECK_ARTIFACT_ROOT` phải trỏ tới volume riêng, không nằm trong document
root. Giới hạn mặc định là 10 MiB/file, tối đa 16.384 px mỗi chiều và 40 triệu pixel;
workflow tiếp tục áp count riêng (normal 1–3, cloaking 2–4). Không sửa file trực tiếp:
size/SHA-256 sai sẽ làm download fail closed.

## Phục hồi durable job

Job giữ nguyên danh sách item và thứ tự input trong PostgreSQL. `attempt` chỉ tăng khi
worker thực sự chuyển item `pending` sang `running`; retry action chỉ reset đúng item
`failed` được chọn, không reset `sent`, `submitted`, `unknown` hoặc item đã thành công.
Stop đang chạy là cooperative: worker không bắt đầu item mới, checkpoint item hiện tại
rồi chốt `stopped`. Sweeper xử lý lease stop đã hết hạn bằng cách chốt job `stopped`
và đánh dấu item đang chạy là `unknown`; operator phải reconciliation explicit, không
được tự retry. Redis restart không ảnh hưởng membership, checkpoint hay stop state này.

## Exact preview và phục hồi

Mỗi lần tạo/cập nhật draft sinh một preview revision mới. Exact plan nằm trong private
artifact JSON; PostgreSQL chỉ giữ hash/reference và metadata allowlist. Trước approve
và trước send phải đọc lại bytes, kiểm SHA-256, expiry, revision mới nhất, account/profile
version và evidence/attachment integrity. Preview stale trả conflict và operator phải
tạo/duyệt revision mới; không sửa trực tiếp artifact cũ hoặc thay hash từ client.

Backup/restore phải giữ PostgreSQL và artifact volume nhất quán. Artifact preview mồ
côi do transaction storage/DB bị ngắt sẽ được xử lý bởi maintenance/retention sau này;
không được tự gắn artifact không còn DB reference vào một preview khác.

## Quality commands

Mọi feature repository mới phải dựng owner từ session/ExecutionContext và dùng
`OwnedRepository` hoặc điều kiện tương đương đã được test. Worker là ngoại lệ duy nhất
được claim job không biết trước owner; ngay sau claim phải dùng owner/actor/account scope
từ row DB. Heartbeat cần đúng lease token + expected version; stale worker không được
gia hạn hay tiếp tục side effect. Không lưu owner mutable trên pooled DB connection.

```powershell
python -m unittest discover -s backend/tests -v
python -m compileall -q .
python -m pip check

Set-Location frontend
npm run typecheck
npm run lint
npm test
npm run build
```

DB integration test chỉ chạy với `DOMAINCHECK_TEST_DATABASE_URL` trỏ tới database
test đã migrate. Không trỏ biến này vào runtime DB. Telegram tests dùng issuer và
RSA fixture; không gọi live provider. Không chạy Streamlit tự động.

Domain Worker exact preview dùng quyền mới `domain_worker.preview`. Khi cập nhật,
chạy `domaincheck-admin sync-rbac` bằng backend venv rồi cấp quyền cho role user/custom
nếu cần; sync giữ nguyên các lựa chọn role hiện có, không dùng reset-user-permissions
chỉ để thêm quyền này. Reload API/FE theo quy trình deploy; không cần Alembic migration
mới. Preview chỉ nhận check `ready` trong ngày và item đã checkpoint succeeded. Dữ liệu
mail exact nằm trong private artifact volume, không trong audit/DB metadata; cần giữ
volume dùng chung cùng images/logo. GET đọc lại luôn kiểm hash/versions/expiry; preview
cũ không còn hợp lệ sau revision mới hoặc thay đổi hồ sơ/account/evidence. Không có
SMTP/IMAP side effect hay approval/send endpoint ở bước này.

Khi dùng `backend/.venv` để chạy uvicorn, mọi lệnh cài dependency và `pip check`
phải dùng `backend/.venv/Scripts/python.exe`. Sau EVID-01, route multipart cần
`python-multipart==0.0.32`; cài package backend lại trong venv cũ trước khi
restart. Kiểm tra `python -c "from domaincheck.main import app"` bằng cùng
interpreter để phát hiện lỗi import ngay.

## Cấu hình Telegram OIDC

Copy `backend/.env.example` thành `backend/.env` rồi điền Client ID/Secret lấy từ
BotFather **Login Widget**; backend tự đọc file này khi chạy local. Với Vite dev, mở
ứng dụng bằng `http://127.0.0.1:5173` và đăng ký đúng Allowed URL/callback
`http://127.0.0.1:5173/api/v1/auth/telegram/callback`. Không trộn `localhost` và
`127.0.0.1` trong cùng lượt login vì cookie browser binding là host-only.

Telegram token endpoint dùng HTTP Basic cho Client ID/Secret; secret không nằm trong
form body/log. `GET /api/v1/auth/telegram/status` chỉ cho biết cấu hình đã sẵn sàng và
callback hiện hành. Login page khóa nút khi thiếu cấu hình và hiển thị callback cần
đăng ký. Production phải dùng HTTPS cùng origin FE/API và đổi redirect URI tương ứng.
User login lần đầu vẫn ở `pending`; bootstrap/kích hoạt admin là bước riêng.

## Bootstrap quản trị viên

Sau mỗi deploy có thay đổi permission catalog, chạy bước idempotent sau migration:

```powershell
domaincheck-admin sync-rbac
```

Lệnh này tạo/cập nhật permission catalog và hai system role `user`, `admin`; không
kích hoạt user và không tự gán role cho danh tính nào. Admin seed toàn catalog khi
tạo lần đầu; user seed các quyền nghiệp vụ. Những lần sync sau giữ quyền, tên và
mô tả đã chỉnh của cả hai role. Trang Role & quyền cho sửa cả admin; lưu role tự
materialize code permission mới thiếu trong DB trong cùng transaction. Không cần
reset role hoặc chạy sync riêng chỉ để lưu quyền mới từ catalog của code.

Không có cơ chế “user đầu tiên là admin” và không dùng Telegram username. Sau khi
danh tính đích đã login một lần và đang ở trạng thái pending, chạy lệnh cục bộ với
OIDC subject ổn định:

```powershell
$env:DOMAINCHECK_BOOTSTRAP_TELEGRAM_SUBJECT="<stable-oidc-subject>"
domaincheck-admin bootstrap-admin
```

Cũng có thể truyền `--telegram-subject`. Lệnh đồng bộ permission/system role, kích
hoạt đúng identity theo issuer + subject và chỉ gán role `admin`; không in token
hay credential. Tài khoản thường được gán role `user` khi admin kích hoạt, sau đó
nhận đúng permission admin đã chọn cho role này. Sau bootstrap hoặc thay đổi role,
user đăng nhập lại vì session cũ bị revoke qua `authz_version`.

## Secret/log policy

Không log request body route Cookie/OIDC token, SMTP/IMAP/proxy password, master
key hoặc URL có token. `VITE_*` là public. Secret envelope cần key AES-256 ở
ngoài DB/repo và key version để rotate. Artifact/runtime legacy không được mount
vào static frontend.

`DOMAINCHECK_SECRET_MASTER_KEY` là URL-safe base64 của đúng 32 byte và
`DOMAINCHECK_SECRET_KEY_VERSION` chọn version đang ghi. API mail chỉ trả các cờ
`has_*`; PATCH không có field password giữ nguyên ciphertext. Đổi hoặc xóa secret
là field explicit, không gửi chuỗi `********`. Nút lưu chỉ ghi DB, không tự mở
SMTP/IMAP. Hai action **Kiểm tra SMTP/IMAP** chỉ hiện với quyền
`mail_accounts.test_own`, yêu cầu Origin + CSRF và dùng secret đã mã hóa của đúng
owner. SMTP chỉ kết nối/TLS/login, IMAP chỉ TLS/login/logout; không gửi thư, chọn
folder hoặc đọc mailbox. Kết quả lỗi được phân loại, không trả raw exception.
Không dùng action này với endpoint thật khi chưa hoàn tất policy egress `NET-01` và
chưa được người vận hành cho phép.

## Redis cache scope

Cache tái tạo được luôn scope theo owner/feature/business day và không chứa URL hay
email thô trong key. TTL kết thúc tại nửa đêm kế tiếp theo timezone user. Redis bị
flush/restart chỉ gây cache miss; PostgreSQL, artifact, approval và delivery không
được dựng lại từ Redis nên không mất. `POST /cache/clear` yêu cầu permission
`clear_cache` của đúng feature, dùng SCAN/UNLINK trong namespace owner; nếu Redis
không sẵn sàng API trả 503. Không dùng `FLUSHDB` trong vận hành ứng dụng.


Login navigation: open /login for Telegram; / is now an authenticated dashboard.
Successful callback returns /, errors return /login. BotFather callback remains
/api/v1/auth/telegram/callback; no provider config change required. Diagnostic
GET /api/v1/auth/telegram/status retains the public configured/redirect_uri fields;
the login card shows a user-facing setup notice rather than infrastructure details.
Restart FastAPI after updating auth routes if the Windows reloader does not exit.
