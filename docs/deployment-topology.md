# Backend, frontend và worker khi deploy

Domain Worker gửi thường dùng worker `--job-type domain_worker_send` hoặc `all`,
precheck riêng dùng `domain_worker_precheck`. Cùng DB, private artifact volume,
secret envelope key và version package với API. SMTP endpoint allowlist hiện có
áp dụng cho gửi; cờ connectivity-test chỉ điều khiển nút thử đăng nhập mail.
Lịch nghỉ batch/checkpoint/ledger nằm DB nên process restart không làm mất tiến độ.
Không chạy job ngày hôm qua trong ngày mới; cloaking không vào sender thường.

Source nghiệp vụ đích nằm trong `backend/` và `frontend/`. Worker là process của
backend, dùng cùng package/version, không phải một dự án thứ ba. Logo đóng gói tại
`backend/src/domaincheck/assets/email/logo-win.jpg`, luôn dùng làm logo mặc định.
Upload thay thế tạo artifact immutable riêng của user; reset quay về logo mặc
định, không bỏ logo. Không đọc config.ini.

Implementation của bốn module `phishing_toolkit`, `cloaking_detector`,
`browser_evidence`, `quick_report_actions` đã ở `backend/compat/`; wheel
force-include lấy từ bên trong backend, dev path không còn trỏ `..`.
Các file root cùng tên là wrapper cho Streamlit/CLI legacy và giữ vị trí runtime
cũ khi chạy legacy. Chưa xóa chúng và các workflow legacy còn lại trước khi hoàn
tất chuyển nghiệp vụ, parity và cutover. Không di chuyển config.ini/runtime thật.
Frontend không import trực tiếp Python hoặc đường dẫn ảnh ở root.

Mục tiêu cuối là hai thư mục source `backend/` và `frontend/`: code, assets,
tests và scripts vận hành thuộc backend/FE tương ứng. Worker là backend process.
Những phần legacy chưa chuyển không bị xóa trong đợt này; đây chưa phải cutover.

## Ý nghĩa các cờ kết nối

| Biến backend | Mục đích | Khi tắt/để trống |
|---|---|---|
| `browser_capture_enabled` | Cho phép worker mở Playwright để chụp Browser Evidence thường | Không chụp tự động; HTTP check vẫn chạy, domain thường vào evidence review |
| `browser_egress_proxy_url` | Proxy HTTP(S) tùy chọn cho browser context của hàm capture legacy chung | Hàm chung dùng browser/HTTP/DNS mặc định, không có guard DNS riêng ở adapter |
| `mail_connectivity_enabled` | Cho phép thao tác thử TLS và đăng nhập SMTP/IMAP | Khóa kiểm tra kết nối, vẫn lưu được cấu hình; cờ này không tự bật gửi/đọc thư |
| `mail_egress_endpoints` | Exact host:port được phép thử (gồm proxy nếu dùng), kèm kiểm tra DNS public | Endpoint chưa khai báo bị chặn |

Tên biến trong `.env` dùng prefix `DOMAINCHECK_` và chữ hoa. Ví dụ endpoint:
`["mail.example.com:465","mail.example.com:993","smtp.example.com:587"]`.
Đây là cấu hình do quản trị viên deploy quản lý, không phải credential tài khoản.
Mục đích là giới hạn nơi server được phép kết nối khi nhận hostname do user nhập.
Thông báo cũ `Mail connectivity requires configured egress policy` có nghĩa là
chức năng đang tắt, chưa có kết nối SMTP nào được thử; không phải lỗi mật khẩu.

## Process thường trực

- Frontend build thành static assets, reverse proxy phục vụ FE và chuyển `/api`
  tới API; browser không truy cập database trực tiếp.
- API nhận thao tác người dùng, lưu Job/JobItem PostgreSQL rồi trả operation ID.
- Worker chạy liên tục, claim job, heartbeat và checkpoint từng URL. Có thể dùng
  cùng image backend với command `python -m domaincheck.workers.cli --job-type all`.
- Browser capture riêng dùng `--job-type browser_evidence` khi bật cấu hình capture;
  môi trường thực thi phải đáp ứng chính sách egress hiện có.
- PostgreSQL giữ job, queue, profile và delivery truth; Redis giữ cache/session.
  API và worker cần truy cập cùng private artifact volume; volume phải tồn tại qua restart.

Không khởi động worker theo từng job. File `.bat` chỉ là shortcut local: mở một
lần, để process chạy rồi tạo nhiều lượt trên giao diện. Khi đóng process thì job
mới chờ ở queued. Khi deploy, dùng Docker Compose `restart: unless-stopped`,
systemd hoặc supervisor để worker tự chạy lại khi server/process restart.

Ví dụ systemd cho worker (đường dẫn minh họa, chưa cài service thực tế):

```ini
[Unit]
Description=Domain Check background worker
After=network.target

[Service]
WorkingDirectory=/opt/domaincheck/backend
EnvironmentFile=/opt/domaincheck/backend/.env
ExecStart=/opt/domaincheck/backend/.venv/bin/python -m domaincheck.workers.cli --job-type all
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Chạy API bằng service/container riêng, không dùng `--reload` trong production.
Process worker cũng chạy độc lập với tab trình duyệt; F5/đóng tab không hủy job.
Pending tiếp tục theo worker; item đang chạy khi lease chết được đánh dấu unknown
và không tự retry. Stop/resume/retry vẫn theo quy tắc workflow hiện có.

`compose.yaml` hiện tại mới chạy PostgreSQL/Redis; chưa phải deployment đầy đủ.
Việc thêm image API/worker/FE, reverse proxy, migration và volume production thuộc
giai đoạn OPS-01. Không tự deploy hay bật gửi SMTP từ tài liệu này.

SMTP/IMAP connectivity: public DNS validation uses the operating-system resolver (the same resolver as mail sockets), with a three-second guard deadline. Exact host:port allowlisting and rejection of any non-public result remain required. DNS failures return `dns_failed`, separately from endpoint/address policy rejection; tests authenticate only and never send/read mail.
