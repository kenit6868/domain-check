# Cloudflare Worker: cache theo ngày và xóa cache — 2026-10-20

Trước thay đổi, precheck loại URL có kết quả SUBMITTED/ALREADY_SUBMITTED trong
toàn bộ lịch sử của owner. Xóa cache chỉ ẩn workspace, nên không cho check mới.

Sau thay đổi:

- Chống trùng kết quả đã gửi theo owner, ngày ở múi giờ tài khoản và cache epoch.
- Sang ngày mới cho check mới; xóa cache hôm nay đổi epoch, cho check và xác nhận
  gửi lại trong ngày. URL lặp trong chính nội dung nhập vẫn được loại.
- Bản ghi, mã report và payload lịch sử được giữ nguyên; batch đã xóa không gửi tiếp.
- UNKNOWN/SUBMITTING vẫn khóa xuyên ngày và reset, cần đối soát trước khi gửi lại.
- Cloudflare vẫn có thể trả về ALREADY_SUBMITTED theo chính sách riêng.

Migration `0007_cloudflare_cache_epoch` thêm cột epoch với mặc định rỗng cho dữ
liệu cũ và mở rộng unique constraint. Không đổi hash nội dung hoặc engine version.
Chạy migration trước khi khởi động API/runner mới, sau khi job đang chạy kết thúc.
Không áp dụng migration hoặc restart hệ thống thật trong lượt này.

Kiểm thử dùng PostgreSQL 16 và Redis 7 riêng trên loopback 55437/55438, mock
checker/submitter, artifact tạm; không probe domain thật hoặc gửi report/email.

- Trực tiếp Cloudflare cuối cùng: 25/25 đạt. Bao gồm chống trùng trong ngày,
  đổi ngày, reset nhiều lần, ALREADY_SUBMITTED, kiểm tra thực sự chạy lại,
  lịch sử bất biến, batch cũ bị chặn, UNKNOWN được giữ khóa.
- Toàn bộ backend: 404 test trong 207.058 giây, 403 đạt, 1 skip do chưa truyền
  wheel. Lượt tổng bắt đầu trước khi bổ sung test ALREADY_SUBMITTED cuối cùng;
  test bổ sung được bao phủ trong lượt trực tiếp 25/25 phía trên.
- Build wheel mới bằng pip build isolation đạt; packaging 3/3 đạt, bao gồm test
  wheel đã skip ở lượt tổng. Lần thử không dùng build isolation thất bại vì môi
  trường dev thiếu hatchling; build isolation cài dependency tạm và xử lý được.
- Alembic upgrade head trên PostgreSQL sạch đạt; alembic check không có schema drift.
- compileall (src/tests/migrations), pip check và git diff --check đạt.
- Không đổi frontend, không chạy lại FE test/build/lint.

Wheel được tạo ngoài repo trong thư mục tạm. Hai container kiểm thử được dọn sau
khi hoàn tất; không thay đổi PostgreSQL/Redis, secret hoặc worker hiện hữu.

## Áp dụng local theo yêu cầu tiếp theo của người dùng

Người dùng đã yêu cầu áp dụng trực tiếp. Trước khi thao tác, backend không có
process Python/listener đang chạy; DB không có job queued/running/stopping,
batch Cloudflare hoạt động hoặc record SUBMITTING. Đã chạy migration từ 0006
lên 0007 trên DB local được cấu hình, rồi mở supervisor `python -m domaincheck
--reload` ở chế độ nền ẩn. Không thay đổi secret hoặc xóa cache của người dùng.

Xác minh: DB có revision 0007 và cột cache_epoch; API `/api/v1/health/ready`
trả HTTP 200 ready; Cloudflare runner `/health/live` trả HTTP 200 ok. Redis
heartbeat xác nhận cả năm nhóm worker ready (2/2/2/2/1 slot) và runner ready.
Không tự check URL hoặc gửi report để kiểm tra triển khai.
