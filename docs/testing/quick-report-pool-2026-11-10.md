# Quick Report — nghẽn pool khi đọc trạng thái Netcraft

Log VPS xác nhận QueuePool size 5 + overflow 2 hết chỗ, timeout 30 giây tại
current_user/Netcraft find. Các GET Netcraft trước đây giữ transaction xác thực
và mở thêm transaction riêng. Nhiều request cùng giữ một slot rồi đợi slot thứ
hai có thể làm nghẽn cả xác thực và form-drafts. Đây là bằng chứng lỗi tài nguyên
ứng dụng; không kết luận an ninh rộng hơn từ log này.

Sửa hai GET trạng thái Netcraft để truyền session request vào get/find. Khi
caller truyền session, service không mở/commit transaction khác. Caller standalone
vẫn có transaction riêng. Giữ owner/day scope, row lock và SENDING → UNKNOWN;
POST preview/submit và checkpoint intent không thay đổi. Không tăng pool.

Kiểm thử trực tiếp trên PostgreSQL cô lập:

- Tái hiện code path cũ: giữ kết nối xác thực, gọi find với session riêng trong
  pool 1 slot; nhận PoolTimeout. Truyền session hiện tại thì hoàn tất và vẫn chặn
  owner khác.
- API thật, cookie fixture, xác thực và role thật: 16 request đồng thời gồm hai
  GET Netcraft, form-drafts và /me, pool 1/overflow 0; tất cả trả 200, tài nguyên
  không tồn tại trả 404; kết thúc không giữ kết nối.
- 13/13 test trực tiếp đạt; dùng dữ liệu fixture, chặn network provider trong
  lượt burst, không SMTP/IMAP hoặc gửi report thật.
- Fixture burst ban đầu đặt pool_timeout 0.3 giây gây timeout khi 16 request xếp
  hàng/import lần đầu; điều chỉnh giới hạn fixture thành 3 giây, vẫn chỉ 1 slot.
  Test tái hiện nested checkout riêng vẫn giữ timeout 0.1 giây.
- Fresh wheel build đạt; 3/3 packaging test đạt. Lượt build không isolation ban
  đầu thiếu hatchling; build isolation cài đúng build dependency và thành công.
- compileall và pip check đạt.

Lượt toàn bộ đầu tiên: 450 test, 2 fail và 1 skip packaging. Hai fail ở bảo vệ
admin cuối cùng do lượt test trực tiếp mới để lại một admin fixture trong cùng
DB trước khi chạy suite. Đã sửa fixture dùng role riêng chỉ có hai quyền mà các
API yêu cầu (quick_report.submit_netcraft/open_form), không gán admin. Chạy lại
suite bằng database sạch và bật
fresh-wheel test; không sửa production policy hoặc bỏ test bảo vệ admin.

Kết quả cuối: **450/450 backend test đạt, không skip**, PostgreSQL/Redis cô lập
và wheel mới. Thời gian suite 247.539 giây, gồm hai regression mới, kiểm tra
30 người × 50 domain, owner/RBAC/CSRF và delivery UNKNOWN/không gửi lại.

Không chạy migration/restart/deploy VPS hoặc worker đang có job. Chỉ tạo hai
container kiểm thử mới, DB/Redis riêng trên cổng 55439/56439; không dùng DB local
5432/Redis 6379. Không cần migration/env mới/build FE cho bản sửa.

Sau deploy BE, reload Quick Report nhiều kết quả và mở task Google mới. Kiểm tra
/me, Netcraft/form-drafts trả bình thường và log không còn QueuePool timeout.
Không reset cache, delivery hoặc submit report để kiểm thử.

## Danh sách cuộn và trạng thái gộp

Toàn bộ kết quả nằm trên một trang, giữ thứ tự/tổng tiến độ/bộ lọc cũ. Các nhóm
thao tác chỉ mount khi gần viewport (400px), sau đó được giữ để không mất trạng
thái. Draft chỉ tải khi mở Xem nội dung đối chiếu; các nhóm cùng check dùng
chung query. Autofill không cần tải draft đối chiếu trước.

GET /quick-reports/netcraft-statuses nhận tối đa 50 UUID. Mỗi request dùng một
session chung với auth, chọn check theo owner/workflow và submission theo
owner/ngày/hash, khóa thứ tự ID ổn định, phục hồi UNKNOWN, không đọc artifact/
preview body hoặc gửi provider. Thiếu check hoặc sai owner từ chối cả request.
Response private/no-store. Batcher FE tách theo QueryClient/owner, giữ query key
từng check, chạy từng nhóm lần lượt; mutations không qua batcher.

Fixture không có IntersectionObserver: 100 card vẫn có mặt, 100 status reader
gộp thành đúng 2 GET, 0 draft; mở một nhóm gọi 1 GET draft. Test viewport riêng:
20 header cùng tồn tại, 0 GET trước khi giao nhau; hai card vào viewport tạo
một request 2 ID, cuộn tới card cuối mới phát sinh ID đó. Test batcher xác nhận
max 50 ID, một request đồng thời, hủy trước khi gửi, phản hồi thiếu và phục hồi
sau lỗi. Các khóa SUBMITTED/SENDING/UNKNOWN và API tắt vẫn được giữ.

BE trực tiếp 14/14 đạt, gồm 20 request auth/status/draft/me đồng thời trong pool
1 slot, giới hạn 51 ID trả 422, thiếu check trả 404, owner/day và UNKNOWN.
Lượt FE đầu có 205 đạt/1 timeout ở fixture dựng đủ 100 card không observer;
fixture lớn có timeout riêng 15 giây thay vì 5, không đổi timeout toàn bộ suite.
Lint ban đầu bắt prefer-const và async không await trong test, đã sửa.
Kết quả FE cuối **206/206 đạt**, build/typecheck/lint đạt.

Kết quả BE cuối cho API gộp: **451/451 đạt, không skip**, PostgreSQL/Redis cô
lập và wheel mới, 281.378 giây. Test trực tiếp 14/14 được chạy lại sau khi bổ
sung assertion khác business day; compileall/pip check đạt. Chưa Chrome/VPS smoke.

Cần deploy cả BE và FE cùng phiên bản (BE có endpoint gộp trước khi FE dùng).
Không cần migration/env mới hoặc cập nhật extension. Đây là giới hạn theo client,
không phải throttle toàn hệ thống hay phép đo tải trên VPS thật.
