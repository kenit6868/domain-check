# Link Status: sửa WHOIS subprocess trong lượt kiểm tra tổng — 2026-10-23

## Nguyên nhân và thay đổi

API gọi `python -m domaincheck.core.link_status` để chạy probe có deadline.
Trong process này, `__name__` là `__main__`. WHOIS fallback trước đó lại dựng
lệnh bằng `__name__`, thành `python -m __main__ whois`. WHOIS con không chạy
đúng module, stdout không có kết quả nên registration giữ UNKNOWN.

Điều này giải thích tại sao lookup riêng có trạng thái nhưng cùng tên qua luồng
Link Status tổng lại không có trạng thái WHOIS. Các test trước gọi hàm sau khi
import hoặc mock kết quả subprocess nên không phát hiện khác biệt entrypoint.

Sửa cả lệnh probe tổng và WHOIS con dùng tên module cố định
`domaincheck.core.link_status`. Giữ timeout WHOIS 9 giây, probe tổng 45 giây,
validation domain/egress, phân loại hold, DNS/HTTP và quyền/CSRF như hiện có.
Không đổi engine/policy version, DB hoặc workflow gửi report.

## Regression và kiểm thử trực tiếp

Test mới thực thi process thật bằng `python -m domaincheck.core.link_status`
từ working directory tạm; process đó thực thi WHOIS child thật. File
`sitecustomize.py` tạm, chỉ được đưa vào PYTHONPATH của test subprocess, cung cấp
WHOIS/DNS/HTTP fixture và chặn socket kết nối thật. Không sửa môi trường chạy
ứng dụng hoặc tạo worker/service thật.

Bốn tình huống: public clientHold, private radio.am serverHold, private
africa.com clientTransferProhibited và public ok. WHOIS phải về HOLD/REGISTERED,
source WHOIS, giữ đúng status/domain; DNS vẫn RESOLVES và HTTP fixture TIMEOUT.
Không có hold thì tổng kết TIMEOUT, có hold thì tổng kết DOMAIN_HOLD; không làm
mất quan sát HTTP độc lập.

- Trước sửa: test mới thất bại **4/4 subcase**, nhận UNKNOWN thay HOLD/REGISTERED.
- Sau sửa: toàn `test_link_status_web.py` **25/25 đạt**, gồm test subprocess mới.
- Compileall, pip check và git diff --check đạt.

Toàn backend: **433/433 đạt, 0 skip**, trong **265.942 giây**, trên PostgreSQL 16
và Redis 7 riêng ở loopback 55467/55468. Migration chỉ chạy trong DB test;
packaging test bật bằng wheel vừa build, SHA256
`aefb74299cf0de2c96507404603206edbb07321ea932f1f881922363f933a04f`.
Log/wheel ở `%TEMP%/domaincheck-link-entrypoint-20261023/`; hai container test
được dọn sau kiểm thử. Không thay FE nên không chạy lại FE test/build/lint.

## Phạm vi và triển khai

Chỉ sửa cách Link Status gọi tiến trình; Quick Report, Check Domain và Domain
Worker có cách gọi WHOIS khác. Bản sửa không làm website timeout thành HTTP OK,
không suy luận thiếu WHOIS là domain bị hold và không xác nhận mọi registry
ngoài mạng đều hoạt động.

Không cần migration hoặc build FE. Nạp lại API khi triển khai rồi kiểm tra lại
URL. Không restart dịch vụ đang dùng, truy cập website thật hoặc gửi mail/report
để kiểm thử trong lượt sửa này.
