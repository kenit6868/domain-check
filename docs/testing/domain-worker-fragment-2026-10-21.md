# Domain Worker: URL có fragment — 2026-10-21

## Thay đổi

Precheck trước đây dùng validator Check Domain từ chối mọi fragment, nên một URL
như `https://example.com/wap/#/register` làm cả danh sách bị từ chối trước khi tạo job.

Domain Worker nay bật hỗ trợ fragment riêng trong validator dùng chung. Giữ nguyên
path/query/fragment trong identity, Check, đầu vào adapter, review và browser
navigation. Cloaking không còn bỏ fragment trong bước chuẩn hóa. Các caller khác
của validator giữ chính sách đầu vào hiện có; không đổi identity hoặc version của
dữ liệu lịch sử. Không cần migration hoặc build frontend.

Public egress vẫn kiểm tra scheme/host/port/DNS và chặn địa chỉ riêng, redirect
không an toàn, userinfo (kể cả userinfo rỗng). Fragment không đổi đích mạng.
Redaction URL evidence hiện có được giữ nguyên.

## Kiểm thử

- Domain Worker: 15/15 đạt, gồm distinct hash routes, loại URL trùng chính xác,
  giữ route khi gọi precheck/capture, review và kiểm tra an toàn.
- Networking: 10/10 đạt, gồm fragment trên public host và chặn private DNS,
  private IP, port không cho phép, userinfo, scheme ngoài HTTP(S).
- Cloaking: 32/32 đạt, gồm Playwright giả lập nhận nguyên `#/register`.
- Bộ kiểm tra đầu vào nhận và giữ nguyên 9/9 URL trong danh sách người dùng;
  chỉ parse/validate, không gọi mạng.
- Toàn bộ backend: 408 test trong 237.029 giây, 407 đạt, 1 skip vì lượt tổng
  chưa truyền wheel. Build wheel mới bằng pip build isolation đạt; packaging 3/3
  đạt, bao gồm test wheel đã skip, chạy ngoài repository.
- compileall, pip check và git diff --check đạt.
- Không đổi frontend; không chạy lại FE test/build/lint.

PostgreSQL 16/Redis 7 riêng trên loopback 55437/55438, migration đến head chỉ trên
DB kiểm thử; checker, capture, SMTP/provider đều dùng fixture/mock. Không truy cập
các URL nghi vấn, gửi email/report hoặc đọc IMAP. Hai container kiểm thử được dọn
sau khi hoàn tất; không sửa secret/runtime/DB đang dùng hoặc restart worker thật.

## Nạp code vào BE đang chạy

Người dùng tự chạy BE theo yêu cầu trước đó. Lượt sửa này không restart supervisor
hoặc worker. Sau khi hết job, Ctrl+C và chờ thoát rồi mở lại bằng lệnh đang dùng
(`python -m domaincheck` từ backend, hoặc `backend/run_backend.bat`). API reload
không tự reload worker. Sau đó precheck lại danh sách; không cần xóa lịch sử/cache.
