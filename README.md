# Domain Check Tool

Ứng dụng web phòng thủ nội bộ để kiểm tra domain nghi phishing, chuẩn bị evidence và xử lý báo cáo takedown. Bản được hỗ trợ hiện tại chỉ gồm **backend Python** và **frontend React**. Streamlit, CLI desktop cũ và PyInstaller đã được gỡ.

Domain Worker nhận URL đầy đủ, kể cả đường dẫn phía trình duyệt như
`https://example.com/wap/#/register`. Precheck và browser capture giữ phần `#`
để mở đúng trang; URL chứa thông tin đăng nhập (`user:password@host`) vẫn bị từ chối.

## Cấu trúc

- `backend/src/domaincheck/`: API, application services, worker và DB.
- `backend/src/domaincheck/engine/`: engine kiểm tra, cloaking, Browser Evidence, formatter/parser mail và helper Cloudflare của bản web.
- `backend/tests/`, `backend/migrations/`, `backend/scripts/`: kiểm thử, migration và lệnh vận hành.
- `backend/compose.yaml`: PostgreSQL/Redis local.
- `frontend/src/`, `frontend/tests/`: giao diện và test React.
- `frontend/extensions/cloudflare-profile-worker/`: source Web Form Assistant; build FE tạo ZIP tại `dist/downloads/web-form-assistant.zip`.
- [Hướng dẫn kỹ thuật](docs/technical-guide.md).

Secret và dữ liệu có sẵn như `config.ini`, `data/`, reports/logs, `.runtime/`, build/dist cũ được giữ nguyên tại chỗ. Bản web không dùng chúng làm nguồn cấu hình. Không chuyển chúng vào source hay gói phát hành.

## Cài đặt và chạy trên Windows

Làm theo [hướng dẫn dev đầy đủ](docs/dev-guide.md) cho lần cài đầu: tạo `.env`,
tạo master key cho database mới, cấu hình Telegram, cài Chromium, migration và
cấp admin. Các lệnh bên dưới giả định đã lưu cấu hình hợp lệ. Database cũ phải
giữ nguyên master key. Lệnh dùng sau deploy, xem log/dừng/cập nhật nằm trong
[hướng dẫn production](docs/team-deployment.md#các-lệnh-dùng-sau-deploy-trên-linux).

Cần Python 3.12–3.14, Node/npm tương thích frontend/package.json, PostgreSQL và Redis. Cấu hình theo `backend/.env.example` và `frontend/.env.example`; không dùng config.ini cũ.

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -e "./backend[test]"
docker compose --env-file backend/.env -f backend/compose.yaml up -d --wait
cd backend
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m domaincheck
```

Terminal frontend:

```powershell
cd frontend
npm ci
npm run dev
```

Lệnh chạy BE ở trên mở API, worker chung và Cloudflare runner. Không cần bật worker thêm. Trên Windows có thể bấm `backend/run_backend.bat`. Frontend và PostgreSQL/Redis vẫn chạy riêng.

Các lệnh dưới đây chỉ dùng khi triển khai tách service, với API chạy bằng uvicorn trực tiếp:

```powershell
backend/run_worker.bat --job-type all
backend/scripts/run_cloudflare_worker.bat
```

Cloudflare runner chỉ cần khi dùng Cloudflare Worker. Precheck riêng: `backend/scripts/run_domain_worker_precheck.bat`. Khi bật capture, cài browser bằng `backend/.venv/Scripts/python.exe -m playwright install chromium`. Không bật gửi mail/report chỉ để smoke test.

CLI quản trị còn được hỗ trợ: `python -m domaincheck.cli sync-rbac` và `python -m domaincheck.cli bootstrap-admin --telegram-subject ...`, chạy trong backend venv/CWD backend. Không tự reset quyền role đang sử dụng.

## Chức năng bản web

Dashboard theo quyền; Check Domain; Quick Report; Domain Worker; Cloaking Review; Domain Evidence Review; Cloudflare Worker; Check Link Status; Phản hồi NCC; Cấu hình mail; Audit Log; quản lý người dùng/role. Không khôi phục menu chỉ có ở bản cũ như Lead Triage, thống kê IMAP legacy, brand scan hoặc case-log CSV.

Cloudflare Worker chống trùng URL đã gửi trong ngày theo múi giờ tài khoản. Sang ngày mới, URL được kiểm tra mới. Nút **Xóa cache kiểm tra hôm nay** bắt đầu lại cache chống trùng của ngày hiện tại, cho phép kiểm tra và xác nhận gửi lại; lịch sử report vẫn được giữ nguyên. Các URL đang gửi hoặc chưa rõ kết quả vẫn bị khóa để đối soát. Cloudflare có thể tiếp tục từ chối report trùng theo chính sách riêng của nhà cung cấp.

Quick Report chỉ dùng email WHOIS được ghi rõ là contact abuse của registrar;
các tên `.radio.am`/`.radio.fm` được tra riêng, không dùng contact của domain cha.
Sau khi nạp bản sửa registrar 2026-10-23, nhập lại URL và bấm **Kiểm tra** để lấy
kết quả theo policy mới; lịch sử cũ không tự đổi. Xem [hướng dẫn kỹ thuật](docs/technical-guide.md).

Check Link Status giữ đúng tên đăng ký dưới các đuôi riêng như `.radio.am` và
`.africa.com`. Sau khi nạp bản sửa backend, bấm kiểm tra lại để lấy dữ liệu mới;
trạng thái WHOIS không suy luận từ domain cha khi thiếu bản ghi của tên cần tra.

Preview/draft tiếng Anh; evidence và SMTP ledger giữ scope user/account/ngày. Chỉ thao tác explicit mới gửi/submit hoặc đồng bộ mailbox, không thực hiện khi render/reload.

## Extension Quick Report

Nút **Tải extension** cung cấp ZIP của bản đi kèm web, đổi thành **Tải bản cập nhật** khi phiên bản kết nối thấp hơn. Bản **3.1.0** chỉ dùng task của web ở chế độ fill_only, đã bỏ bridge local và tự submit của desktop cũ. Giải nén vào thư mục cố định, vào chrome://extensions, bật Developer mode rồi **Load unpacked**. Mở Options, cấu hình exact Application origin và đối chiếu ID với VITE_FORM_ASSISTANT_EXTENSION_ID. Khi cập nhật, thay file trong cùng thư mục, Reload extension rồi tải lại web. Tải ZIP không tự cài hay submit.

Chrome profile đang dùng đường dẫn cũ chrome_extension/ có thể tiếp tục nhờ bản sao sinh từ source FE bằng `python backend/scripts/sync_installed_extension.py`. Đây là bản sinh của extension hiện tại, không phải ứng dụng cũ hay source để sửa. Đổi thư mục unpacked có thể đổi ID; không tự sửa profile người dùng.

## Kiểm thử và deploy

```powershell
cd backend
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe -m pip check
cd ../frontend
npm test -- --maxWorkers=2
npm run build
npm run lint
cd ..
python -m compileall -q backend/src backend/tests
```

Test DB/cache cần DOMAINCHECK_TEST_DATABASE_URL và DOMAINCHECK_TEST_REDIS_URL trỏ đến dịch vụ **cô lập**, không dùng production. DOMAINCHECK_TEST_WHEEL_PATH bật kiểm tra wheel mới ngoài repo.

Deploy wheel backend và toàn bộ frontend/dist, gồm favicon/downloads; giữ migration và policy/draft version hiện hữu. Không còn build EXE hoặc entrypoint Python tại root.

[Báo cáo gỡ bản cũ](docs/testing/web-only-cutover-2026-10-18.md).

## Backend entry point

`cd backend` rồi `.venv/Scripts/python.exe -m domaincheck` là lệnh BE tiêu chuẩn. `backend/run_backend.bat` tương đương trên Windows. Log API và các nhóm worker cùng terminal; một service thoát sẽ dừng cả bộ và trả mã lỗi. Ctrl+C gửi tín hiệu dừng và chờ các tiến trình thoát, không ép kill sau timeout. Guard local port 8790 chặn mở trùng launcher. Dừng các worker thủ công cũ khi đã rảnh trước khi dùng launcher mới; guard không quản lý các service đã chạy riêng. `--dry-run` chỉ in lệnh.

`run_all_workers.bat` chỉ là shortcut cho triển khai API/worker riêng; không chạy nó cùng `run_backend.bat`. Uvicorn trực tiếp không tự sinh worker, dành cho triển khai có process manager riêng. Chế độ `--reload` chỉ reload API, không nhân bản worker; sửa code worker cần restart cả BE khi đã rảnh.

## Team 15–30 người

Chạy source vẫn dùng một lệnh BE như trên. Các loại job có worker riêng và số slot giới hạn; mặc định Quick Report/Check Domain/precheck/gửi batch mỗi nhóm 2 slot, evidence độc lập 1 slot khi bật capture. Mỗi lượt xử lý 1 URL rồi xoay vòng giữa người dùng, giữ nguyên batch/checkpoint. Cloudflare runner giới hạn 2 lượt xử lý URL cùng lúc và nhường slot khi batch pause/chờ. Một người chỉ giữ một batch đang chạy trong cùng loại job; các URL còn lại chờ queue.

Deploy dùng `backend/deploy.bat` trên Windows hoặc `python3 backend/scripts/deploy_team.py` trên Linux sau khi cấu hình `.env` theo hướng dẫn. Helper build trước, dừng/drain các service ứng dụng, chạy migration rồi mở lại toàn bộ. Trong production, service lỗi được restart riêng. Web có thông báo khi worker mất heartbeat.

Đề xuất ban đầu cho Linux: Ubuntu 24.04 LTS, 8 vCPU, RAM 32 GB, SSD/NVMe 500 GB và backup ngoài máy chủ. Đây là ước lượng khởi đầu, cần đo workload thật trước khi tăng slot. Xem [hướng dẫn deploy và cập nhật cho team](docs/team-deployment.md), gồm cấu hình slot, chuyển artifact hiện có, ID extension dùng chung, backup và giới hạn của kiểm thử mô phỏng 20/30 người × 50 URL. Không tự deploy hoặc chuyển dữ liệu runtime trong lần sửa source này.

## Domain Worker: kiểm tra lại sau khi bổ sung email

Ở trang Domain Worker, chọn **Xóa cache không có email**, rồi **Precheck danh sách**
để kiểm tra lại domain đã bị bỏ qua vì không có email hôm nay. Nếu không xóa,
kết quả không có email vẫn được dùng lại trong ngày. Nút yêu cầu quyền
`domain_worker.clear_cache` và chỉ dùng khi không còn batch đang xử lý/chờ gửi;
nếu đang chờ gửi, hoàn thành hoặc kết thúc lượt trước. Lịch sử và các khóa gửi
thành công/chưa rõ kết quả được giữ nguyên. Không cần migration cho nút này.
