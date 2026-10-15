# Vận hành foundation web

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
kích hoạt user và không tự gán role cho danh tính nào. Role `admin` luôn được đồng
bộ với toàn bộ permission trong catalog. Role `user` được tạo rỗng và những lần
sync sau không ghi đè ma trận permission mà admin đã chọn. Đây là deploy step độc
lập, không chờ tới lúc bootstrap admin mới tạo dữ liệu phân quyền.

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

