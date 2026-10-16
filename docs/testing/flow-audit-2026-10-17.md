# Kiểm tra luồng Domain Worker và Review — 2026-10-17

## Phạm vi

Kiểm thử PostgreSQL riêng, artifact trong thư mục tạm, SMTP/capture mock. Không
gửi mail, mở IMAP, probe URL hoặc sửa dữ liệu vận hành. Frontend chạy toàn bộ
Vitest cùng typecheck/lint/build. Backend chạy toàn bộ unittest và wheel cô lập.

## Phát hiện và sửa

Lớp lọc precheck chỉ thu thập recipient từ Check `ready`/`excluded_already_sent`.
Check cloaking giữ đúng disposition `cloaking_review` sau gửi trực tiếp, nên domain
đã gửi đủ từ Review vẫn bị precheck lại. `filter_sent_inputs` nay thu thập route
của cả hai disposition Review, rồi đối chiếu ledger chung theo owner/ngày/domain/
account/channel/recipient. Chỉ đủ mọi delivery `sent` mới loại domain; failed,
missing hoặc một account thành công chưa đủ. Không thay disposition hoặc approval.

## Kịch bản được kiểm chứng

| Luồng | Kiểm chứng |
| --- | --- |
| Tài khoản + input → precheck | Chuẩn hóa/lọc trùng; không email không capture; ready cần ảnh; cloaking capture candidate chưa approve |
| Precheck → gửi thường | Giữ account scope; cấu hình batch; tiến độ/countdown; checkpoint từng thư |
| Thiếu ảnh → Evidence Review | Upload atomic; exact preview; xác nhận; gửi trực tiếp; dùng lại ảnh qua reload |
| Cloaking → Review | Cặp profile; confirmed/not_cloaking với evidence riêng; skip; không auto-send |
| Hai tài khoản và lỗi một phần | Retry chỉ phần lỗi; giữ Message-ID; không gửi lại sent |
| Timeout/worker chết | UNKNOWN riêng; không retry mù; lease hết hạn không khóa vĩnh viễn |
| Reload/nhiều tab | Backend khôi phục batch; chặn job xung đột; khóa theo owner/case |
| Pause/resume/cancel | Giữ ledger/progress; hủy hẳn không resume/retry |
| Nhập lại sau cả hai Review | Domain đủ sent bị loại trước precheck; no-email tái dùng đúng ngày; không tạo job rỗng |
| Phạm vi | Thiếu một account còn xử lý; dữ liệu hôm qua không lọc input hôm nay |

`backend/tests/test_worker_journey.py` ghép các nhánh trong một lượt: bốn domain,
hai tài khoản, gửi batch và cả hai Review, retry hai nhánh, kiểm tra sáu delivery
thành công, queue rỗng, workspace mở lại, rồi nhập lại toàn bộ không tạo thêm job.
Các regression riêng kiểm tra authz/owner/day, hash thay đổi, concurrency và crash.

## Kết quả

- Frontend: 92/92; typecheck, lint và build đạt.
- Backend trên PostgreSQL sạch sau sửa: 237 đạt/2 bỏ qua (239 test).
- Journey ghép luồng + worker send: 28/28.
- Wheel cô lập: 3/3; compileall và pip check đạt.
- Legacy: lần chạy lại 420 test giữ 4 failure/7 error đã có. Lần đầu có thêm
  PermissionError chập chờn trong test ghi sent_log.csv đồng thời trên Windows;
  lần chạy lại không còn lỗi này. Chưa sửa nhóm legacy trong đợt rà soát web này.

## Giới hạn

Đây là xác nhận logic ứng dụng bằng mock và database thật cô lập. Chưa kiểm tra
đường truyền SMTP/IMAP hoặc việc người nhận nhận thư. Legacy suite vẫn có lỗi
đã tồn tại; kết quả chi tiết được ghi trong AGENTS.md ở đợt kiểm tra này.
