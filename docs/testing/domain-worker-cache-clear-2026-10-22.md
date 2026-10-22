# Domain Worker: xóa cache không có email — 2026-10-22

## Hành vi

- Nút **Xóa cache không có email** cạnh **Precheck danh sách** gọi
  `POST /api/v1/domain-worker/cache/clear`, dùng quyền `domain_worker.clear_cache`
  đã có, kiểm tra trạng thái active, Origin và CSRF tại backend.
- Chỉ bỏ hiệu lực kết quả `excluded_no_email` thành công trong ngày của owner
  theo timezone tài khoản. Không xóa Check/result, evidence, review hoặc delivery.
  Marker trong cache provenance được lưu trong DB, không cần migration/Redis.
- Xóa và tạo precheck dùng cùng owner batch lock. Batch đang chạy, tạm dừng hoặc
  chờ gửi chặn xóa; FE khóa nút và backend trả 409 khi có race giữa các tab.
- Không xóa thì vẫn dùng lại kết quả không có email như trước. Xóa không tự tạo
  job: người dùng bấm precheck lại sau đó. Sent/sending/unknown vẫn được bảo vệ.

## Kiểm thử

- Backend trực tiếp `test_worker_send.py`: 28/28 đạt, gồm invalidation, giữ nguyên
  kết quả lịch sử, xóa lặp lại, scope owner/ngày, sent/unknown và chặn active batch.
- Bổ sung kiểm tra sau khi xóa: worker mock trả email mới, precheck mới nhận email
  mới; chạy trực tiếp test này đạt.
- Permission/Origin/CSRF được kiểm tra tại endpoint mới trong `test_domain_worker`.
- Toàn backend trên PostgreSQL/Redis riêng: 409 test trong 225.971 giây,
  408 đạt, 1 skip vì chưa truyền wheel trong lượt tổng. Packaging chạy bù với
  wheel mới: 3/3 đạt, bao gồm test đã skip.
- FE Domain Worker: 25/25 đạt; toàn FE: 162/162 đạt. Test nút xác nhận không tự
  xóa khi mở trang, không tạo precheck khi xóa và giữ nguyên danh sách đang nhập.
- Build/typecheck/lint, compileall, pip check và git diff --check đạt.
  FE build ra thư mục tạm, không ghi đè dist hiện có.

PostgreSQL 16/Redis 7 kiểm thử riêng trên loopback 55437/55438; chỉ migration DB
kiểm thử. Wheel/build/log nằm trong thư mục tạm. Không đọc secret, gửi mail/report,
đọc IMAP, probe URL thật hoặc restart dịch vụ đang dùng. Hai container kiểm thử
được dọn sau khi hoàn tất. Các thay đổi fragment/review FE từ phiên trước được giữ.

Khi triển khai cần nạp lại code BE/worker sau khi job hiện tại hoàn tất và build
FE mới. Không đổi grant hoặc chạy migration trên dữ liệu đang dùng trong lượt này.
