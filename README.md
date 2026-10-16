# Domain Check Tool — Phát hiện & báo cáo domain phishing

Đặc tả chuyển sang React/Vite/Tailwind và Python API nhiều người dùng nằm trong
[PROMPT_TACH_FE_BE_REACT.md](PROMPT_TACH_FE_BE_REACT.md). Đây là phương án triển khai
đề xuất; đã chốt PostgreSQL + Redis, kèm mô hình lưu URL/check/job/delivery và
24 bảng nền tảng, hợp đồng cách ly dữ liệu theo user và bước lập plan chi tiết
trước implementation. Streamlit vẫn là ứng dụng legacy đang dùng; nền tảng web
mới đã bắt đầu tại `backend/` và `frontend/`; Quick Report check đã mở cho user có
permission, còn mọi workflow gửi/submit vẫn đóng.

## Nền tảng web mới — trạng thái hiện tại

Kế hoạch và bằng chứng tiến độ nằm ở
[`docs/implementation-plan.md`](docs/implementation-plan.md). Backend hiện có
FastAPI health endpoint, schema SQLAlchemy/Alembic 24 bảng, owner constraints,
permission catalog, OIDC/PKCE primitives và mã hóa secret. Telegram login dev đã
được kiểm chứng với user mới ở trạng thái `pending`. Frontend là React 19,
TypeScript strict, Vite 8 và Tailwind 4; source chia theo `app`, `components`,
`features` và `lib`. Mọi HTTP đi qua Axios client cùng origin có cookie/CSRF/error
normalization; TanStack Query keys luôn mang scope feature/user/resource. UI có
login/pending shell, Quick Report, trang quản lý user/role và **Tài khoản gửi mail**
gọi API thật. Secret SMTP/IMAP/proxy được mã hóa theo owner và API chỉ trả cờ đã
có secret. Quick Report chỉ kiểm tra và hiển thị DTO; chưa dùng web mới để gửi hoặc
submit report.

Thiết lập môi trường phát triển (không dùng credential thật):

```powershell
Copy-Item backend/.env.example backend/.env
# sửa placeholder local trong backend/.env
docker compose up -d db redis
python -m pip install -e ".\backend[test]"
python -m alembic -c backend/alembic.ini upgrade head
domaincheck-admin sync-rbac
# Sau khi Telegram identity đã login một lần, bootstrap bằng stable OIDC subject:
domaincheck-admin bootstrap-admin --telegram-subject "<stable-oidc-subject>"
python -m uvicorn domaincheck.main:app --app-dir backend/src --reload

Set-Location frontend
npm install
npm run dev
```

Không đặt Telegram secret, master key, SMTP/IMAP password hoặc Cookie Cloudflare
vào biến `VITE_*`. Migration dữ liệu legacy/runtime thật chưa được chạy.
Lưu cấu hình mail không tự test kết nối hoặc gửi thư. Người có quyền
`mail_accounts.test_own` có thể bấm kiểm tra riêng SMTP hoặc IMAP: SMTP chỉ thực
hiện TLS + đăng nhập, IMAP chỉ TLS + đăng nhập/đăng xuất, không gửi thư hay đọc
mailbox. Bản phát triển chỉ kiểm chứng bằng mock; không dùng endpoint mail thật
trước khi hoàn tất policy egress và được phép vận hành.

Redis của web mới chỉ lưu dữ liệu có thể tái tạo. Key được tách theo user, feature,
ngày nghiệp vụ và version; input nhạy cảm được băm trước khi tạo key. TTL hết đúng
nửa đêm theo timezone user. Redis restart/flush không làm mất check history,
delivery, approval hoặc evidence trong PostgreSQL/private artifact storage.

Artifact ảnh của web mới được lưu ở private filesystem root, không phục vụ qua static
frontend. Tên đường dẫn do server sinh theo owner/evidence/artifact UUID; PNG/JPEG
được kiểm MIME thực, signature, kích thước và SHA-256 cho cả batch trước khi commit.
`GET /api/v1/artifacts/{id}` chỉ trả file thuộc user đăng nhập và khi user có quyền xem
đúng feature; response không lộ local path hoặc tên file nguồn. Upload workflow sẽ
được mở theo từng page ở milestone evidence, không có generic upload công khai.

Boundary dữ liệu web đã kiểm chứng A/B: repository cố định owner lấy từ session, không
nhận `owner_user_id` lại từ payload; quan hệ owned dùng composite FK. Cùng URL hoặc cùng
SMTP identity của hai user vẫn là hai record độc lập. ID resource của user khác trả 404,
kể cả khi PostgreSQL tái sử dụng cùng connection. Worker claim job bằng transaction
ngắn/`SKIP LOCKED`; heartbeat cần đúng lease token và optimistic version.

Primitive job bền vững hiện đã giữ thứ tự item, account scope, stop/resume và retry
đúng failed subset trong PostgreSQL. Item chỉ tăng attempt khi bắt đầu chạy và được
checkpoint riêng. Nếu worker mất lease khi item đang chạy, trạng thái được giữ là
`UNKNOWN` để đối soát, không tự coi là failed rồi gửi/chạy lại. Các endpoint nghiệp vụ
vẫn đóng cho tới khi adapter/parity của từng menu hoàn tất.

Exact preview backend cũng đã sẵn sàng: mỗi lần sửa tạo revision mới và lưu toàn bộ
subject/body/delivery plan trong private immutable artifact, không đưa body vào audit
hoặc job metadata. Approve/send validation sẽ khóa nếu preview hết hạn, có revision mới,
sender/profile/evidence thay đổi hoặc file không còn đúng SHA-256. Hiện đây là primitive
nội bộ; route nghiệp vụ chỉ được mở khi formatter và permission của page tương ứng hoàn tất.

Delivery ledger backend cũng đã hoàn tất ở mức primitive nội bộ. Mỗi delivery lấy
business identity từ adapter workflow phía server (không lấy preview ID/hash để né
dedupe), có Message-ID ổn định và ghi attempt `intent` trong PostgreSQL trước khi gọi
SMTP. Hai request/tab không thể claim cùng delivery; chỉ lỗi xác định `FAILED` mới
được retry. Nếu process hoặc kết nối mất sau intent mà chưa checkpoint, recovery giữ
trạng thái `UNKNOWN` để đối soát thủ công, không tự gửi lại. Chưa có route workflow
gửi thật và không có SMTP thật nào được gọi trong giai đoạn này.

Backend Quick Report hiện có API operation thật: `POST /api/v1/quick-reports/checks`

**Quick Report cần worker riêng**, ngoài API và Vite. Mở terminal thứ ba tại
thư mục gốc dự án và chạy:

```powershell
.\backend\.venv\Scripts\python.exe -m domaincheck.workers.cli
# Hoặc trên Windows:
.\run_quick_report_worker.bat
```

Giữ worker chạy khi sử dụng Quick Report. `--once` chỉ xử lý tối đa một lượt
rồi thoát. Worker kiểm tra dependency trước khi nhận job và gia hạn lease mỗi
10 giây khi URL chạy lâu. Cài lại backend dependency sau khi cập nhật bản này.
Backend package khai báo rõ các module compatibility dùng chung với bản cũ,
nên API/worker đã cài package không phụ thuộc thư mục đang đứng khi chạy. Khi
cập nhật bản này, chạy `python -m pip install -e ./backend` từ root bằng Python
của backend venv, rồi restart API/worker. Wheel không chứa `config.ini`, `.env`
hoặc dữ liệu runtime.
Frontend polling GET mỗi 2 giây chỉ hỏi trạng thái, không thực hiện check mới.
`queued`/`pending` là chưa được worker xử lý, khác `running` đang check URL.

Luồng Quick Report hiện dùng `run_cdn_check`: WHOIS/registrar, CDN, registry tĩnh,
recipient lookup và HTTP cloaking đa profile. Đây không phải pipeline Check
Domain đầy đủ: chưa chạy browser evidence/Playwright verify hay SMTP trong
bước check này. Netcraft và provider form là action riêng sau khi có kết quả.

ghi `targets`, `checks`, job và item theo owner rồi trả `202`; worker riêng gọi đúng
`run_cdn_check()` và checkpoint từng URL. `GET /api/v1/quick-reports/checks/{id}`
khôi phục tiến độ/kết quả sau F5. Full URL/path/query và input order được giữ; Redis
cache kết quả theo owner + ngày + engine version, còn PostgreSQL vẫn lưu mỗi check và
cache provenance. Raw HTML, credential và local evidence path không được ghi vào DTO.
Frontend Quick Report tại `/quick-report` đã có hai bước lọc → check, permission gate,
polling 2 giây khi operation active, result card theo thứ tự input và khôi phục
operation theo user sau F5. Form state chỉ ở trình duyệt; PostgreSQL vẫn là nguồn
trạng thái check. Google Safe Browsing và Microsoft SmartScreen đã dùng capability
task HTTPS một lần qua Web Form Assistant v3; extension chỉ tự điền và FE poll trạng
thái bền vững. Tổng cộng tám adapter đã kết nối theo routing server; Netcraft có
exact preview/approval và cờ bật gửi riêng. Khối export nic.top đã bỏ khỏi UI. Playwright
verify/Browser Evidence thuộc workflow evidence riêng, chưa chạy trong Quick Report.

Quick Report dừng tự làm mới khi batch hoàn tất; mỗi khối domain xử lý nút form trong fragment riêng để giảm render lại danh sách khi thao tác liên tiếp.

Quick Report giữ kết quả và lượt check đang chạy khi F5 hoặc chuyển menu; danh sách đã check được khôi phục vào ô nhập. **Xóa cache** dọn kết quả, cache lọc và ô nhập để bắt đầu lại. Kết quả lưu trong RAM của server, mất khi restart server; bấm **Kiểm tra tất cả** tạo lượt check thay thế.

Quick Report web không yêu cầu backend chạy cùng máy với Chrome. Load extension từ
`chrome_extension/cloudflare-profile-worker/`, mở **Options** và cấu hình exact origin
ứng dụng (HTTPS ở production; HTTP chỉ localhost). Build FE với ID extension công khai
trong `VITE_FORM_ASSISTANT_EXTENSION_ID`. Task Google/Microsoft có TTL ngắn, dùng một
lần, chỉ nằm trong session của tab và luôn `fill_only`; nếu extension chưa kết nối,
UI chỉ mở link thủ công và không báo đã tự điền.

Chẩn đoán kết nối web: ô trạng thái hiển thị ID frontend và Application origin
để đối chiếu với extension đã cài. Thiếu ID, ID sai định dạng, thiếu browser
messaging API, origin bị từ chối và timeout là các lỗi riêng. Nút Kiểm tra lại
có loading và toast; không cần mở form hoặc gửi report để kiểm tra kết nối.
Nếu ID đúng nhưng trang chưa có API extension: kiểm tra đã nạp/bật bản v3 trong
đúng Chrome profile, Reload extension rồi reload web. Nếu đã chuyển extension
sang folder khác, phải cập nhật mã trong chính folder đang được Chrome nạp.
Nếu báo “Extension chưa cho phép trang”, mở Options của đúng extension có ID
đang hiển thị và lưu Application origin trùng địa chỉ web. Nạp extension từ
folder khác có thể tạo ID mới với cấu hình riêng; origin của bản cũ không được
tự động chuyển sang. FE nhận cả mã lỗi legacy có tiền tố `Error:`.

### Telegram login trên web mới

Backend chỉ đọc `backend/.env`. Tạo file này từ `backend/.env.example`, điền
`DOMAINCHECK_TELEGRAM_CLIENT_ID` và `DOMAINCHECK_TELEGRAM_CLIENT_SECRET` do BotFather
cấp, rồi đăng ký chính xác callback dev:

```text
http://127.0.0.1:5173/api/v1/auth/telegram/callback
```

Mở frontend bằng đúng `http://127.0.0.1:5173`; không đổi qua `localhost` giữa flow.
Giao diện React hỗ trợ **Sáng / Tối**, chuyển bằng nút trên thanh đầu trang và
trang đăng nhập. Lần đầu chọn theo hệ điều hành; lựa chọn được lưu trên trình
duyệt và giữ qua tải lại. Form, bảng, cảnh báo và dialog dùng cùng bảng màu;
nút và dialog dùng Radix Themes. Dialog hỗ trợ Tab/Esc và trả focus về nút mở.
Thiết kế dùng gradient indigo–violet cho nút chính và mục điều hướng đang chọn,
header chuyển sắc nhẹ, card có đổ bóng và form chia nhóm; hai mode dùng chung bố
cục. Hover/active/focus/disabled có trạng thái riêng, tôn trọng reduced motion.
Trang login hiển thị readiness và khóa nút nếu backend chưa có cấu hình.
Token exchange dùng Authorization Basic theo Telegram OIDC; login đầu tiên tạo user
`pending`, chưa tự cấp admin hoặc quyền nghiệp vụ.

RBAC không tự cấp quyền nghiệp vụ theo tên role. `admin` là system role toàn quyền
trong permission catalog nhưng vẫn chịu owner isolation. Khi admin kích hoạt một
tài khoản thường, hệ thống gán role `user`; seed mới cấp toàn bộ quyền nghiệp vụ,
trừ `users.*` và `roles.*` (hiện 51/56 quyền). Admin có thể điều chỉnh tại
**Role & quyền**. Bộ mặc định bao gồm gửi mail, đọc IMAP và chạy worker; các bước
preview/approve và owner isolation vẫn được thực thi.

`sync-rbac` chạy lại vẫn giữ permission admin đã chọn cho role `user`. Sau khi
đổi quyền role, các phiên của thành viên bị thu hồi; người dùng đăng nhập lại.
Để áp dụng lại bộ mặc định trên DB đã có role `user`, chạy
`domaincheck-admin sync-rbac --reset-user-permissions`; lệnh thay toàn bộ quyền
của role này và thu hồi phiên thành viên.

Bộ công cụ hỗ trợ 2 người rà soát, xác minh và báo cáo (takedown) các domain
giả mạo thương hiệu công ty để lừa đảo.

Draft Xử lý đầu mối giữ narrative và evidence của Domain Worker, rồi nhóm nội
dung thành các mục giả mạo thương hiệu, chuỗi chuyển hướng, backend liên quan
và yêu cầu xử lý. Registrar được đưa vào report khi ghi chú có `reg=`. Mọi
backend đã nhập đều được nêu cùng DNS A/RDAP và nhãn quan hệ chưa xác minh;
không khẳng định lấy cắp thông tin, tiền hoặc OTP nếu evidence không chứng minh.

Draft NCC loại các dòng nhắc AU88/AU888 khỏi nội dung cũ và mô tả bổ sung;
URL chứng cứ vẫn được giữ nguyên. Bấm **Rerun** để cập nhật preview đang mở.
Trong **Xử lý đầu mối**, thư gửi liệt kê các backend được cung cấp cùng DNS A
hiện tại và RDAP nếu có. Mỗi đầu mối ghi rõ có hay chưa thấy giao điểm DNS/URL;
NCC được yêu cầu xác minh mối liên hệ trước khi gắn vào vụ việc. Sau khi tạo lại
draft, duyệt nội dung trước khi bấm gửi; thư đã gửi trước đó không tự thay đổi.
Khi có ảnh DOM nguồn–đích, thư giữ bước tái hiện và redirect của Domain Worker
nhưng bỏ thời điểm/DNS không liên quan và lời yêu cầu điều tra lặp. Bản tiếng
Việt được rút thành tóm tắt đường dẫn và điểm cần xác minh.
Trong bảng nhiều domain, nút Check theo dòng chỉ đưa kết quả của domain đó vào
vùng precheck/report bên dưới; cache các domain khác vẫn được giữ. Check nhiều
domain hiển thị đúng nhóm vừa chọn.

Menu **Xử lý đầu mối** nhận nguyên ghi chú hạ tầng (dòng domain, `A:` và IP).
Sau khi dán hoặc sửa ghi chú, bấm **Phân tích ghi chú** để hiện danh sách; bước
này chỉ xử lý văn bản tại chỗ, chưa truy vấn mạng. Danh sách và kết quả cũ được
ẩn khi nội dung ô nhập thay đổi cho đến lần phân tích tiếp theo.
Các domain trước `=== backend ===` là đối tượng báo cáo, được chọn sẵn;
những domain trong mục backend chỉ là thông tin hạ tầng bổ sung, không có nút
gửi report riêng. Nút **Check** ở từng dòng kiểm tra đúng URL/domain đó; nút **Check đầu
mối đã chọn** kiểm tra song song tối đa sáu mục, giữ thứ tự kết quả theo ghi chú.
Cột **Check** và **Domain/URL** được ghim bên trái bảng. Backend DNS/RDAP được
tái dùng trong phiên tối đa 5 phút khi ghi chú không đổi; target vẫn check mới.
Bước check so A record hiện tại với IP được cung cấp, tra contact RDAP cho các IP
trong ghi chú, kiểm tra HTTP và chạy cùng precheck recipient/cloaking của Domain
Worker cho domain cần báo cáo. Các đầu mối backend chỉ được tra DNS A/RDAP một
lần để bổ sung ngữ cảnh cho draft của từng domain phía trên; thư phân biệt IP
trong ghi chú với DNS mới và đề nghị NCC xác minh mối liên hệ. Bấm **Tạo nội dung report Domain Worker** tại mục cần duyệt mới chạy
pipeline `run_check()` để tạo draft. Kể cả khi DNS timeout, page vẫn
giữ IP/contact trong ghi chú và gắn nhãn chưa xác minh. Bên dưới có draft tiếng Anh
theo từng domain để duyệt, ô mô tả bổ sung đã xác minh, email nhận có thể chỉnh
và nút tải `.txt`; ghi chú AU888/backend giữ trong phần precheck nội bộ.
Ghi chú, kết quả precheck, draft và đường dẫn ảnh DOM được lưu cục bộ trong
`data/lead_triage_cache.json`; mở lại menu hoặc F5 sẽ khôi phục để duyệt tiếp,
không tự chạy Check hay gửi mail. Bấm **Xóa cache** để bỏ bản nháp hiện tại và
nhập thông tin khác; lịch sử gửi và file evidence đã tạo vẫn được giữ để đối
chiếu. Menu không tạo worker job. Draft hiển thị
hai khung có viền đặt cạnh nhau: tiếng Anh (bản gửi) và tiếng Việt (bản đối chiếu).
Bản xem trước đặt logo ngay trước chữ ký như email HTML. Thông tin tổ chức lấy
từ cấu hình chung; chọn tài khoản gửi để xem đúng người ký. Phần văn bản gốc
trong mỗi khung có thể mở để sao chép hoặc đối chiếu nội dung.
Nếu tài khoản SMTP thiếu logo hoặc thông tin tổ chức, bản xem trước và email
gửi cùng lấy giá trị chung trong cấu hình.
Bản đối chiếu dịch các đoạn mở đầu, mô tả, yêu cầu xử lý và câu kết của mẫu
registry/registrar Domain Worker, cùng nhãn chứng cứ và câu VirusTotal; tên tổ
chức, URL, ngày và số liệu chứng cứ giữ nguyên.
Nút gửi nằm ngay dưới draft của từng domain. Chọn tài khoản SMTP,
xác minh email nhận và tích xác nhận riêng cho từng draft để bật nút **Gửi report**.
Email mặc định được lấy từ recipient precheck của Domain Worker theo đúng kênh
draft (registrar/registry/hosting); nếu kênh đó không có, dùng email hợp lệ trong
draft Worker. Email IP/backend trong ghi chú không được tự gán làm người nhận.
Địa chỉ bị chặn hoặc sai định dạng bị loại; ô email vẫn cho chỉnh sau khi xác minh.
Nút **Kiểm tra lại email nhận (Domain Worker)** tại từng draft chạy lại lookup
recipient cho đúng domain, hiển thị kênh/địa chỉ và cập nhật ô người nhận ngay.
Nếu lookup lỗi, kết quả cũ bị loại và nút gửi khóa cho đến khi kiểm tra lại đạt.
Đầu bảng domain có cột **Trạng thái gửi**: chỉ hiện **Đã gửi** sau SMTP thành công
hoặc lượt gửi trước đã xác nhận, còn gửi lỗi vẫn là **Chưa gửi**. Trạng thái giữ
theo đúng URL/domain trong phiên Streamlit khi thay đổi nội dung ghi chú. Khi mở
lại trang, các lượt Lead Triage đã gửi thành công được khôi phục từ `sent_log.csv`.
Domain **Đã gửi** không còn nút Check trong bảng và bị loại khỏi lựa chọn Check
hàng loạt, kể cả sau khi mở lại trang; muốn xử lý thêm một URL khác, nhập đúng
URL đó thành dòng riêng.
Thư gửi chỉ chứa bản tiếng Anh đã preview; delivery thành công được ghi metadata
vào `sent_log.csv` và cùng nội dung/tài khoản/người nhận sẽ không gửi lại.
Subject/body dùng report Domain Worker; bỏ câu provenance subdomain khỏi thư NCC
và hiển thị IP hosting. Khi đầu mối có IP mà pipeline chưa
tạo report hosting, menu gọi cùng formatter `generate_hosting_draft()`. Nội dung
tố phishing của Worker được giữ khi thay tên thương hiệu bị loại bằng “our brand”;
khối backend được chèn trước chữ ký và phân biệt IP được cung cấp với DNS/RDAP mới.
Phần bổ sung ghi thời điểm check UTC, tách IP đầu mối do người vận hành cung cấp
khỏi DNS A quan sát được, và yêu cầu nhà cung cấp xác minh quan hệ với URL báo
cáo trước khi gắn hạ tầng đó vào vụ việc. Yêu cầu xử lý nêu rõ điều kiện sau
khi xác nhận vi phạm; phần report gốc của Domain Worker vẫn được giữ.
Thư dùng nhãn “IPs supplied for investigation”/“IP được cung cấp để điều tra”, không đưa cụm
“trong ghi chú” vào nội dung gửi.
Trước khi gửi, bấm **Chụp URL nguồn + URL đích từ DOM** cho từng URL. Nút gửi
chỉ mở khi helper Phản hồi NCC chụp thành công đủ hai ảnh nguồn–đích từ DOM,
manifest/hash hợp lệ và đúng URL. Nếu chỉ có ảnh nguồn thụ động hoặc capture
thất bại, cần chụp lại; draft vẫn có thể tải để duyệt.
Thư đính kèm ảnh/manifest, chữ ký tổ chức và logo theo cùng helper Domain Worker.
Khi DOM capture thành công, draft dùng nguyên block Browser Evidence của Domain
Worker với các bước tái hiện URL nguồn, nút/link và URL đích; mục **Provider review
focus** hướng NCC đối chiếu thương hiệu/luồng đăng nhập ở trang đích và kiểm tra
hạ tầng backend có phục vụ URL nguồn hoặc đích hay không. Bản tiếng Việt có cùng
các bước để đối chiếu, không khẳng định vi phạm chỉ dựa trên liên kết.
Phần backend ghi rõ IP từ ghi chú và kết quả DNS/IP/RDAP đã kiểm tra; không đưa ghi chú nội bộ hoặc
mối liên hệ AU888 chưa xác minh vào email. Case cloaking cần duyệt riêng không
gửi từ menu này.
Domain trần được kiểm tra tại trang chủ HTTPS; để kết luận và gửi takedown cần
full URL cùng chứng cứ hành vi riêng cho từng URL.
Nếu ứng dụng đang mở khi cập nhật menu, tải lại trang để nhận logic draft mới;
page tự nạp lại helper cũ còn trong bộ nhớ Streamlit.

## Cài đặt

```bash
pip install -r requirements.txt
python -m playwright install chromium
cp config.example.ini config.ini
```

Mở `config.ini`, điền:
- `vt_api_key` — API key VirusTotal (miễn phí tại virustotal.com/gui/my-apikey). Để trống nếu chưa có, tool vẫn chạy được.
- `gsb_api_key` — API key Google Safe Browsing (tùy chọn).
- `brand_name`, `contact_name`, `contact_email` — dùng để điền sẵn vào email báo cáo.

### Cấu hình SMTP theo port

Mỗi phần tử trong `smtp.accounts` có host, port, username và password riêng.
Tool tự chọn đúng transport cho từng tài khoản:

- Port `465` hoặc `"ssl": true`: TLS ngay khi kết nối (`SMTP_SSL`).
- Port `587` và các port khác: dùng `STARTTLS` theo mặc định.
- Chỉ với SMTP nội bộ không hỗ trợ TLS, đặt `"starttls": false` trên đúng
  account đó; không tắt TLS cho Gmail hoặc dịch vụ công cộng.

Email thường có timeout 30 giây. Email kèm manifest/ảnh evidence có timeout 60
giây và dùng `send_message()` để giữ đúng MIME. Nếu kết nối bị timeout hoặc ngắt
tạm thời, tool tạo kết nối mới và thử lại tối đa một lần với cùng Message-ID;
lỗi xác thực, sender hoặc recipient không được retry. Kết quả lỗi ghi rõ bước
`connect`, `starttls`, `authenticate` hoặc `send` để dễ chẩn đoán.

### Build và chia sẻ bản Windows

Chạy `build_app.bat` trên máy build. Script tự tải Chromium đúng phiên bản của
Playwright, rồi spec copy browser vào đúng đường dẫn mà bản `.exe` sử dụng trước
khi chạy PyInstaller. Spec loại thư mục browser nguồn khỏi data collector để
Chromium không bị đóng gói hai lần; spec cũng lọc lại Analysis datas/binaries vì hook
PyInstaller có thể chèn lại cây browser sau bước collect ban đầu. Sau khi build, chia sẻ
**toàn bộ** thư mục `dist/PhishingTool/`; người nhận chỉ cần cấu hình `config.ini`
và chạy `PhishingTool.exe`, không cần cài Python hoặc chạy `playwright install`.
Trong bản frozen, ứng dụng trỏ `PLAYWRIGHT_BROWSERS_PATH` tới đường dẫn tuyệt
đối dưới `_internal/playwright/driver/package.local-browsers`; không phụ thuộc
thư mục làm việc hay biến môi trường trên máy nhận. `build_app.bat` kiểm tra cả
`chrome.exe` và `chrome-headless-shell.exe` sau PyInstaller và dừng build nếu
thiếu. Nếu file có trên máy build nhưng mất sau khi giải nén, kiểm tra lịch sử
cách ly của phần mềm bảo mật trên máy nhận.
Nếu build bằng lệnh PyInstaller trực tiếp, chạy trước:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "0"
python -m playwright install chromium chromium-headless-shell
python -m PyInstaller PhishingTool.spec -y --clean
```

Chrome hiển thị icon lá chắn của Web Form Assistant sau khi người dùng chọn
**Load unpacked** với thư mục extension. Nếu extension đã được nạp từ trước,
vào trang quản lý extension của Chrome và bấm **Reload** để nhận icon mới.

### Cloudflare Worker theo phiên Dashboard

`api_token` không được Page **Cloudflare Worker** sử dụng. `account_id` là tùy
chọn: nếu để trống, worker lấy Account ID từ trường `curr-account` trong Cookie
Dashboard đã nhập; nếu cấu hình thì giá trị cấu hình được ưu tiên.

```ini
[cloudflare]
account_id = YOUR_CLOUDFLARE_ACCOUNT_ID
```

Trên menu **Cloudflare
Worker**, người vận hành dán danh sách URL, kiểm tra Cloudflare rồi nhập header
`Cookie` lấy từ phiên Dashboard để bấm **Bắt đầu gửi**. Ô nhập và ledger được
lưu theo ngày; page tự phục hồi danh sách/job khi quay lại. **Danh sách thực hiện**
được tự mở khóa ngay khi job hoàn tất, không cần bấm **Dừng** để nhập URL mới.
Ô nhập cũng nhận dòng có ghi chú hoặc chuỗi chuyển hướng như
`URL (top2)` / `URL -> URL`: worker chỉ tách các URL thật, bỏ nhãn và mũi tên.
chỉ chứa URL Cloudflare chưa gửi/cần retry; **Bảng loại** chứa URL đã gửi, bị
Cloudflare dedupe, không dùng Cloudflare hoặc chưa rõ kết quả. **Tiến trình gửi**
chỉ xuất hiện khi job submit đang chạy và lấy đúng `record_ids` của job, không
suy từ toàn bộ ledger. Các bảng chỉ xét URL trong ô nhập hiện tại; ledger cũ vẫn
được giữ để chống gửi trùng. Bảng loại được chốt sau precheck; một URL đã vào
job sẽ ở nguyên trong Tiến trình gửi kể cả sau khi thành công hoặc job hoàn tất.
Job cũ thiếu `record_ids` được phục hồi một lần từ mốc bắt đầu và tổng số item,
sau đó từng record được gắn `job_id` để reload không đẩy nhầm URL sang Bảng loại.
Sau precheck và trong lúc gửi chỉ dùng một bảng **Kết quả URL**; trạng thái job
được cập nhật ngay trên chính các dòng đó. Bảng loại mặc định ẩn và chỉ render
khi người vận hành bật toggle **Hiện URL bị loại**.
Trang mở theo trạng thái: trước precheck chỉ hiện ô URL và nút kiểm tra; sau khi
toàn bộ URL hiện tại có kết quả mới hiện **Cấu hình gửi**, rồi **Kết quả URL** ở
bên dưới. Job terminal hiển thị thông báo hoàn thành và số URL thành công/lỗi.
Cloudflare Worker dùng layout rộng toàn vùng nội dung giống Domain Worker để ô
nhập, cấu hình và bảng URL tận dụng màn hình desktop.
Thứ tự dòng của **Kết quả URL** được cố định theo input sau precheck; worker chỉ
đổi trạng thái tại chỗ và thông báo URL đang xử lý, không đưa dòng đó lên đầu.
Trên Windows, checkpoint JSON tự retry ngắn khi `os.replace` gặp sharing
violation tạm thời; lỗi ghi kéo dài được hiển thị trong page thay vì làm văng UI.
URL đang xử lý được đưa lên đầu với `▶ Đang gửi`.
Cookie chỉ nằm trong RAM của batch, không ghi ledger/log/UI kết quả; ứng dụng
không có phép kiểm tra phiên chỉ-đọc nên chỉ xác minh khi gửi item đầu tiên.
Authentication error dừng ở trạng thái chờ phiên mới; mất response chuyển
`unknown` và không tự retry; rate limit/challenge tạm dừng batch. Response
`error_code=dedupe` được hiển thị bằng thông báo `msg`, đánh dấu **Đã gửi gần
đây** và không tự gửi lại URL đó. Mỗi lần submit chạy tuần tự và chỉ chờ giãn
cách sau khi thao tác trước kết thúc. Lỗi xác định của một URL được checkpoint
rồi worker tiếp tục; sau khi hoàn tất có nút **Thử lại URL lỗi**. Nút xóa cache
chỉ xóa ô nhập/kết quả chưa gửi, không xóa lịch sử đã gửi hoặc chưa rõ kết quả.
Page này không còn kênh dự phòng API/extension; extension vẫn được Quick Report
và các adapter webform khác sử dụng độc lập.

### Tự điền form phishing GoDaddy

Khi Quick Report xác định registrar là GoDaddy, nút **Mở & tự điền form
GoDaddy** mở `legalportal.godaddy.com/abuse/phishing` trên Chrome profile hiện
tại. Extension điền email, brand, full URL/path và draft registrar. Người
vận hành phải tự kiểm tra, tích cam kết good-faith và bấm **Send
Report**; extension không tự cam kết, giải CAPTCHA hoặc submit.

## Sử dụng

```bash
# Kiểm tra đầy đủ 1 domain nghi ngờ
python3 phishing_toolkit.py check <domain>

# Tìm domain "anh em" cùng chiến dịch phishing (qua Certificate Transparency)
python3 phishing_toolkit.py related "<tên thương hiệu>"

# Chủ động dò các domain giả mạo domain thật của công ty
python3 phishing_toolkit.py brandscan <domain-that-cua-cong-ty>
```

Lệnh `check` sẽ tự động:
- Lấy SSL issuer + serial number
- Tra WHOIS (registrar, abuse email, nameservers)
- Phát hiện có đứng sau Cloudflare không
- Kiểm tra VirusTotal + Google Safe Browsing (nếu có API key)
- Ghi log vào `case_log.csv`
- Sinh sẵn email báo cáo trong thư mục `reports/`
- So sánh sáu HTTP profile: desktop/Android/iPhone, trực tiếp/Google referrer và
  Googlebot Smartphone; `/vi-vn/` chỉ là probe khám phá đường dẫn, không cộng điểm
  cloaking khi trang gốc và trang 404 khác nhau

## Chạy giao diện web

Thay vì gõ lệnh CLI, có thể dùng giao diện web local bằng Streamlit — thuận tiện hơn
cho 2 người trong team, không cần nhớ cú pháp lệnh:

```bash
streamlit run streamlit_app.py
```

Trình duyệt sẽ tự mở `http://localhost:8501`. Nếu không tự mở, vào link đó thủ công.

Các trang (xem sidebar bên trái):
- **Trang chủ** — form check nhanh 1 domain + bảng 10 case gần nhất
- **Check Domain** — kiểm tra đầy đủ 1 domain (SSL/WHOIS/Cloudflare/VirusTotal/Safe Browsing), ghi log, sinh email báo cáo
- **Related Domains** — tìm domain "anh em" qua crt.sh
- **Brand Scan** — quét biến thể gõ nhầm domain thật (dnstwist, có thể mất tới 10 phút)
- **Case Log** — xem/lọc/sửa `case_log.csv`
- **Report Drafts** — xem, copy, tải các email báo cáo đã sinh sẵn
- **Quick Report** — kiểm tra nhanh nhiều URL, hiển thị cloaking và cho phép xác
  minh thụ động bằng Playwright khi HTTP chưa đủ kết luận. Mỗi kết quả hiển thị
  một dòng nhỏ phía trên cloaking cho biết email
  tố cáo Registrar/Registry tìm được từ WHOIS, RDAP và bảng registry tĩnh. Đây là
  lookup nhẹ, không chạy precheck Domain Worker, không mở website hay chụp bằng
  chứng để quyết định có hiển thị email. Trong **Browser
  Blocking**, Google Safe Browsing và Microsoft SmartScreen mở form chính thức
  trên Chrome profile hiện tại qua Web Form Assistant. Google được điền URL,
  nội dung và taxonomy đã chọn; Microsoft được điền URL. Cả hai chỉ `fill_only`,
  người vận hành vẫn kiểm tra form và tự xác nhận gửi; Playwright vẫn tạm ẩn.
  Riêng Cloudflare dùng một nút **Mở & tự điền Cloudflare Abuse**: mở form bằng
  Chrome profile có extension và tự điền URL/draft/contact/company. Quick Report
  chỉ điền, không submit; nút mở form Cloudflare thủ công cũ đã được thay thế.
- **Cloudflare Form Worker** — dùng cùng phép kiểm tra nameserver Cloudflare của
  Quick Report, chỉ đưa URL được người vận hành nhập vào hàng chờ trong ngày,
  hiển thị full URL và trạng thái trong một bảng duy nhất. Luồng nhận
  Cookie thủ công và gửi tuần tự qua endpoint Dashboard sau xác nhận; có nút
  tiếp tục bằng phiên mới, dừng và retry riêng URL lỗi. Ledger schema v4 checkpoint theo
  URL + fingerprint nội dung nhưng không lưu phiên. Kết quả mất response là
  `unknown`, phải đối chiếu trước khi thử lại. API/extension không xuất hiện trên
  page này; extension của Quick Report vẫn mở Chrome để điền form trên đúng
  Chrome profile cá nhân đang mở (không dùng profile Playwright). Ledger checkpoint
  theo URL để retry/resume và không sao chép/lưu cookie, CAPTCHA hay HTML.
  Extension điền `Confirm email address`, lấy `Company name` từ `brand_name` và
  đưa draft vào đúng `Logs or other evidence of abuse`, không dùng `Comments`.
  Từ extension v1.0.2, mọi request localhost đi qua MV3 service worker thay vì
  content script để không bị CSP/CORS của form Cloudflare chặn.
  Worker xử lý tuần tự từng tab; extension v1.0.3 hiển thị badge chẩn đoán ngay
  trên form và chỉ mở URL tiếp theo sau khi URL hiện tại đã callback hoặc timeout.
  Extension v1.0.4 nhận diện field theo vùng DOM React gần nhất và loại textarea
  Comments, đồng thời badge hiển thị số input/textarea trong lúc chờ form.
  Extension v1.0.5 điền cả hai input email theo thứ tự hiển thị và kiểm tra lại
  giá trị sau React rerender. Turnstile chỉ hỗ trợ chờ người vận hành xác minh;
  tool không tự giải hoặc vượt CAPTCHA.
  Extension v1.0.6 ánh xạ Confirm email từ caption DOM và fallback input ngay sau
  email chính; badge `email fields=2/2` xác nhận cả hai giá trị đã bám vào form.
  Extension v2.0.0 tách Cloudflare thành adapter chuẩn (`matches`, `waitUntilReady`,
  `fill`, `validate`, `captchaPending`, `submit`, `detectSuccess`); coordinator
  chỉ nhận task và điều phối, không chứa selector riêng của provider.
  Extension v2.1.0 thêm adapter Google Safe Browsing và Microsoft SmartScreen;
  quyền host chỉ giới hạn ở ba form chính thức và localhost. Hai adapter mới
  chỉ tự điền từ Quick Report, không tự xử lý CAPTCHA hoặc submit.
  Extension v2.7.1 cập nhật adapter Chống Lừa Đảo theo form SolidJS mới: scope
  control trong form báo cáo, nhận URL `input[type=text][required]`, textarea
  bằng chứng và chọn loại bằng value `2:PHISHING`; vẫn không submit/CAPTCHA.
  Extension v2.8.0 bổ sung adapter XYZ.COM LLC cho ticket Anti-Abuse tại
  `gen.xyz/account/submitticket.php?step=2&deptid=6`: tự điền người báo cáo,
  loại Phishing, domain và nội dung; attachment, CAPTCHA và submit vẫn thủ công.
  Extension v2.6.5 chỉ điền URL trên Microsoft SmartScreen; trường ngôn ngữ được
  giữ nguyên mặc định của website, không mở dropdown hoặc chọn thay người dùng.
  Extension v2.6.0 có popup mini khi bấm icon: hiển thị phiên bản extension,
  hostname, adapter/version, trạng thái và thông báo gần nhất của tab hiện tại.
  Popup chỉ nhận metadata chẩn đoán đã rút gọn, không hiển thị token, email,
  draft, cookie hoặc giá trị CAPTCHA. Checklist cho biết field đã điền và bước
  CAPTCHA/submit còn thủ công; nút **Điền lại**, **Kiểm tra lại** và **Sao chép
  chẩn đoán** chỉ làm việc trên tab hiện tại, không tự submit hoặc click CAPTCHA.
  Extension không chèn panel nổi vào trang form; trạng thái và thao tác hỗ trợ
  chỉ nằm trong popup để không che giao diện website. Badge trên icon cập nhật
  riêng theo tab: `…` đang xử lý, `✓` đã điền/submit, `C` chờ CAPTCHA, `!` cần
  thao tác thủ công và `×` khi adapter gặp lỗi.
- **Domain Worker** — nút precheck kiểm tra email và cloaking đồng thời; case
  cloaking chỉ được tách ngay khi có email nhận, còn domain thường mới đi vào
  job gửi batch. IP/ASN Cloudflare chỉ được xem là proxy/CDN: tool không gửi
  `abuse@cloudflare.com` và loại case chỉ có địa chỉ này khỏi job.
- **Cloaking Review** — hàng đợi bền vững để xem evidence của từng domain, xem
  trước đúng draft/email nhận rồi gửi SMTP trực tiếp; không hiển thị case không
  có email nhận và không tạo worker job gửi. Khi chọn **Không phải cloaking**,
  page yêu cầu Browser Evidence report thường: ưu tiên hai ảnh URL nguồn/đích,
  fallback một ảnh nguồn hoặc upload 1–3 ảnh.
- **Domain Evidence Review** — danh sách riêng trong ngày cho domain thường
  không capture được ảnh; upload 1–3 ảnh, xem thumbnail và tạo preview draft
  trước khi gửi trực tiếp khi worker và các batch khác vẫn đang chạy. Mỗi
  delivery hiển thị rõ account/email nhận/draft; retry chỉ gửi lượt còn thiếu.
- **Thống kê email** — menu gọn để xem **tổng mail nhận trong ngày** của đúng
  một tài khoản: Inbox, Thư rác và tổng hai thư mục. Mặc định là hôm nay; nút
  kiểm tra chạy nền chỉ mở Inbox + Thư rác, dùng `INTERNALDATE`, không tải body
  và không đổi cờ đã đọc. Số Mail Sent/report không xuất hiện và không bị đọc ở đây
  để tránh trùng chức năng.
- **Thống kê tổng quát** — chọn một account và khoảng ngày, rồi bấm một nút
  **Đồng bộ & tính thống kê**. Menu tự đọc Inbox, Sent, Thư rác, metadata
  evidence của Sent và phản hồi NCC cùng phạm vi; hiển thị tỷ lệ thư rác, gửi
  thành công, phản hồi, takedown và evidence, kèm bảng theo kênh/provider/
  subject/draft. Chỉ delivery đã có trong `sent_log.csv` mới tính là report;
  thư Sent quan sát được nhưng chưa khớp log chỉ dùng để bổ sung evidence và bị
  loại khỏi tỷ lệ. Mail Inbox/Junk không liên quan cũng không được tính là phản
  hồi NCC. Không cần mở menu khác trước.
- **Phản hồi NCC** — khi đồng bộ sẽ đọc Inbox, thư mục có cờ IMAP `\\Junk`
  (fallback theo tên Junk/Spam) và folder `2-Cloudflare`, gộp các phản hồi tìm
  được rồi hiển thị thống kê riêng cho từng folder theo ngày địa phương; không
  tính thư Đã gửi. Lỗi đọc Junk hoặc `2-Cloudflare` không làm mất kết quả Inbox.
  Nút **Seen all** nhóm UID theo mailbox nguồn để cập nhật đúng toàn bộ ba
  folder, kể cả thư bị loại khỏi danh sách NCC; bảng
  đối soát hiển thị tổng, số đưa vào danh sách NCC và số không liên quan.
  Sau mỗi lần **Kiểm tra**, kết quả được lưu theo ngày vào
  `data/mail_statistics_cache.json` và tự hiện lại khi mở trang; nút **Xóa cache
  ngày đã chọn** chỉ xóa ngày đang chọn. Cache không chứa password hay body thư.
  Thống kê tổng quát lưu snapshot đã sanitize theo account/khoảng ngày tại
  `data/general_statistics_cache.json`; cache Sent chỉ giữ header, URL và
  metadata attachment ở `data/sent_mail_evidence_cache.json`. Cả hai không lưu
  body, credential hoặc bytes ảnh; snapshot tổng quát còn whitelist dữ liệu
  hiển thị và redact lỗi IMAP trước khi ghi. Delivery cũ thiếu metadata
  evidence luôn ở nhóm **chưa phân loại**, không suy đoán có hay không có ảnh.

Trong khu vực **Browser Blocking**, **Quick Report** dùng Web Form Assistant
v2.2.1 để mở và tự điền form **Chống Lừa Đảo** và **Cốc Cốc Safe** trên Chrome
profile hiện tại. Adapter điền full URL, email liên hệ, nội dung trung lập và
loại `Phishing`/`Trang web lừa đảo`; không tự giải CAPTCHA hoặc submit. **Check
  Domain** vẫn giữ hai link mở form thủ công.
  Riêng Cốc Cốc dùng sự kiện `mousedown` của Material UI và chỉ báo điền thành
  công khi hidden field `type` có giá trị `1` tương ứng **Trang web lừa đảo**.

Web UI gọi thẳng cùng các hàm trong `phishing_toolkit.py` mà CLI dùng (không viết
lại logic riêng), nên kết quả giữa CLI và web luôn khớp nhau.

## Quy trình đầy đủ

Xem `plan_phishing_takedown.md` — quy trình 8 bước từ phát hiện, xác minh,
thu thập bằng chứng, báo cáo theo đúng thứ tự ưu tiên, tới theo dõi kết quả.

## Cấu trúc file

```
phishing_toolkit.py       - Tool chính (check / related / brandscan)
cloaking_detector.py      - Detector HTTP đa profile + xác minh Playwright thụ động
browser_evidence.py       - Lõi Browser Evidence thụ động + mở URL từ DOM tùy chọn
cloaking_ui.py            - Khối hiển thị kết quả cloaking dùng chung cho Streamlit
cloaking_review_queue.py  - Hàng đợi review cloaking bền vững giữa các worker job
cloaking_review_sender.py - Chuẩn bị preview và gửi trực tiếp case Cloaking Review
domain_worker.py          - Precheck email/cloaking và worker batch cho domain thường
report_statistics.py      - Phân tích hiệu quả report theo account/khoảng ngày từ metadata local
sent_mail_evidence.py     - Đồng bộ Sent Mail theo account và lập chỉ mục MIME evidence
general_statistics.py     - Điều phối đồng bộ Inbox/Sent/Junk và snapshot Thống kê tổng quát
domain_check.py           - Bản đơn giản chỉ check SSL + WHOIS (không cần API key)
streamlit_app.py           - Trang chủ giao diện web (streamlit run streamlit_app.py)
pages/                      - Các trang còn lại của giao diện web (multipage app)
config.example.ini        - Template cấu hình, copy thành config.ini
plan_phishing_takedown.md - Quy trình làm việc chi tiết
case_log.csv               - Tự sinh ra sau khi chạy check lần đầu
reports/                   - Tự sinh ra, chứa email báo cáo đã điền sẵn
```

### Browser Evidence — Phase 1

Lõi `browser_evidence.py` mặc định chỉ mở trang và đọc DOM, tuyệt đối không click,
nhập liệu hoặc submit form. Mỗi lần capture hợp lệ tạo một ảnh PNG có panel kỹ
thuật và một manifest JSON chứa URL được yêu cầu, landing URL, redirect HTTP,
profile, control DOM, destination đã resolve và SHA-256 của ảnh. Loại evidence
mặc định là `dom_observed`; không được diễn giải thành redirect đã xác minh.
Trang lỗi trình duyệt/DNS và cảnh báo phishing của provider bị loại khỏi evidence
nội dung.

Phase 2.1 cung cấp hàm **opt-in**
`capture_dom_destination_evidence()` cho báo cáo phishing thông thường khi
cần ghi nhận trang đích được công khai trong DOM. Hàm chỉ chọn control đang hiển thị có nhãn
Register/Login (hoặc tương đương), là anchor HTTP(S) hoặc button `data-href`
không phải submit. Browser cô lập chụp `source_before_open`, mở DOM href trong
tab mới cùng context với referrer trang nguồn, rồi chụp `destination_after_open`.
Manifest `dom_destination_opened` ghi rõ đây không phải click, đồng thời lưu DOM
href, URL cuối, redirect quan sát được, tiêu đề hai trang và hash của cả hai ảnh.
Không click, nhập credential, type, submit form hoặc tải file.
Nếu không có URL DOM khác trang nguồn hoặc trang đích là terminal/browser
error, hàm fail closed để caller dùng capture thụ động hoặc upload thủ công.
Chế độ này chưa được bật tự động cho detector cloaking; cloaking vẫn luôn dùng
HTTP đa profile và Playwright thụ động.

Phase 2 đã chuyển **Phản hồi NCC** sang lõi này. Nút tạo ảnh DOM vẫn mở Chrome
có giao diện nhưng capture không click hoặc submit. Page ưu tiên chụp hai ảnh:
URL nguồn và URL đích được đọc từ control Register/Login; nếu không có URL đích
an toàn thì fallback sang một ảnh nguồn thụ động. Evidence được giữ trong session
qua rerun, hiển thị thumbnail, requested/landing/DOM/final URL; khi gửi reply,
toàn bộ PNG cùng manifest đã kiểm tra hash được đính kèm đúng theo preview. Draft
DOM-open mô tả thao tác mở URL trực tiếp, không khẳng định đã click. Upload thủ
công là fallback duy nhất khi Browser Evidence tự động không tạo được artifact.

Các consumer đã tích hợp theo các phase riêng: Provider Replies và Check Domain
  dùng lõi Browser Evidence; Domain Worker cho domain thường tự thử capture DOM
  destination (ảnh nguồn + ảnh đích), rồi fallback sang capture thụ động nếu không có
  URL tĩnh hoặc không mở được đích; Quick Report vẫn là luồng web form độc lập.

### Browser Evidence — Phase 2 (Check Domain)

Check Domain bắt buộc có Browser Evidence hợp lệ trước khi cho gửi email. Trong
expander **Bằng chứng trình duyệt**, chế độ `Passive DOM` (mặc định) chỉ đọc
trang. Khi cần ghi nhận đường dẫn lừa đảo, người vận hành chọn `Mở URL từ DOM`:
browser cô lập tìm control Register/Login an toàn, chụp trang nguồn, mở URL đọc
được trong tab mới cùng context/referrer và chụp trang đích. Preview hiển thị hai
thumbnail cùng URL nguồn, DOM href, URL đích cuối và redirect chain.

Capture hợp lệ được chèn thành khối kỹ thuật tiếng Anh vào mọi draft; chính PNG +
manifest đã preview được truyền cho cả gửi một draft lẫn gửi tất cả. Verified
DOM destination luôn dùng đúng hai ảnh. Nếu không tìm được control an toàn, DOM
href không khác trang nguồn, hoặc gặp trang terminal, capture fail closed; người vận hành có thể quay về
`Passive DOM` hoặc tải trực tiếp 1–3 ảnh PNG/JPEG. Ảnh thủ công hiển thị thumbnail
ngay, được kiểm tra signature/kích thước và tự tạo manifest hash, không có nút lưu
trung gian. Nút gửi chỉ mở khi toàn bộ artifact còn hợp lệ.

Khối gửi ra ngoài không dùng log kiểu `Technical Evidence` làm nội dung chính.
Formatter chuyển manifest thành phần **Observed Phishing Behavior and Supporting
Evidence**: mô tả control Register/Login, URL công khai trong `href`, các bước
tái hiện, URL đích cuối nếu đã mở, ảnh đính kèm và yêu cầu điều tra/xử lý theo
chính sách phishing. Manifest kỹ thuật vẫn được đính kèm để kiểm tra hash.
Capture thụ động chỉ nói URL được đọc từ markup; không tự tuyên bố đã click hoặc
thu thập credential/OTP/payment khi chưa có bằng chứng tương ứng.

Browser Evidence tự chọn một trong ba nội dung report: `control_with_destination`
cho control có HTTP(S) URL; `control_without_destination` cho control có thật nhưng
navigation phụ thuộc JavaScript/runtime; và `page_without_auth_control` khi không
tìm thấy nút phù hợp. Capture cũng chỉ đếm metadata của field đang hiển thị (không
đọc giá trị): password, OTP/verification code, payment và identity/contact. Email
chỉ mô tả khả năng thu thập loại dữ liệu tương ứng khi field đó thực sự được quan
sát; nếu không có field, email vẫn report suspected phishing/brand impersonation
dựa trên ảnh trang nhưng không bịa chi tiết credential.

Quality gate chặn draft thiếu Subject, recipient, sai full Reported URL, còn
placeholder hoặc thiếu attachment hợp lệ. Draft legacy còn bằng chứng từ dịch
vụ scan cũ sẽ bị loại ở ranh giới gửi và không được tạo mới. Gate còn đối chiếu
full URL trong manifest với report hiện tại để
không gắn nhầm evidence giữa hai path; detector cloaking và Domain Worker không click.

Các nội dung dùng để dán vào **web form** (GSB, Cloudflare, registrar và
registry) luôn
ghi đúng full URL/path. Mẫu không chèn kết quả scan bên thứ ba và không
tự khẳng định đã lấy OTP/thông tin thanh toán khi chưa có quan sát chứng minh;
thay vào đó yêu cầu nhà cung cấp điều tra và áp dụng chính sách nếu xác nhận.
Riêng mẫu Registrar/Registry mô tả rõ luồng nghi vấn có control đăng ký/đăng
nhập dẫn tới nội dung giả mạo, nguy cơ người dùng cung cấp credential/thông tin
cá nhân và hậu quả có thể gồm chiếm quyền tài khoản, gian lận hoặc thiệt hại tài
chính. Các nhận định này luôn dùng ngôn ngữ điều kiện (`suspected`, `may`, `if
confirmed`) để không biến nguy cơ chưa xác minh thành kết luận sự thật.
GSB và Cloudflare có hai pool riêng, mỗi pool 5 biến thể: GSB yêu cầu cảnh báo/
chặn ở Safe Browsing, còn Cloudflare yêu cầu xử lý dịch vụ liên quan hoặc chuyển
tiếp tới origin hosting provider. Biến thể được chọn ổn định theo domain + ngày.

Email registrar có pool Subject/nội dung riêng và chỉ đưa VirusTotal vào khi có
detection. Email registry mặc định yêu cầu điều tra/phối hợp registrar, không tự
khẳng định registrar đã bỏ qua báo cáo và không yêu cầu ClientHold như một kết
luận có sẵn. Chỉ truyền trạng thái `registrar_reported` khi có lịch sử delivery
xác nhận; mọi draft registry đều giữ cả registered domain và full Reported URL.

Mỗi kênh Registrar và Registry hiện có 5 biến thể nội dung riêng, được chọn ổn
định theo domain và ngày để nội dung không đổi khi Streamlit rerun. Quick Report
vẫn giữ nguyên một bước kiểm tra hiện tại, không mở thêm website hoặc tự thu thập
chứng cứ. Nếu một workflow đã có quan sát được xác minh (title, control đăng
nhập, redirect/đích cuối và số attachment), formatter có thể chèn đúng các dữ
kiện được truyền vào; dữ kiện thiếu không được suy diễn và đường dẫn file cục bộ
không xuất hiện trong draft.
Các biến thể viết theo góc nhìn người đại diện thương hiệu/tổ chức
(`We are submitting...`). Khi chưa có lịch sử báo Registrar, draft Registry chỉ
nói tập thể đã phát hiện URL nghi phishing và đang gửi để xem xét; không chèn câu
phủ định máy móc về một báo cáo Registrar chưa tồn tại.

Email gửi qua SMTP có chữ ký tổ chức dùng chung: tên người gửi/email theo từng
account, tiếp theo là `Brand Protection — OKWIN Media Co., Ltd` và
`Business Registration No. 0318893644`. Logo tại `assets/email/logo-win.jpg`
được nhúng inline 64 px ngay trước `Regards` bằng Content-ID; mail vẫn có bản
plain text đầy đủ và evidence tiếp tục là attachment riêng. Có thể đổi các giá
trị bằng `signature_role`, `company_name`, `business_registration_no` và
`signature_logo` trong `[company]`.
`Reported URL` và mọi khối evidence luôn được chèn trước `Regards`; toàn bộ tên,
email, pháp nhân và mã đăng ký được giữ liền nhau ở cuối thư.

## Lưu ý cho Windows

Nếu chạy trong PowerShell/CMD và gặp `UnicodeEncodeError` khi in ra tiếng Việt có dấu, set biến môi
trường UTF-8 trước khi chạy:

```powershell
$env:PYTHONIOENCODING = "utf-8"
python phishing_toolkit.py check <domain>
```

Nếu lệnh `streamlit` hoặc `dnstwist` báo "not recognized" dù đã `pip install`, đó là do
pip cài script vào thư mục không nằm trong PATH của Windows — chạy qua `python -m` thay thế:

```powershell
python -m streamlit run streamlit_app.py
```

## Lưu ý an toàn

- Không mở trang phishing bằng máy/tài khoản thật — dùng máy ảo hoặc trình duyệt cô lập.
- `config.ini` chứa API key, không commit lên git (đã có trong `.gitignore`).
- Google Trust Services không nhận report vì lý do phishing — tool tự bỏ qua bước này khi phát hiện.
- Google Safe Browsing chỉ hỗ trợ *kiểm tra* trạng thái qua API, việc *report* vẫn phải làm thủ công tại https://safebrowsing.google.com/safebrowsing/report_phish/

## Worker xử lý nhiều domain

Trang **Domain Worker** nhận danh sách domain và tự chạy pipeline kiểm tra, sinh
draft, rồi gửi những draft có địa chỉ email hợp lệ. Mặc định mỗi batch xử lý 5
domain, nghỉ 5 phút rồi mới lấy batch tiếp theo.

Luồng sử dụng hiện tại:

1. Chọn tài khoản gửi, dán full URL hoặc nguyên nội dung thô vào **một ô nhập duy
   nhất**, rồi bấm **Check toàn bộ, lọc email & cloaking**. Tool tự trích URL,
   giữ path/query, loại trùng và bỏ qua ghi chú hay token không hợp lệ; một dòng
   lỗi không chặn các URL hợp lệ còn lại. Với từng URL, lookup email và detector cloaking chạy đồng thời.
   Cache trong ngày chỉ áp dụng cho email; cloaking luôn được kiểm tra mới theo
   đúng full URL/path. Pha này không gửi email.
2. Ngay khi một URL có verdict `LIKELY`, `POSSIBLE`, `INCONCLUSIVE` hoặc thiếu
   vantage **và có ít nhất một email nhận**, case được ghi vào **Cloaking
   Review** và hiện trong bảng “Cloaking tách riêng”; không cần đợi hết danh
   sách precheck. Case nghi ngờ cloaking nhưng không tìm được email được ghi vào
   nhóm bỏ qua trong ngày, không tạo queue review. Case đã tách không nằm trong
   danh sách gửi tự động của Domain Worker.
3. Từ precheck schema v4, worker thử mở URL Register/Login được đọc từ DOM trong tab mới
   cùng context/referrer để chụp ảnh nguồn và ảnh đích. Nếu không có destination HTTP(S)
   tĩnh hoặc mở đích không thành công, worker fallback sang capture thụ động trang nguồn.
   Chỉ capture thất bại thật sự mới vào **Domain Evidence Review**; page này gộp URL duy nhất
   trong ngày, hiển thị thumbnail và cho upload 1–3 ảnh. Bấm **Tạo / cập nhật draft để xem**
   để chạy pipeline một lần và xem đúng To/Subject/body theo từng account + recipient; chưa có
   email nào được gửi ở bước này. Sau khi xác nhận, hệ thống gửi đúng delivery plan đã preview,
   kiểm tra lại fingerprint ảnh/draft trước SMTP; gửi lỗi giữ evidence và trạng thái từng lượt để
   retry chỉ phần còn thiếu, còn bản ghi trùng ở các job cũ được đánh dấu hoàn tất. Domain Worker
   chỉ hiển thị số lượng/link và không còn uploader inline; page review vẫn hoạt động khi worker
   đang prechecking/running.
   Trang lỗi trình duyệt/DNS được đánh dấu terminal, không yêu cầu upload ảnh lỗi và vẫn gửi draft thường.
4. Có thể mở **Cloaking Review** ngay trong lúc precheck hoặc Domain Worker thường
   đang chạy. Chọn một case, chọn chế độ/tài khoản, bấm **Tạo / cập nhật draft để
   xem**, đọc đúng nội dung sẽ gửi rồi xác nhận gửi trực tiếp. Trang không tạo hay
   chờ worker job gửi mail.

Phần retry hiển thị số URL lỗi/dở dang và chưa xử lý sẽ chạy lại. Bảng hiện
**Đang retry** cho URL đang xử lý và **Chờ retry** cho các URL trong hàng đợi,
ưu tiên hơn kết quả lỗi của lần trước. Tiến độ chỉ tính URL của lần chạy hiện
tại; thời gian nghỉ giữa batch vẫn theo cấu hình. Nếu kết nối SMTP tiếp tục
timeout, domain vẫn được thử lại nhưng chỉ đổi kết quả khi lần gửi kết thúc.

Sau khi job hoàn tất, bấm **Retry phần lỗi / còn thiếu** để gửi lại các lượt chưa
thành công; nếu lần retry vẫn lỗi, có thể tiếp tục bấm retry sau khi xử lý nguyên
nhân. Worker không tự lặp lại toàn bộ job. Nút **Dừng hẳn tiến trình** kết thúc
worker; chỉ khi bạn bấm **Chạy lại phần lỗi / còn thiếu** và xác nhận gửi thì
worker mới chạy tiếp. Nếu dừng khi precheck chưa xong, bấm **Chạy lại precheck**
trước (chưa gửi email). Kết quả mỗi URL chỉ hiển thị lần xử lý mới nhất, không
cộng dồn lỗi cũ. Delivery đã ghi nhận thành công trong ngày vẫn được bỏ qua.

Worker chạy bằng process riêng nên vẫn tiếp tục nếu đóng hoặc refresh tab trình
duyệt. Trang này hiển thị tiến độ ở trên, tiếp theo là cấu hình gửi worker, rồi
đến bảng kết quả domain trong fragment real-time. Cột **Đã gửi đến** liệt kê các
email nhận đã gửi thành công trong ngày;
một cột **Trạng thái tài khoản** duy nhất liệt kê từng account với trạng thái
**Đã gửi**, **Một phần**, **Lỗi** hoặc **Chưa gửi** cùng recipient tương ứng.
Thêm account mới không làm phát sinh thêm cột. Bảng giữ chiều cao tối thiểu đủ
để theo dõi batch ngay cả khi mới chỉ có ít kết quả. Đồng thời page có nút dừng hẳn
process worker. Ngay sau khi bấm khởi chạy hoặc retry, page tự chuyển sang theo dõi
real-time mỗi 3 giây; không cần bấm **Làm mới trạng thái** để kích hoạt polling.
Mỗi email thành công được ghi ngay vào `sent_log.csv` kèm metadata
account, Message-ID, recipient, kênh, draft/subject và evidence source/count; job và danh
sách mới tự bỏ qua delivery đã gửi thành công trong ngày hiện tại.
Khu **Cần bạn xử lý** gom link Cloaking Review và Domain Evidence Review tại một
vị trí. Form gửi chỉ xuất hiện khi có URL ready và checkbox cho phép gửi thật
luôn mặc định tắt. Page mặc định chỉ phục hồi job của ngày địa phương hiện tại;
job cũ vẫn nằm trên đĩa để audit nhưng không trở thành job thao tác của ngày mới.
Khi job đang `prechecking`, `running` hoặc `waiting`, riêng khối tiến độ tự đọc
`status.json` mỗi 3 giây bằng Streamlit fragment; form nhập và cấu hình không bị
rerun theo nhịp này. Worker ghi stage hiện tại (lookup/cloaking, Browser Evidence,
draft/delivery hoặc nghỉ batch) để UI giải thích đang làm gì. Đây là khối tiến độ
duy nhất; page không dựng lại một bộ metric batch tĩnh phía dưới. Trước khi cho phép
gửi, form chỉ tóm tắt số delivery dự kiến còn thiếu/đã gửi hôm nay; danh sách route
chi tiết không được lặp thành một bảng ít dùng. Draft cuối vẫn được tạo và validate
trong worker. Lỗi delivery mới có mã stage như SMTP auth, kết nối,
recipient rejected hoặc draft validation để retry dễ chẩn đoán hơn.
Draft VNCERT mặc định không tự gửi; chỉ bật nếu toàn bộ danh sách thực sự
nhắm tới nạn nhân tại Việt Nam. Job Domain Worker thường nằm trong
`data/worker_jobs/`; queue case và delivery ledger của Cloaking Review nằm trong
`data/cloaking_review/`. Cloaking Review mới gửi đồng bộ ngay trên page và không
tạo thư mục job. `data/cloaking_send_jobs/` chỉ còn được đọc để migrate/sync lịch
sử job review cũ. Các thư mục runtime này không được commit.

### Cơ chế cloaking trong worker

Mỗi domain trước tiên được kiểm tra bằng sáu HTTP profile. Detector tách riêng
**kết luận cloaking** (nội dung có đổi theo profile hay không) và **kết luận nội
dung** (nội dung cờ bạc có đang công khai hay chỉ xuất hiện ở một số profile).
Do đó, một URL cờ bạc công khai có thể là `GAMBLING_EXPOSED` nhưng vẫn
`NO_SIGNAL` về cloaking. Trang `/vi-vn/` trả 404 khác trang gốc cũng không còn bị
coi là cloaking. Kết quả cloaking có bốn mức:

- `LIKELY`: bằng chứng đủ mạnh nhưng vẫn được tách khỏi luồng gửi tự động để
  người vận hành xác nhận.
- `POSSIBLE` hoặc `INCONCLUSIVE`: worker tự chạy Playwright headless với desktop
  trực tiếp, Android từ Google, iPhone từ Google và Googlebot Smartphone. Worker
  cũng chạy bước này cho `LIKELY` để chụp lại evidence. Mỗi profile được quan sát
  sau 1 giây, 5 giây và sau warm reload. Playwright chỉ tải trang, đọc DOM/tài
  nguyên và chụp ảnh; không click, nhập liệu hay gửi form.
- Nếu response khai báo biến theo cả quốc gia/IP và thiết bị nhưng chưa có vantage
  ngoài mạng hiện tại, worker cũng chạy Playwright rồi chuyển domain sang manual
  review nếu vẫn thiếu độ phủ; `NO_SIGNAL` trong trường hợp này không được tự gửi.
- Nếu Playwright nâng kết quả lên `LIKELY`, domain vẫn chuyển sang **Cần duyệt
  cloaking** và chưa gửi email. `POSSIBLE`/`INCONCLUSIVE` cũng được giữ lại.
- Nếu toàn bộ profile hiển thị cảnh báo phishing của Cloudflare hoặc trang lỗi
  trình duyệt như **Không thể truy cập trang web này**, detector ghi nhận
  `BLOCKED_OR_UNAVAILABLE` và bỏ các trang đó khỏi phép tính cloaking. Worker
  không yêu cầu duyệt cloaking, không đính kèm ảnh lỗi và tiếp tục gửi draft bình
  thường. Trạng thái này không tự khẳng định domain đã bị thu hồi; WHOIS Hold/link
  status vẫn là nguồn xác nhận riêng.
- Ngay trong bước **Check toàn bộ, lọc email & cloaking**, lookup email và
  detector chạy đồng thời. Mỗi case cần duyệt chỉ được ghi vào queue theo từng
  URL khi lookup tìm thấy ít nhất một email nhận. Case không email vẫn lưu kết
  quả precheck/no-email để tránh lookup lại trong ngày nhưng không hiện ở
  Cloaking Review. Queue/preflight được ghi tăng dần trước khi toàn bộ precheck
  hoàn tất; Domain Worker chỉ nhận danh sách không cloaking có email. Refresh,
  đóng tab hoặc chạy job mới không làm mất danh sách chờ duyệt hợp lệ.
- Tại **Cloaking Review**, chọn một URL để xem tín hiệu/manifest/ảnh và chọn
  **Xác nhận cloaking** hoặc **Không phải cloaking**. Nút **Tạo / cập nhật draft
  để xem** chạy pipeline draft dùng chung ngay trên page nhưng chưa gửi email;
  UI hiển thị chính xác tài khoản gửi, email nhận, subject và body cho từng
  delivery. Chỉ sau khi người vận hành đọc và tích xác nhận thì nút gửi trực tiếp
  mới được mở. Checkbox xác nhận chỉ rerun giao diện; case đang mở vẫn được giữ
  bằng `queue_id`, không phải chọn lại domain.
- Với cloaking đã xác nhận, draft tiếng Anh ghi rõ kết luận thủ công, dùng
  evidence đã duyệt trong queue và đính kèm manifest + đúng hai ảnh đại diện.
  Thiếu/rỗng/quá 10 MB một attachment hoặc thiếu cặp ảnh thì chặn gửi. Với
  **Không phải cloaking**, tool loại toàn bộ khối evidence/attachment cloaking,
  rồi yêu cầu Browser Evidence riêng cho report phishing thông thường. Nút capture
  ưu tiên hai ảnh URL nguồn + URL đích từ DOM, fallback một ảnh nguồn; nếu chưa
  capture trước thì thao tác tạo draft tự chạy bước này. Khi tự động thất bại có
  thể upload 1–3 ảnh. Browser Evidence được chèn vào draft và đính kèm
  cùng manifest. Hash/size của attachment được khóa theo preview; file bị đổi trước
  lúc bấm gửi sẽ bị chặn.
- Cloaking Review không launch process và không tạo job gửi. Nội dung đã preview
  được chuyển nguyên vẹn vào SMTP helper hiện có; kết quả từng delivery được ghi
  ngay vào ledger để một lần gửi bị gián đoạn không gửi lại email đã thành công.
  Vì vậy thao tác này độc lập với precheck/Domain Worker đang chạy.
- Queue gộp theo **ngày địa phương + URL chuẩn hóa**, không theo worker job
  hay số tài khoản email. Cùng URL bị phát hiện nhiều lần trong ngày chỉ
  hiện một dòng với evidence mới nhất và lịch sử source job; sang ngày mới
  bảng chỉ hiện case của ngày mới và URL phải được check lại để tạo case mới.
  Dữ liệu ngày cũ vẫn giữ nội bộ cho audit/ledger, chỉ không còn hiện trên UI.
  Bản ghi legacy bị gộp được chuyển vào
  `data/cloaking_review/archive/` thay vì xóa. Chọn đúng một case bằng nút
  **Xử lý** trong bảng native Streamlit để luôn xem draft trước khi gửi.
- Giao diện chỉ có một bảng domain của hôm nay, không còn KPI và bộ lọc
  **Chờ xử lý/Đã xử lý**. Mỗi dòng hiện trực tiếp trạng thái `Chưa gửi`,
  `⚠️ Gửi một phần`, `❌ Gửi thất bại` hoặc `✅ Gửi thành công`. Dòng thành công
  không còn nút xử lý; dòng lỗi có nút **Thử lại**, dòng gửi một phần có nút
  **Gửi tiếp**.
- Mỗi case vẫn theo dõi riêng từng cặp **tài khoản gửi + email nhận + draft**.
  Trạng thái chỉ chuyển sang `SENT` khi tất cả tài khoản SMTP thuộc phạm vi của
  case đã giao đủ draft. Nếu mới hoàn tất một phần, case ở `PARTIAL`, tiếp tục
  có nút **Gửi tiếp** và lần gửi sau mặc định chỉ chọn các tài khoản còn
  thiếu; delivery đã gửi hoặc đã có trong cache hôm nay không bị gửi lại.
- Bảng và phần chi tiết hiển thị **Email nhận**, **Đã gửi từ**, **Còn chờ** và
  tiến độ như `1/2`. Nếu một tài khoản còn thiếu đã bị xóa khỏi `config.ini`, UI
  cảnh báo và giữ case chờ cho tới khi tài khoản đó được cấu hình lại. Nếu một
  job mới trong cùng ngày bổ sung tài khoản gửi cho URL đã hoàn tất, case được
  mở lại thành `PARTIAL` thay vì làm mất nghĩa vụ gửi mới.
- Nếu bạn đã tự quan sát cùng URL hiển thị khác nhau, mở **Bổ sung bằng chứng
  cloaking thủ công** ở Check Domain hoặc case tương ứng trong Cloaking Review, tải
  2–4 ảnh PNG/JPEG/WebP và xác nhận cặp ảnh. Nếu Playwright chưa có đủ cặp ảnh
  hoặc case còn `INCONCLUSIVE`, uploader tự mở; ảnh hợp lệ hiện thumbnail ngay
  trên một hàng. Không còn nút **Lưu ảnh thủ công**: nút **Xác nhận ảnh & tạo
  draft để xem** kiểm tra toàn bộ file, tự lưu evidence/manifest rồi tạo preview
  trong cùng một thao tác. Case chuyển tối đa lên `POSSIBLE` và vẫn phải đọc
  draft, tích xác nhận trước khi gửi. Khi evidence tự động đã đầy đủ, uploader
  được thu gọn dưới công tắc **Thay hoặc bổ sung ảnh thủ công**.

Trên **Check Domain** và **Quick Report**, HTTP detector chạy cùng thao tác check.
Khi kết quả là `POSSIBLE`/`INCONCLUSIVE`, nút xác minh Playwright xuất hiện để
người dùng chủ động chạy bước trình duyệt nặng hơn. Bằng chứng nằm trong
`evidence/cloaking/`; nội dung trang đầy đủ không được đưa vào giao diện hoặc
manifest, chỉ giữ preview, hash, metadata và ảnh chụp. Gallery ảnh Playwright
hiển thị dạng thumbnail nhỏ trên một hàng để phục vụ đối chiếu nhanh.
Riêng **Quick Report**, verdict và điểm nằm ngay trên nhãn **Chi tiết kiểm tra
cloaking**; hai thông báo Cloaking/Nội dung chỉ hiển thị sau khi mở phần chi tiết
để danh sách nhiều URL không bị kéo dài.

Phần cloaking được chèn vào email nhà cung cấp luôn được soạn bằng tiếng Anh.
Nhãn/mô tả tiếng Việt chỉ dùng trong giao diện nội bộ; page title và matched
keyword có thể giữ nguyên ngôn ngữ của website vì đó là dữ liệu bằng chứng.

### Cấu hình vantage mạng cho cloaking

Khi website chỉ lộ nội dung ở một quốc gia/IP khác, cấu hình proxy điều tra trong
`config.ini` theo schema của `config.example.ini`. Không dùng proxy SMTP ở mục
`[smtp]` cho detector. Ví dụ placeholder:

```ini
[cloaking]
vantage_points = [{"name":"VN mobile","country":"VN","proxy":"http://user:password@proxy.example:8080","browser":true}]
```

Mỗi vantage thêm một desktop trực tiếp và một mobile Google vào lớp HTTP;
`browser=true` thêm mobile Google vào Playwright. Tên/quốc gia được ghi vào
manifest, còn URL proxy và credential không được ghi vào evidence hoặc UI.

### Tự điền form .CO Registry

Với URL thuộc TLD `.co`, Quick Report hiển thị nút **Mở & tự điền
form Registry Co**. Web Form Assistant v2.7.0 mở form chính thức
`registry.co/report-abuse/form?type=phishing` và điền tên/email người báo
cáo, loại phishing, brand, domain, full URL/path và nội dung report. Evidence
upload, xác nhận thiện chí, reCAPTCHA và submit luôn do người vận hành
thực hiện; extension không tự tích, giải CAPTCHA hay gửi form.

Với `.xyz` và các TLD cùng registry như `.monster`, `.quest`, `.baby`,
`.cars`, `.beauty`, `.hair`, `.homes`, `.game`, `.lol`, `.mom`, `.pics`,
`.hosting`, `.audio`, `.diet`, `.ceo`, Quick Report hiển thị nút **Mở & tự
điền form XYZ.COM**. Web Form Assistant v2.8.0 mở ticket Anti-Abuse chính thức,
điền tên/email người báo cáo, loại `Phishing`, domain và nội dung report. Người
vận hành phải tự kiểm tra, thêm attachment, xử lý CAPTCHA nếu có và submit.

Quick Report hiển thị thông báo gọn sau khi mở form Google/Microsoft;
không render đối tượng nội bộ `DeltaGenerator` ra giao diện.

Với `.org` và `.ong`, Quick Report định tuyến kênh Registry tới web form abuse
chính thức của Public Interest Registry (PIR): `https://pir.org/report-abuse/`.
Ứng dụng chỉ mở form thủ công và cung cấp nội dung report copy-ready, không tự
submit.
# Quick Report web: provider actions (2026-10-16)

Đối chiếu registrar/registry với bản cũ và hướng dẫn test:
[`docs/quick-report-routing-audit.md`](docs/quick-report-routing-audit.md).
Kết quả hiển thị hạ tầng bằng icon + tên, và hai dòng **Registrar: email**,
**Registry: email**. Các nút theo thứ tự Google → Microsoft → Chống Lừa Đảo →
Cốc Cốc → Cloudflare → Registrar → Registry (chỉ hiện kênh áp dụng).
Form có adapter chỉ hiện một nút extension; mất kết nối thì nút này bị khóa.
Form khác mở thẳng URL bằng nút **Registrar · tên** hoặc **Registry · tên**,
draft đúng kênh được hiển thị inline để copy. Khối Cloaking HTTP đã
ẩn khỏi giao diện; backend vẫn kiểm tra như trước. Email là contact để đối
chiếu, không có thao tác SMTP tự động tại đây.

**Thêm từng tài khoản mail:** vào Tài khoản gửi mail → Thêm tài khoản, nhập
SMTP username (email), host, port và password rồi Lưu tài khoản. Mỗi lần lưu
thêm một tài khoản; không cần nhập danh sách JSON hoặc đủ nhiều tài khoản.
Nhãn tùy chọn, bỏ trống sẽ lấy email. Port 465 tự chọn Implicit TLS (tương
đương `ssl=true` ở cấu hình cũ), 587 tự chọn STARTTLS. IMAP/proxy/chữ ký không
bắt buộc; chưa điền hồ sơ tổ chức vẫn có thể lưu mail. Thiếu trường hoặc lưu
lỗi sẽ hiện thông báo ngay trong hộp thoại. Lưu không tự kết nối/gửi mail.

Backend cần `DOMAINCHECK_SECRET_MASTER_KEY` trong `backend/.env` để mã hóa mật
khẩu mail (32 byte ngẫu nhiên, URL-safe base64). Thiếu khóa trả lỗi cấu hình
máy chủ, không phải trùng tài khoản. Sau khi đặt khóa cần restart backend.
Giữ khóa ổn định và sao lưu riêng: không thay khóa tùy tiện sau khi đã lưu mail.
Lỗi trùng chỉ ra cùng host/port/username của chính user và hướng dẫn mở Sửa.

Giao diện web dùng Sonner cho thông báo light/dark: lưu profile/mail/role,
cập nhật user, mở form, copy, export và kết quả Netcraft đều có phản hồi.
Thông báo có nút đóng; lỗi chi tiết vẫn nằm tại trang/biểu mẫu để đối chiếu.
Khóa user, thu hồi phiên, xóa role và tắt sender yêu cầu xác nhận trước khi
thực hiện. Nút đang xử lý được khóa để tránh bấm lặp. Quick Report có thanh
tiến độ và toast khi lượt đang theo dõi kết thúc; refresh lịch sử không tạo
toast hoàn tất mới. Mất mạng hiện banner; lỗi tải dữ liệu có nút thử lại.

Trước khi dùng Quick Report, mỗi user phải lưu đủ **Thương hiệu**, **Người liên
hệ** và **Email liên hệ hợp lệ** tại **Cài đặt mail → Profile tổ chức**. Trang
sẽ hiện các mục còn thiếu và link cấu hình; lưu xong quay lại Quick Report hoặc
bấm **Kiểm tra lại**. Backend cũng chặn check mới, task form, preview/submit
Netcraft và export nếu hồ sơ chưa đủ, kể cả gọi API trực tiếp. Không yêu cầu
SMTP/IMAP cho workflow này; cờ bật gửi Netcraft là điều kiện riêng của máy chủ.

Quick Report hỗ trợ tám form qua Web Form Assistant: Google, Microsoft,
Cloudflare, Chống Lừa Đảo, Cốc Cốc, GoDaddy, .CO và .XYZ. Backend chọn form
theo kết quả check; extension chỉ điền nội dung, người dùng duyệt và submit
thủ công. Reload extension sau khi cập nhật mã nguồn. Khi extension chưa kết
nối, trang cung cấp link mở form thủ công.

Netcraft có nút **Xem trước Netcraft**, hiển thị đúng email/URL/nội dung tiếng
Anh sẽ gửi. Cần contact email trong hồ sơ và xác nhận nội dung. Server mặc định
tắt gửi thật; muốn bật, đặt `DOMAINCHECK_NETCRAFT_SUBMIT_ENABLED=true` trong
`backend/.env` rồi khởi động lại backend. Trạng thái `UNKNOWN` không được gửi
lại tự động. Bản xem trước hết hạn sau 30 phút hoặc khi hồ sơ thay đổi.

Sau khi cập nhật backend, chạy Alembic `upgrade head` để tạo ledger migration
`0004_provider_submissions` trước khi dùng Netcraft.

Quick Report: nhóm Google GSB, Microsoft, Chống Lừa Đảo, Cốc Cốc và Netcraft
nằm trong một khối toàn chiều ngang, có nội dung đối chiếu bên dưới. Hàng kế
chia ba cột CDN, Registrar, Registry; mỗi cột có nút và nội dung riêng.
Nút extension ghi **Mở và tự điền**. Form thường mở thẳng URL trong tab mới,
không mở thêm modal copy. Chọn mẫu và copy ngay trong phần nội dung inline.
Netcraft dùng nút **Gửi API**: một lần bấm tạo exact preview phía server rồi gửi,
không có modal/checkbox xác nhận. Máy chủ vẫn phải bật gửi Netcraft và tài khoản
phải có quyền; nút khóa khi đã gửi, đang gửi hoặc kết quả chưa xác định. Lỗi
FAILED cho phép thử gửi lại explicit. Giao diện giảm padding/margin và chiều
cao preview; màn hình nhỏ tự xếp các cột dọc.

Quick Report ẩn hẳn nhóm CDN/Registrar/Registry không có form áp dụng; các nhóm
còn lại tự chia đều chiều ngang. Nút báo cáo dùng gradient xanh cyan–blue, nút
phụ copy/refresh giữ màu trung tính. Nếu Netcraft báo API chưa bật, đặt
`DOMAINCHECK_NETCRAFT_SUBMIT_ENABLED=true` trong `backend/.env`, rồi restart
FastAPI (reload mã nguồn không bảo đảm nạp lại biến môi trường). Đây là cờ cho
phép gửi thật khi người dùng bấm Gửi API, không tự gửi lúc tải trang. File mẫu
vẫn mặc định false cho cài đặt mới.

Quick Report đã bỏ khối Báo cáo nic.top và nút xuất Excel khỏi frontend theo yêu
cầu. Đây chỉ là tiện ích xuất URL .top để gửi thủ công, không phải bước kiểm tra
hay gửi report tự động. Backend export legacy được giữ tương thích; các form
Registry áp dụng theo domain vẫn hiển thị bình thường.

### Quick Report: cache trong ngày

Cache chống check mạng lặp lại được scope theo user/ngày/engine/full URL, hết
hạn vào nửa đêm theo múi giờ user. Nhãn Cache phản ánh nguồn của từng check.
Nút **Xoá cache hôm nay** xuất hiện khi có quyền `quick_report.clear_cache`,
chỉ xoá cache Quick Report tài khoản hiện tại trong hôm nay; không xoá lịch sử,
draft hoặc trạng thái gửi. Sau đó nhập lại URL và bấm Kiểm tra để check mới.
Nút bị khoá khi operation hiện tại đang tải/chạy hoặc đang tạo job. Redis lỗi
thì báo lỗi, không thông báo xoá thành công. Cache có thể mất nếu Redis restart.
### Quick Report: danh sách tích luỹ trong ngày (thay cách hiển thị theo operation)

Ô nhập URL luôn hiển thị; nhập danh sách mới rồi Lọc danh sách → Kiểm tra, không
cần bấm Danh sách mới. Khi một lượt đang chạy, vẫn nhập được nhưng nút check
chờ lượt đó kết thúc. Danh sách kết quả lấy từ database theo tài khoản/ngày,
không chỉ từ operation cuối: đã check 10 URL rồi thêm 2 URL mới thì giữ đủ 12.
F5/mở lại vẫn tải danh sách hôm nay; mỗi URL chỉ hiển thị bản check mới nhất.
Sang ngày mới, màn hình lấy ngày mới theo múi giờ user, lịch sử cũ không bị xoá.

Mặc định ẩn kết quả cũ, chỉ hiện kết quả mới của lượt gần nhất. Bỏ chọn
checkbox để xem lại lịch sử trong ngày. Bộ đếm: kết quả cũ (đã có trong ngày hoặc cache hit),
check mới (network trong lượt gần nhất), tổng URL duy nhất trong ngày. Nhãn
Cache trên từng thẻ chỉ xuất hiện khi check đó thực sự dùng Redis; kết quả cũ
trong database không bảo đảm Redis vẫn còn cache. Xoá cache hôm nay vẫn giữ
nguyên lịch sử trong DB nhưng dọn các thẻ đang hiển thị; nhập lại URL và bấm Kiểm tra để check mạng mới.

Nếu lịch sử hiện **Chưa có job**, đây là bản ghi pending/running cũ không có
job item Quick Report gắn với worker, không phải URL đang chờ được xử lý.
Nhập lại đúng URL ở ô nhập rồi bấm Lọc danh sách → Kiểm tra để tạo lượt mới.
Ứng dụng không tự đưa bản ghi này vào worker hoặc tự check lại khi tải lịch sử.

Ví dụ: check 10 URL lần đầu → hiện 10; check tiếp 10 URL mới → chỉ hiện 10 mới,
10 cũ tự ẩn và vẫn được tính trong tổng ngày. Bấm Xoá cache hôm nay → dọn danh
sách đang xem. Nhập lại toàn bộ 20 URL và Kiểm tra → hiện đủ 20 kết quả mới.
Mỗi lượt mới tự bật ẩn kết quả cũ, kể cả trước đó đã bỏ chọn để xem lịch sử.

Thứ tự thẻ Quick Report của lượt hiện tại và số # luôn theo danh sách URL vừa
nhập, khớp thứ tự worker chạy tuần tự. Nếu hiện lại lịch sử cũ, các URL ngoài
lượt hiện tại nằm phía sau; số # không còn dựa vào vị trí xuất hiện trong ngày.

### Trang đăng nhập và dashboard

- `/login` là trang đăng nhập Telegram riêng, hỗ trợ sáng/tối và mobile.
- `/` là dashboard cần đăng nhập, hiển thị các công cụ được cấp quyền, không có
  nút đăng nhập Telegram. Người chưa có phiên truy cập trang nội bộ sẽ về `/login`.
- Đăng nhập thành công luôn về `/`; tài khoản pending vẫn bị chặn và chờ duyệt.
- Đã đăng nhập mà mở `/login` sẽ tự về dashboard. Đăng xuất về `/login`.
- `/app` và `/pending` giữ làm đường dẫn tương thích về `/` sau khi qua kiểm tra phiên.
- Telegram bị huỷ, callback lỗi hoặc flow hết hạn quay về `/login` với thông báo
  an toàn. Lỗi kết nối `/me` không bị hiểu nhầm thành chưa đăng nhập; có nút thử lại.

Callback đăng ký trong BotFather vẫn là `/api/v1/auth/telegram/callback`; không
đổi sang `/login`. Không cần đổi Client ID/Secret hoặc cấu hình extension.
