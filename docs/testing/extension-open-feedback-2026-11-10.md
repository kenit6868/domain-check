# Xác nhận mở form chậm và thông báo lỗi

Toast “Thao tác chưa hoàn tất…” là fallback cho Error không thuộc ApiError.
Nó không đủ xác định lỗi VPS cụ thể. Kiểm tra source phát hiện bridge dùng cùng
deadline 2.5 giây cho ping và mở form, trong khi background exchange HTTP có
timeout 15 giây. Có thể báo thất bại sớm dù extension vẫn đang xử lý/mở tab.

Sửa FE: mở form chờ 20 giây; ping giữ 2.5 giây. Quá hạn hướng dẫn kiểm tra tab
và trạng thái Form, không tự replay/tạo task lại. Thông báo kết nối do UI soạn
được đánh dấu UserMessageError để toast hiển thị đúng; raw response/error vẫn
bị thay bằng nội dung an toàn. Không sửa BE/extension/profile/env hoặc layout.

Kiểm thử:

- Phản hồi mở form sau 3 giây được nhận thành công, gửi message đúng một lần.
- Chưa xác nhận trước 20 giây không bị timeout sớm; đủ 20 giây báo chưa xác
  nhận và không phát message lại; không đưa capability vào thông báo.
- Network reply có chuỗi nhạy cảm giả chỉ tạo hướng dẫn an toàn.
- Toast cho UI-authored error hiện hướng dẫn; Error/ApiError raw không lộ.
- Giữ Netcraft và comparison-draft request pending, Google vẫn đăng ký device,
  tạo task và gọi extension thành công; nút gửi Netcraft vẫn khóa khi chưa có state.

Full FE 213/213 đạt. Build/typecheck/lint được chạy; typecheck lượt đầu bắt
unknown đưa vào RegExp.test, đã chuyển sang biến string chuẩn hóa. Lint lượt
đầu bắt kiểu rejection callback/matcher trong test mới, đã sửa không bỏ rule.
Không kiểm tra trực tiếp phiên Chrome/VPS hoặc gửi report thật. Đây là sửa một
lỗi timeout tái hiện bằng fixture; không khẳng định mọi lần lỗi VPS cùng nguyên nhân.

Sau push/pull chỉ build/recreate web và tải lại trang. Không cần đổi extension
hoặc restart backend/worker cho bản sửa này.
