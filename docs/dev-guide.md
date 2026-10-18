# Chạy và sử dụng môi trường dev

Các lệnh dưới đây dùng source backend/frontend hiện tại. PostgreSQL và Redis chạy
Docker; API và toàn bộ worker chạy chung bằng một lệnh BE; frontend chạy riêng.
Không chạy thêm BAT worker khi đã dùng lệnh BE chung.

## 1. Chuẩn bị lần đầu trên Windows

Cần Python 3.12–3.14, Node/npm theo `frontend/package.json` và Docker Compose.
Mở PowerShell tại thư mục gốc dự án:

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -e "./backend[test]"
backend/.venv/Scripts/python.exe -m playwright install chromium
if (-not (Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
if (-not (Test-Path frontend/.env)) { Copy-Item frontend/.env.example frontend/.env }
```

Chỉnh `backend/.env` bằng trình soạn thảo, không ghi đè cấu hình đang sử dụng:

- Đặt `POSTGRES_PASSWORD` và mật khẩu tương ứng trong
  `DOMAINCHECK_DATABASE_URL` (host `127.0.0.1`). Ký tự đặc biệt trong mật khẩu
  ở URL phải được URL-encode. Đổi biến mật khẩu không đổi mật khẩu của database
  đã tồn tại trong volume.
- Điền `DOMAINCHECK_TELEGRAM_CLIENT_ID`, `DOMAINCHECK_TELEGRAM_CLIENT_SECRET`,
  `DOMAINCHECK_TELEGRAM_REDIRECT_URI` theo ứng dụng Telegram của bạn.
  Dev mặc định dùng `http://127.0.0.1:5173/api/v1/auth/telegram/callback`.
- Đặt `DOMAINCHECK_BOOTSTRAP_TELEGRAM_SUBJECT` thành subject OIDC của admin
  dự kiến; đây không phải username dạng `@ten`.
- Đặt `DOMAINCHECK_BROWSER_CAPTURE_ENABLED=true` để test đầy đủ tính năng ảnh.
- Cấu hình master key theo bước tiếp theo. Giữ `DOMAINCHECK_SECRET_KEY_VERSION=1`
  cho database mới.

**Chỉ khi tạo database mới**, tạo khóa bằng lệnh sau tại thư mục gốc:

```powershell
backend/.venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

Tự sao chép kết quả vào `DOMAINCHECK_SECRET_MASTER_KEY` trong `backend/.env`.
Đây là khóa mã hóa secret SMTP/IMAP/proxy và xác thực nội bộ Cloudflare runner;
không cần mua hay đăng ký. Lưu bản sao an toàn ngoài Git. Khi dùng database cũ,
phải giữ nguyên khóa và phiên bản cũ; không tạo khóa mới mỗi lần chạy/deploy.
Đổi khóa làm credential đã lưu không giải mã được; tăng phiên bản không tự xoay khóa.
Thiếu khóa có thể khiến runner thoát và bộ chạy BE dev dừng theo.

Sau khi lưu `.env`, tại thư mục gốc:

```powershell
docker compose --env-file backend/.env -f backend/compose.yaml up -d --wait
cd backend
.venv/Scripts/python.exe -m alembic upgrade head
cd ../frontend
npm ci
```

Migration tạo/cập nhật schema. API sẽ tự seed catalog và role khi khởi động;
không cần chạy `sync-rbac` trong mỗi lần mở dự án.

## 2. Những lần chạy tiếp theo trên Windows

Nếu container DB/Redis đã chạy thì bỏ qua lệnh Docker đầu tiên. Docker Desktop
đang mở chưa có nghĩa là container đã chạy. Từ thư mục gốc kiểm tra/khởi động:

```powershell
docker compose --env-file backend/.env -f backend/compose.yaml up -d --wait
```

Terminal BE, tại thư mục `backend`:

```powershell
.\run_backend.bat --reload
```

Terminal FE khác, tại thư mục `frontend`:

```powershell
npm run dev
```

Mở `http://127.0.0.1:5173`. BE tự mở API, các nhóm worker và Cloudflare runner;
không cần bật từng worker. `--reload` chỉ reload API. Khi sửa worker hoặc `.env`,
dừng và chạy lại toàn bộ BE. Log API và worker xuất hiện trong terminal BE.

## 3. Quản trị viên đầu tiên và cấu hình chức năng

Đăng nhập Telegram một lần bằng tài khoản admin dự kiến; tài khoản mới sẽ ở
trạng thái `pending`. Giữ BE đang chạy, mở terminal khác tại `backend`:

```powershell
.venv/Scripts/python.exe -m domaincheck.cli bootstrap-admin
```

Lệnh dùng subject trong `.env`; có thể chỉ định rõ bằng
`--telegram-subject SUBJECT_OIDC`. Tài khoản phải từng đăng nhập, rồi đăng nhập
lại sau khi cấp admin. Không có admin/mật khẩu mẫu được tạo sẵn.
Admin chỉnh role và duyệt thành viên trong UI. Role `user` mới hiện có quyền
nghiệp vụ gồm gửi mail/report, nên rà soát quyền trước khi duyệt thành viên.

| Muốn kiểm tra | Cần chuẩn bị |
| --- | --- |
| Check Domain, Quick Report, Domain Worker | User active, quyền tương ứng và worker online |
| Chụp ảnh, cloaking, evidence | Chromium đã cài, capture bật và worker liên quan online |
| SMTP/IMAP | Cấu hình mail trong UI; bật `DOMAINCHECK_MAIL_CONNECTIVITY_ENABLED` và khai báo `DOMAINCHECK_MAIL_EGRESS_ENDPOINTS` đúng mẫu trong `.env.example` |
| Cloudflare Worker | Account ID trong Cấu hình mail, Cookie nhập tại trang worker, runner online và master key hợp lệ |
| Submit Netcraft | `DOMAINCHECK_NETCRAFT_SUBMIT_ENABLED=true` khi chủ động muốn gửi thật |
| Extension Quick Report | Cài ZIP từ UI, Options dùng origin dev; đặt ID vào `frontend/.env` qua `VITE_FORM_ASSISTANT_EXTENSION_ID`, rồi restart FE |

Không bật gửi/submit chỉ để kiểm tra khởi động. Preview và quyền gửi là hai bước
riêng; gửi thật/đọc mailbox được thực hiện bằng thao tác chủ động trong UI.

## 4. Kiểm tra, dừng và cập nhật dev

Kiểm tra API bằng PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/live
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready
```

`live` xác nhận API còn chạy; `ready` kiểm tra DB/cache. Sau đăng nhập, xem trạng
thái worker trên web; API ready không đồng nghĩa mọi worker đều online.
Nếu job không chạy, xem log BE trước khi tạo lại job.

Dừng FE bằng Ctrl+C. Dừng BE bằng Ctrl+C rồi chờ worker lưu phần đang làm;
không đóng cưỡng bức terminal. Có thể để DB/Redis chạy cho lần sau. Nếu muốn dừng
chúng, từ thư mục gốc chạy:

```powershell
docker compose --env-file backend/.env -f backend/compose.yaml stop db redis
```

Sau khi cập nhật source, dừng BE khi đã rảnh. Từ thư mục gốc chạy lại:

```powershell
backend/.venv/Scripts/python.exe -m pip install -e "./backend[test]"
cd backend
.venv/Scripts/python.exe -m alembic upgrade head
cd ../frontend
npm ci
```

Nếu dependency Playwright thay đổi, cài lại Chromium bằng lệnh ở bước 1.
Sau đó mở lại BE/FE theo bước 2. Giữ nguyên `.env`, master key và dữ liệu.
Không cần chạy migration khi chỉ mở lại cùng một phiên bản source.

## 5. Dev trên Linux

Từ thư mục gốc source, chuẩn bị lần đầu:

```sh
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -e './backend[test]'
backend/.venv/bin/python -m playwright install --with-deps chromium
if [ ! -f backend/.env ]; then cp backend/.env.example backend/.env; fi
if [ ! -f frontend/.env ]; then cp frontend/.env.example frontend/.env; fi
```

Bước cài thư viện hệ thống cho Chromium có thể yêu cầu quyền sudo. Chỉnh `.env`
như bước 1; lệnh tạo khóa cho database mới là:

```sh
backend/.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Tiếp tục từ thư mục gốc sau khi lưu cấu hình:

```sh
docker compose --env-file backend/.env -f backend/compose.yaml up -d --wait
cd backend
.venv/bin/python -m alembic upgrade head
cd ../frontend
npm ci
```

Mỗi lần chạy, terminal BE tại `backend` dùng
`.venv/bin/python -m domaincheck --reload`; terminal FE tại `frontend` dùng
`npm run dev`. Bootstrap admin tại `backend` bằng
`.venv/bin/python -m domaincheck.cli bootstrap-admin`.
Các bước sử dụng, dừng và cập nhật giống Windows, thay đường dẫn Python
`.venv/Scripts/python.exe` bằng `.venv/bin/python`.

Production dùng service độc lập theo [hướng dẫn deploy](team-deployment.md),
không dùng supervisor dev trong container.
