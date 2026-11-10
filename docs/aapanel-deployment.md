# Cài mới trên VPS aaPanel

Mô hình: Cloudflare (nếu có) → HTTPS Nginx aaPanel → HTTP `127.0.0.1:8088` → Caddy frontend/API → FastAPI và worker nội bộ. Một hostname công khai dùng chung FE/API. Cài mới khác với chuyển dữ liệu; hướng dẫn này không di chuyển dữ liệu cũ.

## Chuẩn bị

- Source tại `/www/wwwroot/domaincheck`, website aaPanel không được phục vụ trực tiếp thư mục source này. Document root của site dùng một thư mục trống riêng.
- Python 3 và Docker Compose 2.24.4 trở lên (override dùng `!reset`/`!override`). Không cần PM2/Node/Python venv trên host.
- `backend/.env` chứa cấu hình backend/production; `frontend/.env` chứa `VITE_FORM_ASSISTANT_EXTENSION_ID`. Tạo từ mẫu tương ứng; không commit env hoặc sao chép config.ini.
- Trong backend env: `DOMAINCHECK_ENVIRONMENT=production`, `DOMAINCHECK_PUBLIC_HOST=tool-check.camellrp.com`, `DOMAINCHECK_ALLOWED_ORIGINS=["https://tool-check.camellrp.com"]`, `DOMAINCHECK_TELEGRAM_REDIRECT_URI=https://tool-check.camellrp.com/api/v1/auth/telegram/callback`, `DOMAINCHECK_BROWSER_CAPTURE_ENABLED=true`.
- `DOMAINCHECK_CONTAINER_DATABASE_URL` dùng hostname `db`, tài khoản/database `domaincheck`, mật khẩu khớp POSTGRES_PASSWORD và URL-encode. Các giá trị Telegram client ID/secret, bootstrap subject và master key phải đầy đủ. Bootstrap subject là subject OIDC của admin, không phải username Telegram. Giữ master key an toàn và không tự đổi khi đã có dữ liệu mã hóa.
- DNS bản ghi A `tool-check` có Content là IPv4 public VPS; khi Cloudflare Proxy bật, DNS public trả IP Cloudflare là bình thường. Callback HTTPS đã đăng ký tại Telegram.

## Kiểm tra cấu hình chưa chạy dịch vụ

Từ root source sau khi đã cập nhật các file aaPanel:

```sh
python3 backend/scripts/deploy_team.py --aapanel --dry-run
cd backend
dc() { docker compose --env-file .env --env-file ../frontend/.env -f compose.yaml -f compose.production.yaml -f compose.aapanel.yaml "$@"; }
dc config --quiet
```

`config --quiet` kiểm tra cú pháp và biến bắt buộc mà không in secret; không kiểm tra kết nối hay bảo đảm mọi giá trị ứng dụng hợp lệ. Không chia sẻ đầu ra `config` đầy đủ hoặc nội dung env. Dry-run chỉ in kế hoạch, không khởi chạy process.

Override aaPanel loại port host của DB/Redis và thay toàn bộ port web bằng `127.0.0.1:8088:80`, tránh trùng Redis host và Nginx 80/443. Caddy nhận riêng địa chỉ `http://HOSTNAME` trong container web để không tự cấp TLS/redirect HTTPS; backend env vẫn giữ hostname không scheme và public origin HTTPS. Nginx aaPanel chịu trách nhiệm TLS/redirect bên ngoài. Bản Compose standalone vẫn giữ hành vi cũ.

## Build và chạy lần đầu

Chỉ cài mới và sau khi hoàn tất kiểm tra trên; từ root source:

```sh
python3 backend/scripts/deploy_team.py --aapanel
```

Helper build, mở DB/Redis, chạy migration rồi mở API/worker/web. Với lần cập nhật sau này, dừng nhận công việc mới và đợi các delivery hiện tại hoàn tất trước thao tác deploy/drain; chuẩn bị backup DB/artifact/key theo team-deployment.md. Không khởi động worker riêng ngoài stack. Luôn dùng `--aapanel` cho VPS aaPanel, kể cả những lần cập nhật sau, để giữ đúng override.

Từ backend với hàm dc ở trên:

```sh
dc ps -a
dc logs --tail=100 migrate api web
curl -fsS -H 'Host: tool-check.camellrp.com' http://127.0.0.1:8088/api/v1/health/ready
```

Migrate exit 0 là bình thường. Readiness thành công xác nhận API/DB/Redis, không bảo đảm từng worker có heartbeat. Không mở cổng 8088 công khai hoặc bật dịch vụ Redis/DB khác cho ứng dụng.

## Website và HTTPS trên aaPanel

1. Website → thêm site `tool-check.camellrp.com`, chọn static/PHP không cần database, document root riêng (không phải thư mục source).
2. Cài chứng chỉ TLS hợp lệ trên site. Nếu Cloudflare Proxy gây trở ngại cho HTTP validation, dùng DNS validation; Origin Certificate cũng phù hợp khi luôn đi qua Cloudflare nhưng truy cập trực tiếp cần trust tương ứng. Không tắt TLS verification.
3. Bật redirect HTTP → HTTPS cho site; Cloudflare chọn Full (strict), không dùng Flexible.
4. Reverse Proxy path `/`, Target URL `http://127.0.0.1:8088`, Send Host `tool-check.camellrp.com`. Tắt proxy cache cho toàn site để tránh cache API/session/ZIP cũ. Đặt proxy read timeout 300 giây và giới hạn upload tối thiểu 40 MB cho form có nhiều evidence. Chỉ sửa site này, kiểm tra Nginx config hợp lệ trước reload.
5. Kiểm tra web và `https://tool-check.camellrp.com/api/v1/health/ready`. Không phục vụ source/env hoặc dùng direct PHP project để chạy Python.

## Admin và extension

Đăng nhập Telegram lần đầu tạo user pending, sau đó từ backend chạy:

```sh
dc exec api python -m domaincheck.cli bootstrap-admin
```

Đăng nhập lại, hoàn thiện hồ sơ, rà quyền và kiểm tra cảnh báo worker. Cấu hình mail/egress theo nhu cầu, không tự gửi report để thử. Extension trên Chrome lưu Application origin `https://tool-check.camellrp.com`; ID phải khớp build FE. Với team, ID unpacked có thể khác giữa máy; cần cơ chế phân phối giữ ID ổn định như team-deployment.md.

Các lệnh vận hành dùng đúng hàm dc ba compose file. Không dùng plain deploy helper thiếu `--aapanel` hoặc các ví dụ standalone hai compose file cho stack aaPanel. Không `down -v`, không xóa volume, không đổi master key trong cập nhật thường ngày.

Nguồn chính thức: [aaPanel Proxy Project](https://www.aapanel.com/docs/Function/proxy.html), [Docker Compose merge](https://docs.docker.com/reference/compose-file/merge/), [Caddy site addresses](https://caddyserver.com/docs/caddyfile/concepts#addresses).
