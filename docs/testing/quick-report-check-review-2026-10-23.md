# Review nút Kiểm tra của Quick Report — 2026-10-23

## Kết luận

Luồng thông thường đúng, nhưng chưa đúng đầy đủ ở đầu vào, đồng bộ kết quả và
phục hồi sau gián đoạn. Có 5 nhóm vấn đề dưới đây. Lượt này chỉ review, không
sửa source ứng dụng hoặc triển khai.

## Luồng đã xác nhận

1. Trang kiểm tra quyền xem và mức đầy đủ của hồ sơ tổ chức.
2. Lọc danh sách chạy local; chỉnh input sẽ bỏ danh sách đã chuẩn bị.
3. Kiểm tra gửi POST `/api/v1/quick-reports/checks`, tối đa 100 đầu vào.
   Backend kiểm tra quyền check, tài khoản active, Origin, CSRF và hồ sơ.
4. Backend chuẩn hóa URL, loại trùng trong request, tạo Check/Job/JobItem theo
   owner và ngày theo timezone tài khoản. API trả 202; không chạy probe tại POST.
5. Worker đọc cache theo owner/ngày/engine/URL hoặc gọi adapter. Adapter kiểm tra
   WHOIS/CDN, contact và cloaking HTTP; không gửi report chỉ vì bấm Kiểm tra.
6. FE lưu operation ID theo user trong sessionStorage và poll job active mỗi
   2 giây. Daily history độc lập, lấy kết quả mới nhất theo workflow identity.
   Reload chỉ GET, không tự POST lại. Mutation mặc định không retry.

Scheme và port không có ý nghĩa khi loại trùng workflow là hành vi được ghi rõ
trong code/test hiện tại; review này không coi riêng hành vi đó là lỗi mới.

## Q01 — Cao: bộ lọc cắt URL hợp lệ ở dấu phẩy/chấm phẩy

`parseTargets.ts:23` tách theo newline, dấu phẩy và chấm phẩy ở mọi vị trí.

- `https://example.test/path?q=a,b` thành URL hợp lệ
  `https://example.test/path?q=a`, còn `b` bị bỏ.
- `https://example.test/path?a=1;b=2` bị cắt tương tự.

Người dùng bấm Kiểm tra sẽ kiểm tra URL khác với đầu vào. Cần ưu tiên mỗi dòng
một URL như hướng dẫn trên trang; không tách delimiter bên trong URL.
Đã tái hiện bằng test gọi parser thật.

## Q02 — Cao: trạng thái gián đoạn không được phản ánh đúng

`JobRepository.recover_interrupted_items` đổi JobItem running thành unknown.
`QuickReportService.get` chỉ lấy Check theo check_id, không hợp nhất trạng thái
item. Worker chỉ xử lý tiếp item pending và có thể chốt job partial, trong khi
Check vẫn running. Khi job terminal, polling operation dừng nhưng card còn ghi
worker đang kiểm tra. Đã chạy hai method thật với session/DB được mock:

```text
Interrupted recovery: job=partial item=unknown API check=running
```

Ngay cả khi API trả check unknown, `QuickReportResultCard.tsx:15,39` vẫn ghi
Đang chờ; `QuickReportOperationView.tsx:39` không tính unknown vào kết thúc;
`useQuickReportOperation.ts:38` chỉ đếm failed nên partial không có failed có thể
toast thành công. Đã tái hiện UI unknown bằng fixture, hiển thị chờ và 0/1 URL.

Cần thống nhất trạng thái Check/JobItem khi phục hồi, thể hiện UNKNOWN riêng,
cảnh báo kết thúc không đầy đủ và không tự chạy lại item bất định.

## Q03 — Vừa: daily cũ che check mới của cùng URL

`QuickReportOperationView.tsx:25` luôn giữ daily record khi ID khác với check
trong operation, dù daily response đang cũ hơn. Record đó bị coi là kết quả cũ
ở dòng 34 và ẩn mặc định.

Tái hiện: daily còn check A succeeded; operation mới đã có check B pending cùng
URL. UI ẩn card mới; bỏ checkbox lại thấy A Hoàn tất thay vì B đang chờ. Có thể
gặp trong khoảng đồng bộ lúc kiểm tra lại, sau xóa cache hoặc khi response về
khác thứ tự. Daily poll sau có thể sửa màn hình nhưng không bảo đảm nếu lỗi tải.

Cần metadata thứ tự/thời điểm đủ để so độ mới, hoặc quy tắc merge bảo toàn check
của operation hiện tại mà vẫn xử lý được một operation mới hơn từ tab khác.
Đã tái hiện bằng fixture qua trang thật.

## Q04 — Vừa: mở khóa Kiểm tra khi chưa tải xong operation khôi phục

`QuickReportPage.tsx:61` chỉ khóa input khi create pending hoặc operation đã tải
và active. Khi sessionStorage có ID nhưng GET còn chờ, active=false. Người dùng
có thể lọc và bấm Kiểm tra, tạo operation nữa dù job trước có thể còn chạy.
Nút xóa cache cùng trang đã kiểm tra thêm operationId && !operation.

Đã giữ GET operation ở trạng thái pending bằng mock: sau nhập/lọc, nút Kiểm tra
vẫn enabled. Backend create không có cơ chế idempotency cho request lặp; khóa
Target trong transaction không đồng nghĩa loại trùng operation giữa request.
Cần khóa lúc đang khôi phục và xử lý rõ tình huống POST timeout chưa biết kết quả.

## Q05 — Vừa: Quick Report bỏ fragment của URL

`canonical_target` trong `application/quick_reports.py:46` chỉ dựng lại scheme,
host, port, path và query. Đầu vào `https://example.test/wap/#/register` trở thành
`https://example.test/wap/`. Các hash route khác nhau cũng bị gộp identity.

Đã gọi hàm thật để xác nhận. Probe và nội dung report dùng requested_url đã bị
cắt nên không còn đúng route người dùng nhập. Domain Worker đã hỗ trợ fragment,
nhưng sửa ở workflow đó không áp dụng sang Quick Report. Cần giữ route trong
requested_url và xác định rõ identity tương ứng, giữ ranh giới egress hiện có.

## Kiểm thử và giới hạn

- Bộ FE hiện có `tests/quick-report.test.tsx`: **21/21 đạt**.
- Test chẩn đoán tạm: **4/4 đạt**, xác nhận hành vi lỗi hiện tại ở Q01, UI Q02,
  Q03 và Q04. Test được dọn sau review; không sửa test sản phẩm để hợp thức lỗi.
- Backend `test_quick_reports.py`: **5 đạt, 3 skip**, do chưa cấu hình PostgreSQL
  kiểm thử cô lập. Không coi lượt này là kiểm thử integration DB thành công.
- Q02 recovery và Q05 canonicalization: script mock/local gọi method thật đạt
  kết quả tái hiện nêu trên; không dùng DB hoặc mạng thật.
- Chưa chạy toàn bộ backend/FE, build/lint vì không thay đổi source ứng dụng.
- Không đọc secret, gửi report/mail, truy cập IMAP, probe URL thật hoặc
  khởi chạy/restart worker. Chỉ thêm báo cáo này.

Ưu tiên sửa Q01/Q02 trước, sau đó Q03/Q04 và Q05. Các thay đổi backend khi sửa
cần test trực tiếp và toàn bộ unittest theo hướng dẫn dự án.
