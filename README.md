# Domain Check Tool — Phát hiện & báo cáo domain phishing

Bộ công cụ hỗ trợ 2 người rà soát, xác minh và báo cáo (takedown) các domain
giả mạo thương hiệu công ty để lừa đảo.

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
  minh thụ động bằng Playwright khi HTTP chưa đủ kết luận
- **Domain Worker** — nút precheck kiểm tra email và cloaking đồng thời; case
  cloaking chỉ được tách ngay khi có email nhận, còn domain thường mới đi vào
  job gửi batch
- **Cloaking Review** — hàng đợi bền vững để xem evidence của từng domain, xem
  trước đúng draft/email nhận rồi gửi SMTP trực tiếp; không hiển thị case không
  có email nhận và không tạo worker job gửi
- **Thống kê email** — chọn ngày (mặc định hôm nay) và đếm toàn bộ thư nhận, đã
  gửi và thư rác của từng tài khoản IMAP. Công cụ dùng `INTERNALDATE`, đổi sang múi giờ địa
  phương rồi mới lọc ngày; chỉ đọc khi bấm **Kiểm tra** và không đổi cờ đã đọc.
  Tài khoản chỉ cấu hình SMTP, không có `imap_host`, vẫn hiện trong bảng với trạng
  thái **Không có trong IMAP** và không bị thử kết nối.
  KPI và bảng chi tiết có thêm **Tổng nhận + rác** = Mail nhận + Thư rác.
  Nút Kiểm tra chạy bằng job nền bền vững: có thể chuyển menu/F5 trong lúc chạy;
  khi quay lại, trang đọc trạng thái và kết quả đã cache sau khi hoàn tất.
- **Phản hồi NCC** — khi đồng bộ sẽ đọc cả Inbox và thư mục có cờ IMAP `\\Junk`
  (fallback theo tên Junk/Spam), gộp các phản hồi tìm được và hiển thị tổng số
  thư Inbox/Thư rác theo đúng bộ đếm ngày địa phương của menu Thống kê email;
  không tính thư Đã gửi. Nút **Seen all** nhóm UID theo mailbox nguồn để cập
  nhật đúng cả Inbox và Thư rác.
  Sau mỗi lần **Kiểm tra**, kết quả được lưu theo ngày vào
  `data/mail_statistics_cache.json` và tự hiện lại khi mở trang; nút **Xóa cache
  ngày đã chọn** chỉ xóa ngày đang chọn. Cache không chứa password hay body thư.

Trong khu vực **Browser Blocking** của **Check Domain** và **Quick Report** có
thêm nút mở form báo cáo của **Chống Lừa Đảo** và **Cốc Cốc Safe**. Các nút chỉ
mở trang chính thức ở tab mới để người vận hành tự điền/xác nhận; công cụ không
tự gửi dữ liệu sang hai dịch vụ này.

Web UI gọi thẳng cùng các hàm trong `phishing_toolkit.py` mà CLI dùng (không viết
lại logic riêng), nên kết quả giữa CLI và web luôn khớp nhau.

## Quy trình đầy đủ

Xem `plan_phishing_takedown.md` — quy trình 8 bước từ phát hiện, xác minh,
thu thập bằng chứng, báo cáo theo đúng thứ tự ưu tiên, tới theo dõi kết quả.

## Cấu trúc file

```
phishing_toolkit.py       - Tool chính (check / related / brandscan)
cloaking_detector.py      - Detector HTTP đa profile + xác minh Playwright thụ động
cloaking_ui.py            - Khối hiển thị kết quả cloaking dùng chung cho Streamlit
cloaking_review_queue.py  - Hàng đợi review cloaking bền vững giữa các worker job
cloaking_review_sender.py - Chuẩn bị preview và gửi trực tiếp case Cloaking Review
domain_worker.py          - Precheck email/cloaking và worker batch cho domain thường
domain_check.py           - Bản đơn giản chỉ check SSL + WHOIS (không cần API key)
streamlit_app.py           - Trang chủ giao diện web (streamlit run streamlit_app.py)
pages/                      - Các trang còn lại của giao diện web (multipage app)
config.example.ini        - Template cấu hình, copy thành config.ini
plan_phishing_takedown.md - Quy trình làm việc chi tiết
case_log.csv               - Tự sinh ra sau khi chạy check lần đầu
reports/                   - Tự sinh ra, chứa email báo cáo đã điền sẵn
```

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

1. Chọn tài khoản gửi, nhập danh sách full URL và bấm **Check toàn bộ, lọc email
   & cloaking**. Với từng URL, lookup email và detector cloaking chạy đồng thời.
   Cache trong ngày chỉ áp dụng cho email; cloaking luôn được kiểm tra mới theo
   đúng full URL/path. Pha này không gửi email.
2. Ngay khi một URL có verdict `LIKELY`, `POSSIBLE`, `INCONCLUSIVE` hoặc thiếu
   vantage **và có ít nhất một email nhận**, case được ghi vào **Cloaking
   Review** và hiện trong bảng “Cloaking tách riêng”; không cần đợi hết danh
   sách precheck. Case nghi ngờ cloaking nhưng không tìm được email được ghi vào
   nhóm bỏ qua trong ngày, không tạo queue review. Case đã tách không nằm trong
   danh sách gửi tự động của Domain Worker.
3. Khi precheck hoàn tất, chỉ danh sách domain thường có email mới có thể chạy
   **Domain Worker**. Worker vẫn kiểm tra lại trong pipeline đầy đủ để cách ly
   một case nếu website thay đổi sau precheck.
4. Có thể mở **Cloaking Review** ngay trong lúc precheck hoặc Domain Worker thường
   đang chạy. Chọn một case, chọn chế độ/tài khoản, bấm **Tạo / cập nhật draft để
   xem**, đọc đúng nội dung sẽ gửi rồi xác nhận gửi trực tiếp. Trang không tạo hay
   chờ worker job gửi mail.

Worker chạy bằng process riêng nên vẫn tiếp tục nếu đóng hoặc refresh tab trình
duyệt. Trang này hiển thị tiến độ, kết quả gửi của từng domain và có nút dừng hẳn
process worker. Mỗi email thành công được ghi ngay vào `sent_log.csv`; job và danh
sách mới tự bỏ qua delivery đã gửi thành công trong ngày hiện tại.
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
  **Không phải cloaking**, tool loại toàn bộ khối evidence và attachment cloaking
  trước cả preview; email sau đó là report phishing thông thường. Hash/size của
  attachment được khóa theo preview; file bị đổi trước lúc bấm gửi sẽ bị chặn.
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
