# Migration dữ liệu legacy

Chưa có migration runtime nào được apply. Alembic `0001_foundation` chỉ được
kiểm trên PostgreSQL test rỗng: upgrade tạo 24 bảng, `alembic check` không drift,
downgrade xóa hết bảng nghiệp vụ.

Importer legacy thuộc MIG-01 và phải có `--dry-run` mặc định, source root và
legacy owner explicit, fingerprint/checkpoint, quarantine, source precedence
theo `_runtime_path()` và apply idempotent. `config.ini` chỉ được import bằng CLI
cục bộ, mã hóa ngay và không in secret. Không lấy mtime làm business day khi
source không có căn cứ.

Cutover thật cần dừng writer legacy đúng scope. Rollback sau external send phải
giữ delivery truth PostgreSQL; không quay về CSV cũ làm nguồn dedupe. Các lệnh
Alembic hiện chỉ dành cho DB dev/test do operator tự cấu hình.

