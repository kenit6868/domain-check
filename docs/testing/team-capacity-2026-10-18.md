# Báo cáo kiểm thử tải team và triển khai — 2026-10-18

## Kết quả

Đã triển khai worker nền có giới hạn đồng thời và chia lượt theo người dùng cho
team 15–20 người, bổ sung kịch bản kiểm thử 30 người, khoảng 50 URL/người.
Chạy source vẫn dùng một lệnh. Production quản lý và restart từng service riêng,
có script một lệnh để build, dừng an toàn, chạy migration và khởi động lại.
Chưa chuyển hệ thống production sang bản mới.

Cấu hình Linux đề xuất để bắt đầu: Ubuntu 24.04 LTS, 8 vCPU, RAM 32 GB,
SSD/NVMe 500 GB và backup ngoài máy chủ. Đây là ước lượng kỹ thuật, chưa được
kiểm chứng bằng tải thật từ nhà cung cấp. Xem [hướng dẫn triển khai](../team-deployment.md)
để cấu hình, tính dung lượng lưu trữ và xác định khi nào cần nâng cấp.

## Các thay đổi đã rà soát

- Dev: `python -m domaincheck` hoặc `backend/run_backend.bat` khởi động API,
  worker theo loại job và Cloudflare runner nội bộ. Reload chỉ áp dụng cho API.
- Slot mặc định: Quick Report 2, Check Domain 2, precheck 2, SMTP batch 2,
  evidence độc lập 1 khi bật capture. `all` có evidence và các vòng nhận việc
  độc lập, thay cho vòng tuần tự ưu tiên cố định trước đây.
- Mỗi lượt một URL, giữ nguyên ID job/item, thứ tự và checkpoint. Worker không
  nhận thêm batch cùng người/cùng loại khi đã có lease đang hoạt động. Lịch sử
  phục vụ của người dùng quyết định lượt nhận tiếp. Khóa giao dịch advisory
  ngắn tuần tự hóa việc nhận job; tác vụ mạng nằm ngoài khóa. Batch mới không
  vượt lượt của người đang chờ. Số lỗi được tính từ toàn bộ item đã lưu.
- Khi dừng an toàn, worker hoàn tất và lưu item hiện tại rồi trả phần chưa làm
  vào chính job đó. SMTP batch giữ cơ chế một domain/lượt, `next_run_at`,
  preview/intent bất biến và bảo vệ trạng thái `sent`/`UNKNOWN`.
- Cloudflare có 2 slot xử lý URL, nhường slot giữa các record và khi pause/chờ.
  Kiểm tra xác nhận, profile và chủ sở hữu vẫn diễn ra trong từng lượt sau khi
  lấy slot, trước khi tạo intent. Cookie chỉ được giữ riêng tư trong RAM.
- Pool DB mặc định 5 kết nối và 2 kết nối dự phòng. Migration 0006 thêm 3 index
  cho queue/lịch sử phục vụ theo chủ sở hữu, không sửa dữ liệu lịch sử.
- Heartbeat Redis mỗi 5 giây, TTL 20 giây, xóa khi thoát bình thường và tự phục
  hồi sau lỗi Redis tạm thời. `health/workers` yêu cầu đăng nhập, chỉ trả nhóm/
  trạng thái/số slot được phép xem, không cache. `ready` kiểm tra DB/cache;
  `live` chỉ xác nhận tiến trình còn chạy.
- FE cảnh báo khi worker chưa sẵn sàng, đang dừng hoặc chưa xác định; kiểm tra
  mỗi 15 giây và gắn cache trạng thái với user/quyền. Không hướng dẫn tạo job trùng.
- Bổ sung Dockerfile Linux, volume artifact riêng tư dùng chung, FE tĩnh/Caddy
  HTTPS, migration trước khởi động và restart từng service độc lập. Đã chuyển
  `httpx` từ dependency chỉ dành cho test sang dependency runtime sau khi image
  sạch phát hiện lỗi import của OIDC. Thư viện hệ thống/trình duyệt được cache
  trong layer riêng; có test kiểm tra phiên bản Playwright nhất quán.

## Kết quả xác minh

| Hạng mục | Kết quả |
| --- | --- |
| Toàn bộ backend, PostgreSQL/Redis sạch và cô lập + wheel mới | **402/402 đạt, không bỏ qua**, 235,866 giây |
| Toàn bộ test FE | **160/160 đạt**, 25 file |
| FE build/typecheck và lint | Đạt |
| Đóng gói wheel mới và smoke test ngoài repository | Đạt trong bộ test đầy đủ |
| Build Docker backend và FE | Đạt với source cuối; chỉ dùng ID extension công khai giả lập |
| Cấu hình Compose, thứ tự deploy và dừng khi lỗi | Đạt |
| compileall, pip check của backend/Python hệ thống, kiểm tra khoảng trắng diff | Đạt |
| Giao diện giả lập 1440px / 390px | Không lỗi trang hoặc tràn ngang; đã xem cảnh báo |

Wheel mới dùng trong lượt test backend cuối:
`domaincheck_backend-0.1.0-py3-none-any.whl`, SHA256:

```text
81aa18150a4982f7eb97d497fc458690bd4df1ae9734296cefb7e9a9effdf6f1
```

### Kịch bản nhiều người gửi việc đồng thời

Dùng queue PostgreSQL thật, `QuickReportService`/`QuickReportWorker` thật, phạm
vi chủ sở hữu và checkpoint từng item. Adapter probe/cache được giả lập, có 2
slot nhận việc. Hai mươi hoặc ba mươi bên gửi tạo công việc đồng thời, mỗi người
có 50 URL kiểm thử khác nhau. Mọi item hoàn tất đúng một lần, không lặp; số probe
giả lập chạy cùng lúc không vượt quá 2. Các kịch bản bật chế độ một URL mỗi lượt.

| Kịch bản | URL hoàn tất | Thời gian queue/probe giả lập |
| --- | ---: | ---: |
| 20 người × 50 | 1.000 | 30,18 giây |
| 30 người × 50 | 1.500 | 47,02 giây |

Thời gian trên bao gồm tạo dữ liệu kiểm thử, thao tác DB và probe giả chỉ mất
vài mili giây; **không phải ước lượng thời gian kiểm tra domain, chụp ảnh, gửi
SMTP hoặc gọi nhà cung cấp thật**. Máy chạy test không phải máy chủ production
đề xuất. Các test khác kiểm tra chia lượt giữa người dùng, batch mới không vượt
lượt, lease còn hạn/hết hạn, dừng và chạy tiếp không xử lý lặp, bộ đếm lỗi qua
nhiều lượt và tiến độ độc lập giữa các loại job.

Hai mươi lượt SMTP giả lập giữ Message-ID riêng: 19 thành công, 1 mất kết nối
sau DATA, giữ `UNKNOWN` và không retry. Các test delivery, retry phần còn thiếu,
Sent-copy, phạm vi dữ liệu và phân quyền đều đạt. Hai mươi tác vụ Cloudflare
giả lập không vượt 2 lượt đồng thời; người đang pause nhường slot.

### Kiểm tra container và cô lập sự cố

Chỉ khởi động project riêng `domaincheck-team-smoke-20261018`, dùng credential
giả, volume riêng mới, tắt gửi ra ngoài, queue rỗng và HTTP chỉ bind loopback
cổng 63880. Không mount `.env`/`config.ini` production hoặc Chrome profile đang
dùng. Backend chạy bằng user không phải root; worker Playwright vượt kiểm tra
khởi động trình duyệt trắng.

Các kết quả quan sát:

- Migration hoàn tất trước API/worker; có đủ 3 index queue.
- Readiness API trả 200 qua Caddy; phục vụ được FE và ZIP extension 3.1.0.
- Heartbeat sẵn sàng: Quick Report 2, Check Domain 2, precheck 2, gửi batch 2,
  evidence 1 và Cloudflare runner 1.
- Dừng/chạy lại Quick Report worker: API vẫn trả 200, heartbeat worker phục hồi.
- Dừng an toàn toàn bộ ứng dụng → chạy lại migration → khởi động thành công.
- Dừng Redis: liveness 200, readiness 503. Chạy lại Redis: readiness 200 và mọi
  heartbeat tự phục hồi, kể cả Cloudflare runner giữ phiên dài hạn.
- Số job trong kiểm tra container luôn bằng 0: không xử lý tác vụ ra ngoài.
- Giao diện desktop/mobile chỉ dùng dữ liệu đăng nhập giả lập và tài nguyên local.

Đã xóa container kiểm thử và các volume riêng sau khi hoàn tất. Các lệnh kiểm
thử không restart hoặc migrate `domaincheck-db-1`, `domaincheck-redis-1`,
`ql_link_live_db`, dữ liệu runtime, secret, browser profile hoặc tiến trình ứng
dụng thật. Image/cache build kiểm thử có thể được giữ để tái sử dụng; không chạy
lệnh Docker prune toàn hệ thống.

## Giới hạn và điều kiện trước khi deploy thật

Đây chưa phải kiểm thử nghiệm thu với 30 người thật sử dụng mọi dịch vụ ngoài.
Gửi SMTP/capture tại review, đồng bộ IMAP, gửi Netcraft và hạn mức nhà cung cấp
giữ luồng hiện tại. Số slot nền không phải giới hạn chung cho mọi kết nối ra
ngoài. Cần đo các luồng này và thời gian chờ thực tế trước khi tăng slot.
Storage và DB trên cùng máy chủ vẫn có thể cùng bị ảnh hưởng khi máy gặp sự cố;
chưa bổ sung object storage, nhiều máy chủ hoặc tự động dọn evidence theo hạn lưu.

Trước production: cấu hình hostname HTTPS/origin, secret và mail allowlist thật;
kiểm tra backup/restore; thống nhất ID extension trên các máy; chủ động chuyển
artifact riêng tư hiện có vào volume mới tại thời điểm an toàn. Lần thay đổi này
không gửi SMTP, truy cập IMAP, submit report, probe URL nghi vấn, cài extension
Chrome, migrate production hoặc deploy hệ thống thật.
