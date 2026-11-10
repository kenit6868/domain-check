# Verisign DNS Abuse — 10/11/2026

Extension 3.1.5 thêm adapter `verisign_dns_abuse`, nối routing registry, schema FE/API và task exchange. Registrar/target_domain lấy từ check của owner và snapshot vào task hiện có; payload cũ mặc định rỗng, không cần migration. Catalog .com/.net/.name/.cc giữ nguyên; các namespace private có registry riêng không chuyển sang Verisign.

## Kiểm tra form thật

Mở `https://verisign.my.site.com/DNSAbuse` bằng Chromium headless cô lập, xác nhận redirect `/ReportAbuse/` và native controls nằm trong nhiều lớp open shadow DOM của Salesforce Lightning. Chỉ đọc DOM, chọn DNS Abuse và thử điền dữ liệu mẫu `example.com`/`reporter@example.test` với tên registrar, nội dung giả. Không dùng cấu hình thật hoặc truy cập website nghi vấn.

Adapter chạy trên trang thật trả `valid: true`, đồng thời kiểm tra lại giá trị DOM:

| Field | Giá trị mẫu |
|---|---|
| Type of Abuse | DNS Abuse |
| DNS Abuse Category | PHISHING |
| Domain | example.com |
| TLD | com |
| URL | URL mẫu đầy đủ, giữ fragment |
| observedDate / notifiedRegistrarDate | 11/10/2026 (MM/DD/YYYY) |
| notifiedRegistrar | Fixture Registrar |
| reporterEmail / description | Email và nội dung fixture |

Hai ngày được chọn bằng click ô ngày rồi nút Today trong đúng `lightning-datepicker`. Không bấm Next, không gửi report, không thao tác CAPTCHA/upload hoặc profile Chrome. Kết quả chỉ xác minh bước điền form; không xác minh submission hay các bước sau Next. Ngày sử dụng ngày địa phương của trình duyệt; người vận hành kiểm tra ngày quan sát và thông báo registrar trước khi tiếp tục.

## Kiểm thử

- Frontend toàn bộ: 30 file, **177/177 đạt**. Fixture mới kiểm tra shadow DOM, form xuất hiện sau lựa chọn, Today, full URL, registrar tùy chọn, giá trị người dùng, TLD/nội dung không hợp lệ, nhiều form mơ hồ và giới hạn origin/path. Test background kiểm tra truyền registrar/target_domain và quyền host.
- Lượt FE đầu có một assertion version còn cố định 3.1.4; đã cập nhật thành 3.1.5, chạy lại toàn bộ đạt. Sau bổ sung assertion payload/quyền host, 10/10 test adapter/background đạt.
- Backend toàn bộ: **445 test, 260 đạt, 185 skip**, không lỗi. Skip do thiếu PostgreSQL/Redis kiểm thử cô lập; không chứng minh integration DB thật. Test form-task trực tiếp: 9 đạt, 2 skip. Test mock kiểm tra snapshot/exchange trường mới và mặc định payload cũ.
- Build/typecheck, lint, compileall, pip check và git diff --check đạt.
- Build tạo `frontend/dist/downloads/web-form-assistant.zip` bản 3.1.5.

Không chạy migration, restart API/worker, cập nhật bản extension đang cài hoặc triển khai dịch vụ. Để dùng: triển khai code API/FE, giải nén ZIP vào cùng thư mục extension và Reload thủ công. Next, CAPTCHA, upload và Submit vẫn thủ công.

## Sửa lỗi sau phản hồi — extension 3.1.6

Kiểm tra 3.1.5 trước đó chỉ chạy adapter trong page world nên chưa chứng minh hoạt động trong môi trường cô lập của content script. Đã tái hiện lỗi ngày bằng CDP `Page.createIsolatedWorld`: Salesforce synthetic shadow được nhìn như light DOM, `getRootNode().host` rỗng nên hai ô ngày không được chọn. Bản sửa đi qua cả parentElement và native shadow host để tìm đúng lightning-datepicker rồi click Today.

Sau chọn Phishing, form xuất hiện input `phishingDetails` với nhãn Brand name or website being impersonated. Bản 3.1.6 chọn Phishing trong bước chờ, chờ field này rồi điền `brand_name`. Registrar/target_domain trước đó mới lưu trong background storage; đã bổ sung vào response `form-task-for-tab` cho content script. Regression test kiểm tra cả exchange → storage → content response, không chỉ storage.

Trên form thật, chạy adapter 1.0.1 trong isolated world với dữ liệu giả, chờ thêm một giây, xác minh `valid: true` và đọc lại bằng Playwright ở page world:

- `phishingDetails`: Fixture Brand.
- `notifiedRegistrar`: Fixture Registrar.
- `observedDate` và `notifiedRegistrarDate`: 11/10/2026.

Không bấm Next/Submit/CAPTCHA/upload. Fixture test bao gồm datepicker trong native shadow và synthetic light DOM, field brand xuất hiện sau chọn Phishing và giữ giá trị người dùng đã sửa. Bộ frontend mới: 178 test; adapter/background trực tiếp 11/11 đạt. Build/typecheck/lint đạt; ZIP 3.1.6 chứa đúng adapter/background nguồn. Lượt sửa này không đổi backend, không restart dịch vụ.
