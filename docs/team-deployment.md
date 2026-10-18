# Hướng dẫn triển khai cho team 15–30 người

Mức tải khởi đầu được xem xét là tối đa 20 người cùng gửi khoảng 50 URL/người,
tương đương 1.000 URL trong hàng đợi. Một kịch bản kiểm thử khác bao gồm 30 người
và 1.500 URL. Hệ thống xử lý theo lượt, không mở đồng thời 1.000 trình duyệt hoặc
kết nối SMTP. Các bài kiểm thử dùng probe và SMTP giả lập nên không bảo đảm
thông lượng hay thời gian hoàn tất khi làm việc với nhà cung cấp thật.

## Chạy source ở môi trường dev

Xem [hướng dẫn dev từng bước](dev-guide.md): tạo `.env` và master key, migration,
cấp admin lần đầu, lệnh chạy hằng ngày trên Windows/Linux và kiểm tra chức năng.

Trên Windows, mở `backend/run_backend.bat`, hoặc chạy trong thư mục `backend`:

```powershell
.venv/Scripts/python.exe -m domaincheck
```

Trình quản lý tiến trình tự khởi động API, một tiến trình cho mỗi loại job đang
bật và Cloudflare runner riêng. Tham số `--reload` chỉ tải lại API, không tạo
thêm worker trùng lặp. Nếu một tiến trình con thoát, bộ chạy dev dừng và báo lỗi
để tránh âm thầm chạy thiếu thành phần.

Không mở thêm các file BAT worker riêng khi đang dùng lệnh chạy chung.
`--job-type all` vẫn được hỗ trợ: mỗi loại job có vòng nhận việc và số slot
riêng, bao gồm Browser Evidence khi bật capture.

## Giới hạn xử lý ban đầu

Slot là số lượt công việc có thể xử lý đồng thời trong một tiến trình.

| Nhóm worker | Slot/tiến trình | Hậu tố cấu hình sau DOMAINCHECK_ |
| --- | ---: | --- |
| Kiểm tra Quick Report | 2 | WORKER_QUICK_REPORT_SLOTS |
| Check Domain | 2 | WORKER_CHECK_DOMAIN_SLOTS |
| Precheck Domain Worker, gồm bước chụp bằng chứng | 2 | WORKER_DOMAIN_WORKER_PRECHECK_SLOTS |
| Gửi SMTP theo batch của Domain Worker | 2 | WORKER_DOMAIN_WORKER_SEND_SLOTS |
| Chụp Browser Evidence độc lập | 1 | WORKER_BROWSER_EVIDENCE_SLOTS |
| Xử lý URL Cloudflare đồng thời | 2 | CLOUDFLARE_RUNNER_SLOTS |

Đây là giới hạn khởi đầu, chưa phải cam kết về cấu hình CPU/RAM. Mỗi slot xử lý
một URL mỗi lượt (`WORKER_ITEMS_PER_TURN=1`), lưu kết quả rồi trả phần chưa làm
vào hàng đợi của chính job đó. Thứ tự URL trong từng job được giữ nguyên; các
thành viên được chia lượt thay vì phải đợi một batch 50 URL chạy hết.

Mỗi người chỉ có một batch đang được worker nhận xử lý trong cùng loại job.
Job còn lease hợp lệ không bị worker khác nhận lại. Bộ lập lịch ưu tiên người
chưa được phục vụ hoặc đã chờ lâu nhất, rồi mới xét lượt nhận job. Tạo batch mới
không giúp vượt lượt của người khác đang chờ. Khóa giao dịch advisory ngắn của
PostgreSQL bảo đảm giới hạn này khi có nhiều worker; tác vụ mạng chạy ngoài khóa.
Batch SMTP tiếp tục tuân thủ `next_run_at`. Không bổ sung cơ chế tự gửi lại
delivery có trạng thái `sent`, `already_sent` hoặc `UNKNOWN`.

Các slot trên chỉ giới hạn job nền. Gửi SMTP tại trang review, đồng bộ phản hồi
nhà cung cấp, gửi Netcraft và chụp ảnh thủ công vẫn dùng luồng request hiện có.
Đây không phải giới hạn chung cho toàn bộ hoạt động mạng của hệ thống. Trước khi
tăng slot, cần đo tải gần với thực tế của team, bao gồm hạn mức SMTP theo tài
khoản/nhà cung cấp và các lượt capture thủ công chạy cùng lúc.

Theo dõi RAM, thời gian chờ queue, job chờ lâu nhất, thời gian chạy trình duyệt,
lỗi kết nối và số delivery `UNKNOWN`. Chỉ tăng slot từng bước sau khi đo;
thêm tiến trình hoặc bản sao worker cũng làm tăng tài nguyên và kết nối DB.

Pool DB mặc định có 5 kết nối, tối đa thêm 2 kết nối dự phòng và chờ pool tối đa
30 giây. Năm nhóm worker, API và một Cloudflare runner có tổng trần 49 kết nối
trong pool, trước khi thêm bản sao hoặc tác vụ quản trị. Cloudflare giữ một kết
nối trong pool cho khóa singleton. Không chạy Cloudflare runner thứ hai cho
cùng database.

Cloudflare nhường slot giữa các URL và khi batch đang pause, chờ thời gian gửi
tiếp hoặc chờ Cookie mới. Các batch đang chờ vẫn giữ heartbeat và phạm vi phiên;
phiên bị pause không chiếm toàn bộ slot của runner.

## Cấu hình máy chủ Linux đề xuất

Ước lượng khởi đầu cho 15–20 người, có dư địa tăng lên khoảng 30 người:

| Thành phần | Đề xuất ban đầu |
| --- | --- |
| Hệ điều hành | Ubuntu Server 24.04 LTS, x86-64 |
| CPU | 8 vCPU, ưu tiên tài nguyên CPU ổn định hoặc được cấp riêng |
| RAM | 32 GB |
| Lưu trữ | 500 GB SSD/NVMe; backup lưu ngoài máy chủ |
| GPU | Không cần |
| Dịch vụ | Một máy chủ chạy Docker Engine + Compose, tách container |

Đây là ước lượng kỹ thuật để bắt đầu, chưa phải cấu hình đã được đo tải thật và
không phải khuyến nghị mua từ một nhà cung cấp cụ thể. Docker hỗ trợ Ubuntu
24.04. Container ứng dụng dùng image Python nền Debian, Playwright được ghim
phiên bản và cài Chromium cùng các thư viện hệ thống cần thiết.

Ban đầu giữ các slot mặc định ở bảng trên. Sau khi đo tải thật, có thể cân nhắc
tăng Quick Report lên 4 slot và precheck lên 3 slot, giữ evidence độc lập ở 1
slot. Không tăng đồng loạt tất cả nhóm worker hoặc mở một trình duyệt cho mỗi
người dùng.

Dung lượng ổ đĩa phụ thuộc thời gian giữ evidence. Ví dụ để lập kế hoạch:
1.500 domain × 2 ảnh/domain × trung bình 2 MB/ảnh tạo khoảng 6 GB ảnh/ngày,
chưa gồm ảnh quan sát cloaking bổ sung, artifact khác, database, Docker image
và backup. Vì vậy 250 GB có thể thiếu nếu lưu lâu ngày. Cần đo kích thước thực
tế và giữ ít nhất 20% dung lượng trống. Chưa triển khai hoặc cho phép tự động
xóa evidence/lịch sử trong thay đổi này.

Khi CPU/RAM thường xuyên cao và thời gian chờ vượt mục tiêu của team, cần tìm
đúng điểm nghẽn trước khi nâng cấp. Có thể dùng ngưỡng tham khảo CPU trên khoảng
75% hoặc RAM trên khoảng 80% kéo dài, rồi cân nhắc 12–16 vCPU / 64 GB RAM,
hoặc tách worker trình duyệt sang máy thứ hai với quyền truy cập kho artifact
đã được kiểm tra. Đây là gợi ý vận hành, không phải quy tắc tự tăng tài nguyên.
Thay đổi này chưa triển khai object storage hoặc mô hình nhiều máy chủ.
Với cùng số slot, đợt tải 30 người vẫn có thể chờ lâu hơn; thời gian test giả lập
không dự đoán được độ trễ DNS, trình duyệt, SMTP hay nhà cung cấp.

## Chuẩn bị môi trường production

Cần Docker Compose hỗ trợ `env_file` tùy chọn,
`service_completed_successfully` và `--wait` (phiên bản 2.24 trở lên), Python 3
trên máy chủ để chạy script deploy và tên miền DNS đã cấu hình cho HTTPS.

Chỉ với cài đặt mới, sao chép `backend/.env.example` thành `backend/.env`,
sau đó áp dụng các giá trị mẫu trong `backend/.env.production.example`.
Không ghi đè secret của bản đang chạy; không đọc hoặc sao chép `config.ini`.
Cần cấu hình:

- `POSTGRES_PASSWORD` thật và `DOMAINCHECK_CONTAINER_DATABASE_URL` tương ứng,
  dùng hostname `db`; ký tự đặc biệt trong mật khẩu phải được URL-encode.
- Thông tin Telegram client, subject quản trị ban đầu và `SECRET_MASTER_KEY`.
- `DOMAINCHECK_PUBLIC_HOST`, `ALLOWED_ORIGINS` dùng HTTPS và Telegram redirect URI.
- `BROWSER_CAPTURE_ENABLED=true` để dùng service evidence có sẵn. Image backend
  đã cài Chromium và thư viện Linux. Nếu chủ động tắt capture, cần loại service
  evidence bằng cấu hình ghi đè đã được kiểm tra, tránh để service lặp restart.
- Danh sách SMTP/IMAP egress được phép và các cờ gửi report theo chủ đích.
- `VITE_FORM_ASSISTANT_EXTENSION_ID` công khai dùng chung. Đây là tham số build
  FE; thay ID phải build lại FE. Options của extension trên từng máy phải dùng
  đúng origin HTTPS của web. Extension cài unpacked không có manifest key có
  thể nhận ID khác nhau giữa các máy; cần thống nhất ID bằng cách phân phối qua
  cơ chế quản lý hoặc store trước khi dùng cho cả team. Thay đổi này không sửa
  ID, key, phiên bản extension hoặc Chrome profile đang cài.

Trong danh sách trên, các tên cấu hình backend viết ngắn phải có tiền tố
`DOMAINCHECK_`, ví dụ `DOMAINCHECK_BROWSER_CAPTURE_ENABLED` và
`DOMAINCHECK_SECRET_MASTER_KEY`. Tên biến `POSTGRES_PASSWORD` và
`VITE_FORM_ASSISTANT_EXTENSION_ID` giữ nguyên. Đối chiếu tên đầy đủ trong các
file `.env.example` trước khi lưu.

Chỉ Caddy mở cổng 80/443 ra ngoài. API và Cloudflare runner là dịch vụ nội bộ.
PostgreSQL/Redis giữ cấu hình chỉ bind loopback từ Compose gốc. Caddy phục vụ
bản build FE, favicon, ZIP extension và chuyển `/api` đến API. Các service dùng
chung backend image, master key, database và volume artifact riêng tư.
Code ứng dụng chạy bằng user không phải root. Phạm vi build không chứa `.env`,
dữ liệu runtime hoặc browser profile đã cài.

## Deploy và cập nhật bằng một lệnh

### Chuẩn bị lần đầu trên Linux

Tại thư mục gốc source đã đưa lên máy chủ:

```sh
docker compose version
python3 --version
if [ ! -f backend/.env ]; then cp backend/.env.example backend/.env; fi
chmod 600 backend/.env
```

Chỉnh `backend/.env` theo phần production ở trên và
`backend/.env.production.example`; file production example chỉ là các giá trị
ghi đè, không thay thế toàn bộ cấu hình. Đặc biệt điền đầy đủ:
`DOMAINCHECK_ENVIRONMENT=production`, `DOMAINCHECK_PUBLIC_HOST`,
`DOMAINCHECK_ALLOWED_ORIGINS`, các biến Telegram,
`DOMAINCHECK_BOOTSTRAP_TELEGRAM_SUBJECT`, `POSTGRES_PASSWORD`,
`DOMAINCHECK_CONTAINER_DATABASE_URL` và `DOMAINCHECK_BROWSER_CAPTURE_ENABLED=true`.
Public host chỉ là hostname, ví dụ `tool.example.com`; origin phải có `https://`.
DNS phải trỏ về máy chủ và cổng 80/443 phải truy cập được để cấp HTTPS.

**Chỉ với database mới**, tạo master key:

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Tự sao chép kết quả vào `DOMAINCHECK_SECRET_MASTER_KEY`, giữ
`DOMAINCHECK_SECRET_KEY_VERSION=1`. API, worker và runner dùng chung khóa từ
file env này. Sao lưu khóa ngoài Git và tách khỏi backup database.
Nếu chuyển database cũ sang máy mới, dùng lại đúng khóa và phiên bản cũ;
không chạy tạo khóa mới để thay khóa đang sử dụng. Đổi khóa làm credential
SMTP/IMAP/proxy cũ không giải mã được; tăng phiên bản không tự xoay khóa.
Production thiếu khóa sẽ không khởi động hợp lệ.

Mật khẩu trong URL kết nối phải khớp `POSTGRES_PASSWORD` và được URL-encode.
Sửa `POSTGRES_PASSWORD` không tự đổi mật khẩu PostgreSQL đã nằm trong volume.
Lần cài mới chưa có dữ liệu mail, Cookie hoặc domain; cấu hình trong UI sau
khi cấp admin. Giữ các cờ gửi/đồng bộ theo đúng mục đích sử dụng.

Xem trước các bước rồi deploy từ thư mục gốc:

```sh
python3 backend/scripts/deploy_team.py --dry-run
python3 backend/scripts/deploy_team.py
```

Không cần cài venv, Node hoặc Chromium trên host production: các image cài
dependency và build FE. Host cần Python 3 để chạy helper và Docker Compose.

Chạy từ thư mục gốc dự án trên Windows:

```powershell
backend/deploy.bat
```

Trên Linux:

```sh
python3 backend/scripts/deploy_team.py
```

Thêm `--dry-run` để chỉ in lệnh, không gọi Docker hoặc khởi động job.
Script build image trước, bảo đảm DB/cache sẵn sàng, dừng các service ứng dụng
và chờ worker lưu phần đang làm, chạy migration riêng rồi khởi động lại các
service. Nếu build hoặc migration lỗi, quá trình deploy dừng, không mở bộ dịch
vụ khác phiên bản với nhau. Migration gồm `0006_worker_capacity`, chỉ thêm
index cho queue. Tên project Compose `domaincheck` và volume `postgres-data`
hiện có được giữ nguyên.

Trước khi cập nhật, cần có backup database đã kiểm tra và bản sao artifact
riêng tư nhất quán. Thư mục artifact của bản chạy trực tiếp trên máy không tự
được chép vào volume `artifacts-data` mới. Trước lần chuyển đầu tiên, dừng API
và worker cũ khi đã rảnh, chuyển thư mục đó vào volume với quyền truy cập phù
hợp; nếu không, link artifact lịch sử sẽ trỏ tới file chưa có trong volume.
Không chạy đồng thời worker trên host và bộ service production. Thay đổi source
này không tự xóa hoặc sao chép dữ liệu runtime.

Khi nhận `SIGINT`/`SIGTERM`, worker ngừng nhận job mới. Quick Report, Check Domain,
precheck và evidence hoàn tất URL hiện tại, lưu checkpoint rồi trả phần chưa làm
vào chính job đó để tiến trình mới tiếp tục. Worker SMTP batch vốn xử lý một
domain mỗi lượt nhận việc. Compose cho phép chờ dừng tối đa 30 phút. Khi máy bị
tắt cưỡng bức hoặc hết thời gian chờ, hệ thống vẫn dùng cơ chế phục hồi tác vụ/
delivery chưa chắc chắn hiện có, không tự gửi lại khi chưa biết kết quả.
Cookie Cloudflare chỉ nằm trong RAM, nên sau khi restart runner, người dùng có
thể phải nhập Cookie mới và chủ động tiếp tục batch.

Dùng cùng các file Compose và file env với `ps`, `logs -f api precheck send-mail`
hoặc lệnh restart từng service. Chỉ restart service khi đã rảnh hoặc thực hiện
quy trình dừng an toàn nêu trên. Cấu hình này không yêu cầu Kubernetes hoặc nhiều
máy chủ và không cài service tự khởi động trên host; cần cấu hình Docker tự chạy
cùng máy chủ.

## Kiểm tra trạng thái và xác minh

### Các lệnh dùng sau deploy trên Linux

Vào thư mục `backend` từ thư mục gốc. Định nghĩa hàm sau trong terminal hiện tại
để mọi lệnh luôn dùng đúng env và cả hai file Compose:

```sh
cd backend
dc() { docker compose --env-file .env -f compose.yaml -f compose.production.yaml "$@"; }
dc ps -a
```

Mỗi terminal mới cần định nghĩa lại `dc`. Ví dụ `dc ps -a` tương đương:

```sh
docker compose --env-file .env -f compose.yaml -f compose.production.yaml ps -a
```

API, web và các worker cần ở trạng thái chạy; `migrate` kết thúc với mã 0 là
bình thường. Kiểm tra migration và API:

```sh
dc logs --tail=100 migrate
dc exec api python -m alembic current
curl -fsS https://tool.example.com/api/v1/health/live
curl -fsS https://tool.example.com/api/v1/health/ready
```

Thay `tool.example.com` bằng hostname đã cấu hình. Mở web, đăng nhập Telegram
lần đầu rồi cấp admin như mục tiếp theo. Kiểm tra worker sau đăng nhập;
endpoint worker cần session hợp lệ, không phải healthcheck công khai.

Xem log gần nhất hoặc theo dõi liên tục:

```sh
dc logs --tail=100 api quick-report check-domain precheck evidence send-mail cloudflare-runner web
dc logs -f --tail=100 precheck send-mail
```

Ctrl+C khi đang xem `logs -f` chỉ thoát theo dõi log, không dừng service.
Không đưa log có dữ liệu riêng tư hoặc nội dung `.env` lên kênh công khai.

### Dừng, mở lại và restart một worker

Các lệnh sau chạy tại `backend`, với hàm `dc` đã khai báo ở trên:

```sh
# Dừng bộ dịch vụ, giữ nguyên volume dữ liệu
dc stop
# Mở lại cùng phiên bản đã build, khi không có cập nhật source/cấu hình
dc up -d --no-build --wait
# Chỉ restart Quick Report, API vẫn chạy
dc restart quick-report
```

Ưu tiên thực hiện lúc worker rảnh; khi dừng, chờ checkpoint và thời gian drain
tối đa 30 phút. Không dùng lệnh xóa volume để dừng ứng dụng.
Restart `cloudflare-runner` làm mất Cookie đang giữ trong RAM, người dùng cần
nhập lại nếu được yêu cầu. `restart` không nạp `.env` mới hay source mới.

Docker phải được cấu hình tự khởi động cùng máy chủ. Các service ứng dụng dùng
`restart: unless-stopped`; service đã bị dừng chủ động cần được mở lại bằng lệnh
trên. Service `migrate` chỉ chạy rồi kết thúc, không chạy nền liên tục.

### Khi cập nhật source hoặc cấu hình production

1. Chuẩn bị backup database và artifact nhất quán, giữ bản sao master key an toàn.
2. Đưa phiên bản source mới lên máy chủ, giữ `.env` và dữ liệu hiện có.
3. Từ thư mục gốc chạy `python3 backend/scripts/deploy_team.py`.
   Nếu đang ở `backend`, lệnh tương đương là `python3 scripts/deploy_team.py`.
4. Kiểm tra `dc ps -a`, log migration, readiness, đăng nhập và worker như trên.

Helper đã chạy migration và API tự seed; không cần chạy lại migration/seed bằng
tay sau mỗi deploy thành công. Toàn bộ đợt cập nhật có khoảng gián đoạn API để
drain và migration. Restart riêng một worker là thao tác độc lập với API.
Đổi ID extension cần build lại FE; đổi env cần deploy để tạo lại container.
Không tạo lại master key và không reset quyền role trong quy trình cập nhật.

Nếu cần chủ động đồng bộ catalog quyền, tại `backend` dùng:

```sh
dc exec api python -m domaincheck.cli sync-rbac
```

Lệnh này không thay thế migration và không bắt buộc trong vận hành thường ngày.

Trên Windows, dùng cùng lệnh `docker compose --env-file .env -f compose.yaml
-f compose.production.yaml ...` tại `backend` thay cho hàm Bash `dc`;
deploy từ thư mục gốc bằng `backend/deploy.bat`.

### Lần cài mới: migration, seed và quản trị viên đầu tiên

Với database mới hoàn toàn, script deploy thực hiện theo thứ tự:

1. Build image backend và frontend.
2. Mở PostgreSQL/Redis và chờ healthcheck đạt. Bước dừng service ứng dụng cũ
   không có tiến trình để dừng trong lần cài đầu tiên.
3. Service `migrate` chạy `alembic upgrade head`, lần lượt áp dụng migration
   `0001` đến `0006_worker_capacity`. Migration lỗi thì không mở ứng dụng mới.
4. Khởi động API và các nhóm worker sau khi migration thành công. API tự chạy
   seed catalog/quyền trong giao dịch có khóa advisory trước khi nhận request.
   Worker có thể khởi động song song với API; migration là điều kiện chung.
5. Khi API đạt readiness, mở FE/Caddy.

Seed hiện tạo 54 mã quyền và 2 role hệ thống: `admin` có 54 quyền, `user` có 47
quyền. Role `user` mặc định gồm quyền thao tác nghiệp vụ như gửi mail/report;
chỉ loại quyền quản trị user/role và quyền xem Dashboard/Audit Log toàn hệ thống.
Admin có thể chỉnh lại trước khi duyệt thành viên. Cờ `safe_for_default_user`
trong định nghĩa quyền không phải công thức cấp quyền role `user` hiện tại.

Không có tài khoản admin/mật khẩu mẫu được tạo sẵn. Biến
`DOMAINCHECK_BOOTSTRAP_TELEGRAM_SUBJECT` không tự cấp admin khi API khởi động:

1. Đăng nhập Telegram một lần bằng tài khoản quản trị dự kiến. User mới được
   tạo ở trạng thái `pending` và chưa được gán role/quyền sử dụng.
2. Tại terminal, vào thư mục `backend` rồi chạy:

   ```sh
   docker compose --env-file .env -f compose.yaml -f compose.production.yaml exec api python -m domaincheck.cli bootstrap-admin
   ```

   Lệnh dùng subject đã cấu hình trong env, phải trùng subject OIDC của tài
   khoản vừa đăng nhập; đây không phải Telegram username dạng `@ten`.
   Có thể truyền rõ bằng `--telegram-subject SUBJECT_CAN_CAP_QUYEN`.
3. Đăng xuất/đăng nhập lại vì phiên cũ có thể hết hiệu lực khi cấp quyền.
   CLI kích hoạt tài khoản và gán role `admin`; không tạo tài khoản giả nếu
   subject chưa từng đăng nhập.
4. Trong giao diện quản trị, điều chỉnh role, duyệt các user `pending` và gán
   role cần thiết. Khi kích hoạt user, hệ thống bổ sung role `user` mặc định.

Không seed domain, batch, case review, evidence, tài khoản SMTP/IMAP, Cookie
Cloudflare hoặc dữ liệu mẫu nghiệp vụ. Những dữ liệu này được tạo khi người
dùng cấu hình và thực hiện thao tác. Login/CLI quản trị có thể tạo session và
audit log tương ứng.

Seed chạy lại an toàn khi API restart: bổ sung mã quyền mới, giữ lựa chọn quyền,
tên/mô tả của role hiện có và chỉ dọn mã obsolete đã được liệt kê. Quyền mới
không tự cấp cho role cũ. Không dùng `--reset-user-permissions` để deploy thông
thường vì đó là thao tác reset quyền rõ ràng.

- `/api/v1/health/live`: chỉ xác nhận tiến trình API còn hoạt động.
- `/api/v1/health/ready`: kiểm tra DB/cache có giới hạn thời gian, trả 503 khi
  chưa sẵn sàng.
- `/api/v1/health/workers`: dành cho tài khoản đang hoạt động và đã đăng nhập;
  lọc theo quyền xem tính năng. Chỉ trả tên nhóm, trạng thái và số slot sẵn sàng,
  không trả người dùng, URL, email, lịch sử queue hoặc thông tin kết nối.
  Phản hồi dùng `Cache-Control: no-store`.
- Mỗi slot gửi heartbeat vào Redis mỗi 5 giây, TTL 20 giây và xóa dấu hiệu khi
  thoát bình thường. Slot chết hoặc mất heartbeat tự hết hạn. Cloudflare chỉ
  phát một dấu hiệu runner, không phải số tác vụ đang xử lý bên trong.
- Web kiểm tra các nhóm worker được phép xem mỗi 15 giây, hiển thị trạng thái
  offline, đang dừng hoặc chưa xác định; không khuyến khích tạo job trùng.

Chính sách restart phục hồi từng service khi tiến trình thoát. Chỉ bị đánh dấu
unhealthy không làm Docker tự restart một tiến trình còn chạy. Readiness của
API tách khỏi worker để lỗi worker không chặn việc xem và duyệt dữ liệu hiện có.
Production chạy API và worker bằng các entry point riêng; không chạy trình quản
lý dev bên trong các container này.

Trước khi đưa vào sử dụng thật, kiểm tra HTTPS/đăng nhập, profile và quyền tài
khoản, backup/restore volume và kết nối extension trên máy của các thành viên.
Gửi mail/report, đồng bộ mailbox và probe URL nghi vấn vẫn phải do người vận
hành chủ động thực hiện.

Tài liệu tham khảo:

- [Docker Compose trong production](https://docs.docker.com/compose/how-tos/production/).
- [Vòng đời service Compose](https://docs.docker.com/reference/compose-file/services/).
- [Public key giúp giữ ID extension Chrome ổn định](https://developer.chrome.com/docs/extensions/reference/manifest/key).
- [Docker Engine trên Ubuntu](https://docs.docker.com/engine/install/ubuntu/).
- [Yêu cầu container của Playwright](https://playwright.dev/python/docs/docker).
