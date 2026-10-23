# Sửa registrar/contact WHOIS — 2026-10-23

## Vấn đề và bằng chứng

Người dùng báo Quick Report dùng `hostmaster@radio.am` làm email abuse khi nhập
`https://tubidy.radio.am/`. Có hai nguyên nhân trong source cũ:

1. `whois_query_domain` đưa các tên dưới `radio.am` về WHOIS của domain cha.
2. `get_whois_info` và Quick Report dùng bộ email WHOIS không phân vai làm
   recipient registrar; một email admin có thể chặn fallback RDAP.

Tra cứu chỉ đọc tại `whois.centralnic.com:43`, query `tubidy.radio.am` trong lượt
này trả các trường:

```text
Domain Name: TUBIDY.RADIO.AM
Registrar: NICENIC INTERNATIONAL GROUP CO., LIMITED
Registrar Abuse Contact Email: abuse@nicenic.net
```

Đây là quan sát lúc kiểm tra, không phải mapping registrar cố định. Người dùng
làm rõ SAV là tên khác `tnt.radio.am`. Không gán cùng một registrar cho mọi tên
dưới `.radio.am`. Không truy cập website mục tiêu hoặc gửi mail/report.

Nguồn đối chiếu namespace và endpoint:

- [Chính sách dotRadio](https://radio.fm/policy/): đăng ký tên cấp ba trong
  `.radio.am` và `.radio.fm`.
- [dotRadio](https://www.radio.fm/): registry services do CentralNic cung cấp.
- [CentralNic WHOIS guidance](https://centralnicregistry.com/policies/whois-guidance/):
  endpoint `whois.centralnic.com`, TCP 43.

## Thay đổi

- `.radio.am`/`.radio.fm` dùng WHOIS endpoint cố định CentralNic, tra tên cấp ba
  đăng ký. Host sâu hơn như `login.fixture.radio.am` tra `fixture.radio.am`.
- Response phải có Domain Name đúng tên được hỏi; không dùng bản ghi domain cha,
  domain khác hoặc response không đủ dữ liệu. Không theo referral tùy ý.
- WHOIS chỉ đưa email trong trường `Registrar Abuse Contact Email` vào
  `emails`/`abuse_email`; không trả raw text hoặc email admin/registrant/kỹ thuật.
  Helper dùng chung nên các lookup mới của Domain Worker/Check Domain cũng nhận
  tập email WHOIS đã phân vai này. Không viết lại kết quả cũ của các workflow.
- Quick Report chỉ tin WHOIS abuse được phân vai, xác thực định dạng và lọc
  privacy/blocklist. Thiếu contact thì hỏi RDAP trước khi dùng bảng tĩnh.
  Registrar bổ sung từ RDAP được giữ trong result để API tạo đúng form.
- Registrar có form vẫn đi theo bảng form hiện có; không suy luận từ hậu tố.
- Không dùng RDAP public `.am`/`.fm` thay cho namespace radio riêng khi lookup
  private không thành công. Không có contact đáng tin thì để trống.
- WHOIS socket: deadline 8 giây, tối đa 1 MiB và từ chối CR/LF/NUL trong query.
  WHOIS public qua python-whois dùng timeout 10 giây và quiet=True; không shell.
- Quick Report mới dùng `legacy-run-cdn-check-v2-registrar-abuse`, tránh dùng lại
  Redis result của policy cũ. Engine binding, Check, preview và delivery đã lưu
  giữ nguyên. Đây là đổi policy nghiệp vụ, không phải đổi tên do di chuyển code.

## Kiểm thử

- `test_registrar_contacts.py`: **15/15 đạt**. Kiểm tra riêng registrar cho từng
  tên trong cùng suffix, lookup đúng tên, từ chối parent/mismatch, loại email
  không phân vai và email không hợp lệ, RDAP/form/static fallback, namespace
  cache mới, timeout/response cap và query injection. Mọi lookup dùng mock.
- Toàn bộ backend: **424/424 đạt, 0 skip**, trong **217.621 giây**.
  PostgreSQL 16 và Redis 7 test riêng ở loopback 55447/55448; migration chỉ chạy
  trên DB test. Packaging test bật bằng wheel vừa build.
- Wheel SHA256:
  `5260644f7a68947aa9f1666ea81fc2304f02111679a96d7564fd885824021c3a`.
- Compileall, pip check và git diff --check đạt.
- Build wheel đầu tiên với `--no-build-isolation` lỗi do venv thiếu hatchling;
  build lại bằng PEP 517 isolation mặc định đạt, không sửa package venv đang dùng.
- Không đổi FE nên không chạy FE test/build/lint trong bản sửa này.

Log và wheel test nằm trong thư mục tạm
`%TEMP%/domaincheck-registrar-20261023/`. Hai container test được dọn sau kiểm thử;
không tác động PostgreSQL/Redis đang dùng của dự án.

## Triển khai và giới hạn

Không cần migration hoặc build FE. Nạp code BE/worker sau khi job hiện tại đã
hoàn tất; không restart dịch vụ trong lượt này. Nhập lại URL và bấm Kiểm tra để
có result mới; không tự sửa lịch sử, reset batch hoặc gửi lại delivery cũ.
Job tạo trước cập nhật giữ engine binding cũ. Domain Worker vẫn có quy tắc cache
và review riêng, không tự invalidate kết quả đã tạo trước bản sửa.

Các vấn đề độc lập trong review trước (trạng thái UNKNOWN, parser input FE,
đồng bộ daily, fragment Quick Report, domain RDAP public/subdomain, cấu trúc cây
RDAP hoặc bootstrap retry) không được coi là đã sửa bởi thay đổi này.
