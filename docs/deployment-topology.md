# Backend, frontend và worker

Source được hỗ trợ chỉ nằm trong backend/ và frontend/. Streamlit/desktop/CLI
cũ đã được gỡ; engine dùng chung nằm trong package domaincheck.engine, không
phụ thuộc file ở root. Xem [hướng dẫn kỹ thuật](technical-guide.md).

Frontend build thành static assets, bao gồm favicon và downloads/extension ZIP.
Reverse proxy phục vụ FE và chuyển /api tới API. API và worker là các process
của cùng release backend, dùng chung PostgreSQL, Redis, private artifact volume
và secret envelope. Cloudflare Cookie runner là process riêng giữ session trong
RAM; không đưa Cookie vào DB/log/source.

`backend/compose.yaml` cung cấp PostgreSQL/Redis local. Bộ deploy team dùng thêm
`backend/compose.production.yaml` cho API, worker theo loại job, Cloudflare runner
và FE/Caddy HTTPS. Xem [hướng dẫn deploy team](team-deployment.md).
Dùng `docker compose -f backend/compose.yaml ...` cho DB/cache local;
project name, volume name và đường dẫn backend/.env được giữ nguyên. Không xóa
volume để dọn source hoặc tạo stack khác tên làm ứng dụng trông như mất dữ liệu.

Cài wheel/package mới, chạy migration/seed hiện hữu rồi thay API/worker cùng
version tại thời điểm an toàn. Không restart worker đang có job hoặc reset
delivery UNKNOWN/sent. Cấu hình production dùng HTTPS, origin chính xác, SMTP/
IMAP allowlist, storage bền vững và browser runtime phù hợp. Không bật submit
để smoke test. Source cleanup không tự deploy hoặc thay đổi DB production.

Các secret/runtime/build artifact còn ở root là dữ liệu giữ nguyên, không phải
dependency của release web. Không đóng gói chúng. Extension source và ZIP nằm
trong FE; bản sao installed-path ignored chỉ phục vụ Chrome profile đang dùng.

[Báo cáo cutover](testing/web-only-cutover-2026-10-18.md).
