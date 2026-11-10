# Khôi phục giao diện Quick Report

Bỏ nút Xem/Ẩn nội dung đối chiếu, trả nội dung tự hiển thị như trước.
QuickReportResultCard khôi phục nguyên source tại fb82623, gồm padding/markup;
QuickReportProviderActions giữ class layout cũ, chỉ thêm ref điều khiển query.
CSS padding không đổi trong bản tối ưu trước; chưa quan sát trực tiếp lỗi CSS
trên VPS. Không tự nhận định đã sửa lỗi stylesheet/cache production.

Query dữ liệu chỉ bật khi gần viewport 400px. Không trì hoãn render markup,
không skeleton wrapper thay toàn bộ thao tác. Netcraft giữ batch 50 ID, request
session và khóa trạng thái gửi. Draft tự tải với tối đa 2 request đang chạy;
queued request đã hủy không chạy, lỗi không giữ slot vô hạn. Mỗi HTTP request
vẫn có timeout 30 giây. Đây là giới hạn client, không phải throttle toàn hệ thống.

Kiểm thử cuối: 208/208 FE đạt, build/typecheck/lint đạt. Fixture fallback 100 card
có đủ nội dung tự động, 2 request trạng thái, 100 draft được đọc lần lượt; unit
test xác nhận peak draft requests = 2. Test viewport xác nhận chưa nhìn thấy thì
chưa gọi trạng thái, cuộn đến mới tải, nút gửi chưa có dữ liệu vẫn bị khóa.
Không đổi BE/extension/env, gửi report, restart hoặc deploy dịch vụ thật.

Sau push/pull chỉ build và recreate web; Ctrl+Shift+R, kiểm tra nhóm báo cáo và
draft tự xuất hiện, không còn nút Xem nội dung đối chiếu. Nếu padding vẫn sai,
cần ảnh hoặc computed style từ VPS để xác định phần stylesheet cụ thể.
