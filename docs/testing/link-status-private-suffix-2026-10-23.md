# Link Status: giữ tên đăng ký trong suffix riêng — 2026-10-23

## Nguyên nhân

`registered_domain()` dùng tldextract offline nhưng không bật PRIVATE section.
Do đó `fixture.radio.am` thành `radio.am`, `fixture.africa.com` thành
`africa.com`, và registration/DNS NS dùng nhầm domain cha. Nhánh WHOIS còn gọi
python-whois với cơ chế tách domain riêng, nên chỉ đổi tên hiển thị không đủ.

## Thay đổi

- Dùng snapshot PSL sẵn trong package, bật `include_psl_private_domains=True`,
  không tải PSL mạng hoặc tạo cache. Giữ đúng label trên private suffix.
- Domain gốc như `radio.am`/`africa.com` vẫn được xem là đăng ký public của chính
  nó; ccTLD nhiều nhãn và IDNA tiếp tục được xử lý đúng.
- Private namespace không dùng RDAP IANA của TLD cha để kết luận registered,
  hold hoặc not-found. Không coi 404 từ `.com` RDAP là bằng chứng cho tên dưới
  `.africa.com`.
- WHOIS child query đúng tên tại route cố định của các registry namespace được
  hỗ trợ, gồm radio.am/radio.fm/africa.com và nhóm CentralNic đã liệt kê trong
  source. Dùng transport WHOIS có deadline/response cap đã thêm ở bản sửa contact.
- Chỉ chấp nhận status khi Domain Name trong bản ghi khớp đúng tên truy vấn.
  Parent/mismatch/ambiguous/empty response bị bỏ; namespace không có route cũng
  không fallback tới domain cha. Không tìm được thì registration UNKNOWN.
- IPC response mang domain để parent kiểm tra identity trước khi dùng status.
  Child chỉ xuất domain/status, không xuất raw WHOIS hoặc thông tin cá nhân.
- DNS A/AAAA/CNAME và HTTP giữ hostname/URL gốc. DNS NS dùng tên đăng ký đã sửa.
  Giữ nguyên egress private/mixed IP, redirect checks, TLS và các deadline.
- Không thay đổi FE, DB, cache hoặc engine version. Link Status vẫn request-local,
  chạy lại sẽ tra mới, không tạo job worker hoặc gửi report/mail.

## Ví dụ đã xác nhận local

| Đầu vào | Trước | Sau |
| --- | --- | --- |
| `https://tubidy.radio.am/` | `radio.am` | `tubidy.radio.am` |
| `https://www.ten.africa.com/path` | `africa.com` | `ten.africa.com` |
| `https://www.fixture.uk.com/` | `uk.com` | `fixture.uk.com` |
| `https://www.example.co.uk/` | `example.co.uk` | `example.co.uk` |
| `https://radio.am/` | `radio.am` | `radio.am` |

Không tra WHOIS/DNS/HTTP thật cho các tên trong bảng. Các case registry status
được dựng bằng fixture và mock, không chứng minh availability hoặc dữ liệu
hiện tại của một domain/provider ngoài mạng.

## Kiểm thử trực tiếp

`test_link_status_web.py`: **24/24 đạt**. Tám test mới bao phủ private/public
suffix và IDNA; WHOIS child giữ nguyên tên; parent/mismatch/ambiguous bị từ chối;
bỏ public RDAP cho private; namespace chưa hỗ trợ; DNS/HTTP không đổi mục tiêu;
NS query và child-process dispatch. Các test egress/redirect/hold/error và stream
request-local/CSRF hiện có vẫn đạt.

Packaging smoke bổ sung kiểm tra radio.am/africa.com ngoài working directory
repository. Compileall, pip check và git diff --check đạt.

Toàn backend: **432/432 đạt, 0 skip**, trong **240.430 giây**, dùng PostgreSQL 16
và Redis 7 cô lập ở loopback 55457/55458, migration chỉ chạy trên DB test.
Packaging chạy với wheel mới, SHA256
`40ea8d72cafbae9a35d6245c4d61f06e6de5211d94e3181dad8e04b648fd9056`.
Log/wheel nằm trong `%TEMP%/domaincheck-link-private-20261023/`. Hai container
test được dọn sau kiểm thử; không tác động DB/cache đang dùng của dự án.
Không đổi FE nên không chạy lại FE test/build/lint trong lượt này.

## Giới hạn tra cứu và vận hành

Lookup đúng tên không bảo đảm registry luôn cung cấp bản ghi: timeout, rate limit
hoặc thay đổi endpoint sẽ trả UNKNOWN, không suy ra hold từ domain cha.
PSL PRIVATE gồm cả hosting namespaces không có WHOIS độc lập; không tự tạo
WHOIS route cho mọi private suffix. Các domain thuộc ccTLD public vẫn theo luồng
RDAP/WHOIS hiện có.

Tham khảo [PSL](https://publicsuffix.org/list/),
[CentralNic WHOIS](https://centralnicregistry.com/policies/whois-guidance/),
[CentralNic private registry policy](https://centralnicregistry.com/policies/dispute/)
và [nguồn client WHOIS có định nghĩa africa.com](https://github.com/weppos/whois/blob/main/CHANGELOG.md).
Trong lượt này, đọc trang giới thiệu `registry.africa.com` bị timeout và Gateway
SRS trả 429; không coi đó là kết quả cho các domain con, không suy đoán status
hoặc thay contact registry dựa trên lỗi này.

Nạp lại API khi triển khai, rồi bấm kiểm tra lại trên Link Status. Không cần
migration hoặc build FE; không khởi chạy/restart dịch vụ thật trong lượt này.
