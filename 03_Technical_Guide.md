# CẨM NANG VẬN HÀNH KỸ THUẬT SOC (TECHNICAL OPERATIONS GUIDE)
## HƯỚNG DẪN CHI TIẾT CẤU HÌNH, SCRIPTS VÀ BIỂU MẪU CHO 9 PHƯƠNG ÁN

---

### I. PHA 1: THU THẬP CHỨNG CỨ & CHẶN NHANH ĐẦU CUỐI (TỐC ĐỘ < 1 GIỜ)

#### 1. Chặn trình duyệt người dùng (Safe Browsing & SmartScreen)
*   **Google Safe Browsing Form**: dùng nút **Mở Google Safe Browsing** trong
    Quick Report (hoặc [Báo cáo Google](https://safebrowsing.google.com/safebrowsing/report_phish/)).
*   **Microsoft SmartScreen Form**: dùng nút **Mở Microsoft SmartScreen** trong
    Quick Report (hoặc [Báo cáo Microsoft](https://www.microsoft.com/wdsi/support/report-unsafe-site-guest)).
    Hai nút mở form chính thức kèm query `url` của URL đang báo cáo để người vận
    hành kiểm tra, bổ sung nội dung và tự xác nhận; tự điền bằng Playwright tại
    Quick Report đang tạm ẩn.
*   **Kênh cộng đồng Việt Nam**: Mở form [Chống Lừa Đảo](https://chongluadao.vn/report/reportphishing) và [Cốc Cốc Safe](https://safe.coccoc.com/) bằng các nút trong **Browser Blocking** của **Check Domain** hoặc **Quick Report**. Đây là bước thủ công; kiểm tra lại URL/bằng chứng trên form trước khi gửi.
*   **Nội dung mẫu gửi báo cáo (tiếng Anh)**:
    ```
    The domain [DOMAIN_GIẢ_MẠO] is actively cloning our official website login panel to harvest customer passwords and OTP tokens. Please add this phishing URL to your browser security filters to safeguard users.
    ```

#### 6. Thu hồi chứng chỉ SSL (SSL Certificate Revocation từ các tổ chức CA)
*   **Kỹ thuật xác định Certificate Authority (CA) phát hành**:
    ```bash
    # Lệnh CLI trích xuất Issuer (đơn vị cấp phát) của chứng chỉ
    openssl s_client -connect brand-scam-login.xyz:443 -showcerts | openssl x509 -noout -issuer
    
    # Lệnh lấy số Serial Number của chứng chỉ phục vụ viết đơn tố cáo
    openssl s_client -connect brand-scam-login.xyz:443 -showcerts | openssl x509 -noout -serial
    ```
*   **Danh sách Cổng báo cáo của các CA phổ biến**:
    *   **Let's Encrypt**: [Báo cáo Let's Encrypt Abuse](https://letsencrypt.org/repository/) hoặc gửi email trực tiếp tới `abuse@letsencrypt.org`.
    *   **ZeroSSL**: Gửi email báo cáo abuse tới `abuse@zerossl.com` hoặc điền biểu mẫu tại `zerossl.com/terms/`.
    *   **Google Trust Services (GTS)**: Điền biểu mẫu tại [Google GTS Report Abuse](https://pki.goog/report-abuse/).
    *   **Sectigo / Comodo**: Điền biểu mẫu tại [Sectigo Report Abuse](https://sectigo.com/support-resources/report-abuse-ssl-certificate).
    *   **DigiCert**: Gửi email tới `abuse@digicert.com` hoặc điền biểu mẫu tại [DigiCert Support](https://www.digicert.com/reporting-abuse/).
    *   **GoDaddy**: Gửi email tới `abuse@godaddy.com` hoặc điền biểu mẫu tại [GoDaddy Verification](https://sg.godaddy.com/help/report-abuse-24108).
*   **Mẫu email yêu cầu thu hồi SSL**:
    ```
    Subject: URGENT: SSL Certificate Revocation Request for Phishing Domain - [DOMAIN_GIẢ_MẠO]
    Dear CA Security Team,
    We request the immediate revocation of the SSL certificate issued for the domain [DOMAIN_GIẢ_MẠO] (Serial Number: [SERIAL_NUMBER]). This domain is actively committing brand phishing and fraud against our customers. Evidence is attached.
    ```

#### 7. Đưa vào Cổng Bảo mật & Antivirus Feed Cộng đồng (VirusTotal, PhishTank, OpenPhish)
*   **VirusTotal API Command**:
    ```bash
    # Gửi URL độc hại qua API v3 kèm API Key của SOC
    curl --request POST \
      --url https://www.virustotal.com/api/v3/urls \
      --header "x-apikey: <YOUR_VT_API_KEY>" \
      --form url=https://brand-scam-login.xyz
    ```
*   **Báo cáo PhishTank**: [phishtank.org](https://phishtank.org/)
*   **Báo cáo OpenPhish**: Gửi email chứa URL vi phạm tới `submit@openphish.com`.

---

### II. PHA 2: TRIỆT HẠ HẠ TẦNG TRUNG GIAN & IP GỐC (TỐC ĐỘ 2 - 12 GIỜ)

#### 2. Triệt hạ qua CDN Proxy (Cloudflare, Fastly, Akamai, AWS Cloudfront, Stormwall, DDOS-Guard)
*   **Danh sách cổng lạm dụng của các nhà cung cấp CDN**:
    *   **Cloudflare**: Báo cáo tại [abuse.cloudflare.com](https://abuse.cloudflare.com/) (Chọn "Phishing & Malware").
    *   **Fastly**: Báo cáo tại [fastly.com/abuse](https://www.fastly.com/abuse) hoặc gửi email tới `abuse@fastly.com`.
    *   **Akamai**: Gửi email tới `abuse@akamai.com` hoặc báo cáo tại [Akamai Compliance](https://www.akamai.com/legal/compliance/report-abuse).
    *   **AWS Cloudfront**: Báo cáo tại [Amazon AWS Abuse](https://aws.amazon.com/premiumsupport/knowledge-center/report-cloudfront-abuse/) hoặc gửi email tới `abuse@amazonaws.com`.
    *   **Stormwall**: Gửi email tới `abuse@stormwall.pro` hoặc báo cáo qua live chat của họ.
    *   **DDOS-Guard**: Gửi email tới `abuse@ddos-guard.net` or báo cáo tại [DDOS-Guard Abuse](https://ddos-guard.net/en/abuse).
*   **Kịch bản quét subdomain tìm IP gốc và bypass CDN**:
    ```bash
    # Phân giải nhanh dải subdomain thông dụng
    for sub in mail cpanel direct ftp dev staging webmail secure panel; do dig $sub.brand-scam-login.xyz +short; done
    
    # Tra cứu lịch sử phân giải DNS (DNS History API) để tìm IP trước khi cắm Cloudflare
    curl -s "https://api.securitytrails.com/v1/history/brand-scam-login.xyz/dns/a" -H "apikey: <YOUR_API_KEY>" | grep -E -o "([0-9]{1,3}\.){3}[0-9]{1,3}"
    ```

#### 3. Khóa máy chủ Hosting IP gốc (DMCA / AUP Takedown)
*   **Lệnh CLI WHOIS xác định abuse email của ISP IP gốc**:
    ```bash
    # Quét dải IP và lọc lấy tổ chức (Org) cũng như email nhận phản ánh lạm dụng
    whois <IP_GỐC> | grep -E -i "OrgName|abuse-mailbox|abuse-email|Comment"
    ```
*   **Mẫu thư DMCA tắt máy chủ gửi Hosting Provider**:
    ```
    Subject: URGENT: Phishing Site Takedown Request - [URL_GIẢ_MẠO] - IP: [IP_GỐC]
    Dear Abuse Team,
    The website at [URL_GIẢ_MẠO] hosted on your IP [IP_GỐC] is an unauthorized clone of our brand. Please suspend this hosting instance immediately.
    ```

---

### III. PHA 3: KHÓA TÊN MIỀN & LEO THANG (TỐC ĐỘ 12 - 48 GIỜ)

#### 4. Khóa tên miền tại Registrar (ClientHold Status)
*   **Giải thích kỹ thuật**: Trạng thái `ClientHold` được thiết lập bởi Registrar sẽ dừng việc phân giải DNS của tên miền tại các máy chủ tên miền gốc (Root Nameservers).
*   **Lệnh lọc email abuse của Registrar**:
    ```bash
    whois brand-scam-login.xyz | grep -E -i "Registrar Abuse Contact Email|Abuse Email"
    ```
*   **Mẫu email gửi Registrar**:
    ```
    Subject: URGENT: Phishing Domain Suspension - [DOMAIN_GIẢ_MẠO]
    Dear Abuse Department,
    Please place the domain [DOMAIN_GIẢ_MẠO] on ClientHold status due to brand phishing and credential harvesting.
    ```

#### 5. Leo thang khóa tên miền tại Registry tối cao & Hướng dẫn tra cứu/xử lý mọi đuôi ccTLD lạ cho IT
Đối với các tên miền quốc gia (ccTLD như .cn, .in, .io, .jp, .kr, .cc, .tw...), quy trình UDRP của ICANN không được áp dụng hoàn toàn. IT cần áp dụng Quy trình Truy vết & Khiếu nại Registry trực tiếp sau:

##### BƯỚC 1: TRA CỨU ĐƠN VỊ QUẢN LÝ TỐI CAO (REGISTRY OPERATOR) CỦA ĐUÔI TÊN MIỀN
Khi gặp một đuôi ccTLD lạ, IT truy cập trực tiếp cơ sở dữ liệu gốc của IANA để tìm thông tin Registry:
*   **Cơ sở dữ liệu IANA Root Zone**: [iana.org/domains/root/db](https://www.iana.org/domains/root/db)
*   **Lệnh CLI truy vấn nhanh máy chủ WHOIS gốc**:
    ```bash
    # Ví dụ truy vấn đuôi .jp để tìm máy chủ WHOIS của Registry Nhật Bản
    whois -h whois.iana.org .jp | grep -E "refer|whois"
    ```
    *Đầu ra sẽ trả về máy chủ WHOIS của Registry sở tại (ví dụ: `whois.jprs.jp`).*

##### BƯỚC 2: TRUY VẤN MÁY CHỦ WHOIS CỦA REGISTRY ĐỂ LẤY THÔNG TIN LIÊN HỆ LẠM DỤNG (ABUSE CONTACT)
*   Sử dụng máy chủ WHOIS vừa tìm được để quét sâu thông tin tên miền:
    ```bash
    whois -h whois.jprs.jp brand-scam-login.jp
    ```
    *Lọc email abuse hoặc biểu mẫu khai báo abuse được hiển thị.*

##### BƯỚC 3: DANH SÁCH THÔNG TIN & YÊU CẦU CỦA CÁC ccTLD & gTLD PHỔ BIẾN
*   **Tên miền `.cn` (Trung Quốc)**: Registry CNNIC (`supervision@cnnic.cn`). Yêu cầu bản dịch nhãn hiệu tiếng Trung/Anh có công chứng.
*   **Tên miền `.in` (Ấn Độ)**: Registry NIXI (`abuse@registry.in`).
*   **Tên miền `.io` (Lãnh thổ Ấn Độ Dương/Tech)**: Identity Digital (`abuse@identity.digital`).
*   **Tên miền `.jp` (Nhật Bản)**: Registry JPRS (`info@jprs.jp`). Đòi hỏi xác thực nhãn hiệu đăng ký tại Nhật Bản.
*   **Tên miền `.kr` (Hàn Quốc)**: Registry KISA (`abuse@kisa.or.kr`). Phối hợp báo cáo qua KrCERT.
*   **Tên miền `.ru` (Nga)**: Coordination Center for TLD RU (`abuse@cctld.ru` / `ru-abuse@cctld.ru`).
*   **Tên miền `.uk` (Vương Quốc Anh)**: Nominet (`abuse@nominet.uk`).
*   **Tên miền `.eu` (Liên Minh Châu Âu)**: EURid (`abuse@eurid.eu` / `legal@eurid.eu`).
*   **Tên miền `.tw` (Đài Loan)**: TWNIC (`abuse@twnic.tw`).
*   **Tên miền `.hk` (Hồng Kông)**: HKIRC (`abuse@hkirc.hk`).
*   **Tên miền `.us` (Mỹ)**: GoDaddy Registry (`abuse@about.us`).
*   **Tên miền `.me` (Montenegro)**: doMEn (`abuse@domain.me`).
*   **Tên miền `.xyz`**: XYZ.COM LLC (`abuse@xyz.xyz`).
*   **Tên miền `.top`**: .top registry (`abuse@nic.top`).
*   **Tên miền `.club`**: GoDaddy Registry (`clubabuse@godaddy.com`).
*   **Tên miền `.co`**: GoDaddy Registry (`coabuse@godaddy.com`).

##### BƯỚC 4: LEO THANG LÊN CERT QUỐC GIA SỞ TẠI (NẾU REGISTRY KHÔNG PHẢN HỒI)
Nếu Registry của nước đó phớt lờ, gửi email trực tiếp tới tổ chức ứng cứu sự cố CERT của quốc gia quản lý tên miền đó để can thiệp hành chính:
*   Danh sách CERT các nước được tra cứu tại thư mục quốc tế: [Danh bạ CERT toàn cầu (FIRST)](https://www.first.org/members/teams/).

#### 8. Báo cáo CERT Quốc gia (VNCERT/CC)
*   **Đầu mối báo cáo**: Gửi email chứa đầy đủ log chứng cứ an ninh mạng đến [VNCERT](mailto:report@vncert.vn) đối với các tên miền nhắm vào nạn nhân trong nước.

---

### IV. KỊCH BẢN LEO THANG PHÒNG VỆ (NẾU ĐỐI TÁC KHÔNG PHẢN HỒI)

#### A. KHI MÁY CHỦ HOSTING PHỚT LỜ (Host/VPS không hợp tác)
*   **Hướng 1: Khiếu nại lên Upstream ISP / Transit Provider**
    Tra cứu đường truyền mạng thông qua lệnh traceroute để tìm nhà mạng trung chuyển (Autonomous System - AS) và gửi mail abuse trực tiếp lên mạng cấp trên (như Cogent, HE).
    ```bash
    # Traceroute kiểm tra mạng trung chuyển
    traceroute -I <IP_GỐC>
    ```
*   **Hướng 2: Blacklist toàn cầu & Định tuyến BGP**
    Khai báo IP lên Spamhaus / SpamCop để ép hạ định tuyến AS mạng của Hosting đó.
*   **Hướng 3: Chặn chặn mạng biên (ISP trong nước)**
    Phối hợp gửi báo cáo VNCERT yêu cầu các ISP lớn (Viettel, VNPT, FPT) chặn phân giải IP trên hệ thống định tuyến mạng Core.

#### B. KHI REGISTRAR PHỚT LỜ (Nhà đăng ký tên miền không khóa)
*   **Hướng 1: Leo thang lên Registry quản lý đuôi**
    Gửi ticket khiếu nại lên cơ quan Registry tối cao của đuôi tên miền (như Verisign quản lý .com) để yêu cầu gán trạng thái `ServerHold`. Trạng thái này do Registry trực tiếp gán, đè lên mọi cấu hình của Registrar.
*   **Hướng 2: Gửi khiếu nại lên ICANN Compliance**
    Khởi tạo ticket khiếu nại tại [ICANN Compliance Abuse](https://www.icann.org/compliance) với bằng chứng cụ thể chứng minh Registrar vi phạm Thỏa thuận RAA về xử lý lạm dụng.
*   **Hướng 3: Tranh chấp khẩn cấp URS qua WIPO**
    Tiến hành nộp đơn URS (Uniform Rapid Suspension) lên WIPO để tạm dừng hoạt động tên miền trong vòng 14 ngày.

#### C. KHI REGISTRY PHỚT LỜ (Đơn vị quản lý đuôi tên miền không khóa)
*   **Hướng 1: Báo cáo Cơ quan Quản lý Viễn thông Quốc gia** sở tại quản lý Registry đó (như FCC ở Mỹ, MIIT ở Trung Quốc).
*   **Hướng 2: Đẩy chặn hiển thị đầu cuối trình duyệt** qua Google Safe Browsing, SmartScreen, và các DNS an toàn cộng đồng (Quad9, Cloudflare 1.1.1.2).

---

### V. PHA 4: BIỆN PHÁP PHÁP LÝ LÂU DÀI

#### 9. Tranh chấp pháp lý ICANN (UDRP/URS)
*   **Quy trình WIPO**: Sử dụng tài liệu sở hữu trí tuệ chính thức kiện tranh chấp tên miền thông qua Diễn đàn Trọng tài hoặc WIPO để lấy lại quyền sở hữu tên miền vi phạm thương hiệu.

---

### VI. KỊCH BẢN PLAYWRIGHT TỰ ĐỘNG HÓA THU THẬP BẰNG CHỨNG (GIAI ĐOẠN 2)

Trong Domain Check Tool, các trang terminal do trình duyệt/nhà cung cấp tạo ra
(ví dụ “Không thể truy cập trang web này”, lỗi DNS hoặc cảnh báo phishing của
Cloudflare) không được dùng làm chênh lệch cloaking. Khi toàn bộ profile đều ở
trạng thái này, kết quả là `BLOCKED_OR_UNAVAILABLE`; worker tiếp tục luồng gửi
draft bình thường và không đính kèm ảnh lỗi. Đây không phải bằng chứng độc lập
rằng registrar/registry đã thu hồi domain; cần đối chiếu WHOIS Hold và Check Link
Status nếu cần kết luận takedown.

Kết quả cloaking `LIKELY`, `POSSIBLE`, `INCONCLUSIVE` hoặc thiếu vantage luôn bị
Domain Worker cách ly khỏi luồng gửi tự động. Chỉ case có ít nhất một email nhận
mới được ghi thành record JSON trong `data/cloaking_review/` để tránh tạo một
review không thể gửi. Case không email được ghi vào `excluded_no_email` và log
no-email trong ngày. Trang **Cloaking Review** là nơi duy nhất để người vận hành
xem evidence, chọn từng URL và quyết định: gửi kèm evidence cloaking, gửi report
thường sau khi loại evidence cloaking, hoặc bỏ qua.

Nút **Check toàn bộ, lọc email & cloaking** tạo preflight schema v3. Với từng
full URL, lookup recipient và HTTP detector chạy song song; cache theo ngày chỉ
áp dụng cho recipient. Khi HTTP cần xác minh, Playwright chạy ngay trong precheck.
Kết quả cần duyệt có recipient được enqueue và ghi tăng dần vào `preflight.json`
trước khi chuyển sang URL kế tiếp, nên case đầu tiên có thể được duyệt/gửi trong
lúc phần còn lại của danh sách vẫn đang precheck. Nếu recipient rỗng hoặc chỉ có
giá trị email rỗng, case không được enqueue/migrate và UI hai page không tính hay
hiển thị record legacy đó. Chỉ các mục trong `ready` mới được đưa vào job gửi
thường; mục `cloaking_review` không bao giờ được gửi bởi job đó.

Mỗi thao tác xử lý đúng một queue record. Người vận hành chọn disposition và tài
khoản gửi, sau đó bấm **Tạo / cập nhật draft để xem**. Page gọi pipeline dùng
chung để tạo draft ngay trong request nhưng chưa gửi; UI hiển thị đúng recipient,
subject và body đã cá nhân hóa cho từng tài khoản. Nút SMTP chỉ được mở sau khi
người vận hành xác nhận đã đọc nội dung đang hiển thị. Active queue ID được lưu
riêng trong session state từ callback của nút hành động trong bảng; checkbox,
uploader và các widget khác không suy lại selection theo row index nên không làm
mất case đang mở.

`cloaking_review_sender.py` gửi trực tiếp, đồng bộ bằng SMTP helper hiện có; không
tạo worker job, không launch process và không phụ thuộc trạng thái Domain Worker.
Nội dung chuyển vào SMTP chính là nội dung vừa preview. Một lock ngắn theo queue
ID ngăn hai phiên gửi cùng case đồng thời, còn kết quả từng delivery được
checkpoint ngay sau SMTP để lần retry bỏ qua email đã thành công. Trạng thái
`PENDING_REVIEW`, `PARTIAL`, `SENT`, `FAILED`, `SKIPPED` cho phép khôi phục sau
refresh/restart. Các trạng thái `QUEUED_*` và `data/cloaking_send_jobs/` chỉ được
đọc để migrate/sync job review legacy; luồng mới không tạo chúng.

Queue ID được tính từ ngày địa phương và full URL đã chuẩn hóa, không
từ worker job ID. Nhiều observation trong cùng ngày được merge vào một record;
`source_job_ids`/`observations` giữ lịch sử và result mới nhất dùng để duyệt.
Trang review gọi `list_items_for_day()` nên chỉ render đúng ngày địa phương hiện
tại. Sang ngày mới, record cũ không còn trên UI và URL phải qua check mới để có
queue ID mới; JSON lịch sử không bị xóa để vẫn audit được delivery. Legacy record
được archive có thể khôi phục sau khi migration.

Queue schema v3 giữ `required_accounts`, `deliveries`, số draft cần giao cho mỗi
tài khoản và lịch sử `send_attempts`. Một delivery được định danh theo tài khoản
SMTP, draft và email nhận; `sent` và `already_sent` đều là đã giao, còn `failed`
vẫn có thể retry. Case chỉ thành `SENT` khi mọi `required_accounts` hoàn tất;
nếu ít nhất một tài khoản hoàn tất nhưng còn tài khoản khác chưa giao, case là
`PARTIAL` và vẫn thuộc tập selectable. Trang review hiển thị recipient, sender
đã hoàn tất, sender còn chờ và ledger chi tiết. Khi mở trang, migration đọc
review job/result/event cũ để khôi phục cả lượt `already_sent_today` vốn trước
đây chỉ có trong `events.jsonl`.

Phần đầu Cloaking Review chỉ có một bảng của hôm nay, không có KPI hoặc bộ lọc
state. `ButtonColumn` hiện **Xử lý**, **Gửi tiếp** hoặc **Thử lại** cho các state
active. `SENT` hiện `✅ Gửi thành công` nhưng action cell rỗng nên không thể mở
gửi lại; `FAILED` hiện `❌ Gửi thất bại`, giữ `last_error` và vẫn cho retry.

Disposition **Xác nhận cloaking** dùng result/evidence đã duyệt trong queue làm
nguồn sự thật, kể cả khi lần tạo draft hiện tại không tái hiện được tín hiệu.
Draft tiếng Anh ghi rõ operator đã xác nhận cloaking và chỉ được gửi khi có
manifest cùng hai ảnh hợp lệ, không rỗng và không quá 10 MB mỗi file. Disposition
**Không phải cloaking** loại khối evidence lẫn mọi attachment cloaking trước khi
preview. Sau đó page yêu cầu một Browser Evidence độc lập cho report phishing
thông thường: gọi capture nguồn–đích từ DOM, fallback capture thụ động một ảnh
nguồn, hoặc nhận 1–3 ảnh upload. Nếu chưa có evidence, nút tạo draft tự chạy
capture; nút capture riêng chỉ dùng khi muốn xem/chụp lại trước. Khối Browser Evidence tiếng Anh được chèn vào
mọi draft; PNG + manifest đi cùng email. Preview lưu size + SHA256 của từng
attachment report thường; nếu file thay đổi trước SMTP, người vận hành phải kiểm
tra và tạo lại preview.

Nếu Playwright không tạo được cặp ảnh hoàn chỉnh, Cloaking Review tự hiển thị
uploader 2–4 ảnh và khóa bước tạo draft xác nhận cloaking. File được kiểm tra đủ
số lượng, signature và kích thước trước khi ghi bất kỳ artifact nào; ảnh hợp lệ
hiện thumbnail ngay. Không có thao tác lưu evidence riêng: nút **Xác nhận ảnh &
tạo draft để xem** commit ảnh/manifest rồi chuẩn bị preview trong cùng một lần
rerun. Active case được giữ bằng `queue_id`, độc lập với selection event tạm thời
của dataframe. Hai ảnh thuộc cùng URL và được operator xác nhận khác nội dung sẽ
nâng cả `NO_SIGNAL` lẫn `INCONCLUSIVE` lên tối đa `POSSIBLE`; thao tác này không
tự gửi mail. Kết quả legacy đã có `operator_evidence` cũng được chuẩn hóa khi đọc
để không yêu cầu tải lại ảnh. Khi đủ manifest + cặp ảnh, uploader được thu gọn và
preview hoạt động theo luồng gửi trực tiếp bình thường.

Worker Playwright bao gồm Desktop trực tiếp, Android/iPhone từ Google và
Googlebot Smartphone. Với case HTTP đã `LIKELY`, browser vẫn được chạy để tái
hiện bằng chứng. Hệ thống chọn tối đa hai ảnh của cặp profile có chênh lệch mạnh
nhất (title/nội dung/redirect/keyword), rồi đính kèm cặp ảnh cùng manifest sau khi
người vận hành phê duyệt. Không đính kèm toàn bộ ảnh quan sát vào email.

#### Browser Evidence dùng chung — Phase 1

`browser_evidence.py` là lõi capture Browser Evidence chính thức. Chế độ mặc
định là thụ động: module ghi một PNG và manifest JSON theo cùng evidence set,
phân biệt rõ `requested_url`, `landing_url`, redirect HTTP do máy chủ trả về,
profile trình duyệt, control DOM được chọn và destination đã resolve. Ảnh được
khóa bằng SHA-256; validator từ chối file rỗng, sai định dạng, quá 10 MB hoặc đã
thay đổi sau khi tạo manifest. Credential trong URL/error/DOM được che trước khi
lưu.

Evidence thụ động luôn có loại `dom_observed` và
`navigation_verified=false`: không click, type, submit hay tuyên bố đã xác minh
redirect từ control. Terminal page của trình duyệt/DNS/provider không được ghi
thành evidence nội dung.

Phase 2.1 bổ sung `capture_dom_destination_evidence()` ở chế độ **opt-in**
cho report phishing thường, không dùng cho detector cloaking. Hàm tìm control
Register/Login đang hiển thị, chỉ chấp nhận anchor HTTP(S) hoặc button có
`data-href` không submit, chụp `source_before_open`, rồi mở resolved URL trong
tab mới cùng BrowserContext và referrer trang nguồn để chụp
`destination_after_open`. Manifest dùng `evidence_type=dom_destination_opened`,
`navigation_verified=false`, `destination_opened=true`, lưu URL cuối, redirect
3xx, tiêu đề hai trang, DOM element và SHA-256 của đúng hai ảnh. Không click,
nhập dữ liệu, dùng credential, submit form hoặc tải file. Không có control an
toàn, DOM URL trùng trang nguồn hoặc đích là terminal thì capture thất
bại và không để lại artifact; caller phải fallback sang capture thụ động/upload.

Từ Phase 2, Provider Replies gọi lõi này thay vì tự triển khai capture DOM. Nút
capture dùng `capture_normal_report_evidence()`: ưu tiên
`dom_destination_opened` với đúng hai ảnh nguồn/đích, sau đó fallback
`dom_observed` một ảnh nguồn nếu không có destination an toàn. UI giữ nguyên
evidence set qua rerun, hiển thị requested/landing/DOM/final URL và chỉ coi ảnh
sẵn sàng khi toàn bộ PNG/manifest còn đúng hash. Reply gửi đúng thread đính kèm
toàn bộ artifact; narrative DOM-open nói rõ URL được mở trực tiếp trong tab mới,
không tuyên bố đã click. Upload thủ công là fallback duy nhất khi capture tự
động không tạo được artifact.

#### Check Domain — Phase 2.1 mở URL từ DOM

Check Domain yêu cầu Browser Evidence hợp lệ trước mọi thao tác gửi email. UI có
hai lựa chọn trong expander Bằng chứng trình duyệt: `Passive DOM` (mặc định, không
click) và `Mở URL từ DOM` (opt-in). Chế độ này gọi
`capture_dom_destination_evidence()` với browser cô lập, chụp trang nguồn rồi
mở anchor/data-href Register/Login trong tab mới cùng context và referrer; preview
requested URL, DOM href, URL đích cuối và redirect chain trước khi gửi. Không
click, nhập dữ liệu, submit form hay tải file.

Cùng evidence set sau preview được chèn bằng formatter tiếng Anh vào mọi draft và
truyền nguyên artifact cho cả gửi một draft lẫn gửi tất cả. Gate trước SMTP kiểm
tra Subject, recipient, full Reported URL, placeholder, sự tồn tại của attachment
và 1–3 ảnh + một manifest. Draft legacy còn evidence của dịch vụ scan cũ sẽ bị
loại ở ranh giới gửi; phiên bản hiện tại không có submit, retry hay attachment
scan. DOM destination phải có đúng hai ảnh và manifest phải khớp full Reported URL
hiện tại.

Formatter gửi nhà cung cấp phải biến telemetry thành một lập luận abuse dễ xử
lý: `Observed Phishing Behavior and Supporting Evidence`, control nhìn thấy,
resolved `href`, các bước tái hiện, URL đích cuối nếu đã mở, mô tả attachment và
yêu cầu điều tra/áp dụng chính sách. Không gửi raw log `Technical Evidence` làm
phần trình bày chính. Khối evidence được đặt trước chữ ký; manifest JSON chỉ là
attachment kiểm chứng hash. Capture thụ động dùng “inspect the link target” và
ghi rõ chưa click; DOM-open ghi rõ URL được mở trực tiếp trong tab cô lập. Không
tự thêm claim credential/OTP/payment nếu chưa có quan sát.

Case selector dùng `classify_evidence_case()`:

- `control_with_destination`: nêu label, resolved `href`, bước tái hiện và URL đích.
- `control_without_destination`: nêu control nhưng nói rõ không có HTTP(S) URL tĩnh;
  yêu cầu provider kiểm tra runtime/JavaScript, không tạo URL giả.
- `page_without_auth_control`: vẫn trình bày suspected phishing/unauthorized brand
  impersonation theo screenshot và yêu cầu đối chiếu branding/runtime behavior.

DOM capture chỉ lưu số lượng aggregate của field đang hiển thị, tuyệt đối không
lưu value: password, OTP, payment và identity/contact. Chỉ khi count lớn hơn 0,
formatter mới thêm “capable of collecting …” tương ứng. Đây là bằng chứng về giao
diện thu thập dữ liệu, không phải khẳng định đã có nạn nhân hoặc dữ liệu đã bị gửi.

Nếu DOM capture không tìm thấy control an toàn, DOM URL trùng nguồn hoặc gặp trang
terminal, UI fail closed và giữ nguyên fallback `Passive DOM`/upload. Check Domain
nhận 1–3 ảnh PNG/JPEG thủ công, hiện thumbnail và commit ngay thành evidence set
gồm ảnh + manifest SHA-256; không có nút lưu riêng. Quality gate xác thực toàn bộ
hash trước SMTP. Domain Worker cho domain thường cũng dùng cùng chiến lược: thử mở
destination HTTP(S) từ DOM để chụp nguồn/đích rồi mới fallback capture thụ động; detector
cloaking vẫn giữ Playwright đa profile thụ động riêng.

Domain Worker preflight schema v4 chạy lookup email và detector cloaking song song, sau đó
capture Browser Evidence cho domain thường có email. Capture ưu tiên
`capture_dom_destination_evidence()` (hai ảnh `source_before_open` và
`destination_after_open` trong tab mới cùng context/referrer). Khi control không có URL tĩnh
hoặc destination không mở được, worker dùng `capture_passive_browser_evidence()` để giữ
ảnh trang nguồn. Chỉ khi cả hai capture không tạo được artifact hợp lệ mới ghi vào
`evidence_review`.

Page **Domain Evidence Review** đọc tất cả preflight v4 của các job chính, lọc theo ngày địa
phương và dedupe theo full URL chuẩn hóa; case chỉ xuất hiện một lần dù bị check ở nhiều job.
Domain Worker chỉ hiển thị số lượng/link, không còn uploader inline. Operator chọn case, xem
thumbnail và upload 1–3 ảnh thủ công rồi bấm **Tạo / cập nhật draft để xem**. Pipeline được
chạy một lần ở chế độ dry-run, hiển thị đúng To/Subject/body đã personalize theo từng
account + recipient và chưa gọi SMTP. Khi xác nhận, hệ thống kiểm tra fingerprint manifest,
ảnh và draft; chỉ delivery plan đã preview mới được gửi. Ledger hiển thị rõ từng delivery,
giữ evidence + trạng thái khi lỗi và retry chỉ các account/recipient còn thiếu; case gửi hoàn
tất bị loại khỏi danh sách. Không tạo review job mới; thao tác vẫn chạy độc lập khi worker
đang prechecking/running/waiting. Khi một URL đã gửi thành công, các bản ghi chờ trùng URL
trong job khác cũng được đánh dấu hoàn tất để không xuất hiện lại sau khi bản ghi mới nhất bị loại.

Nếu capture trả về terminal source (trình duyệt/DNS/provider warning), worker không đưa ảnh lỗi
vào manual review và vẫn cho draft thường tiếp tục; trạng thái terminal không tự khẳng định domain đã bị thu hồi.
Mẫu GSB và Cloudflare nhận full URL/path, mô tả suspected phishing/brand
impersonation và yêu cầu điều tra; không tự tạo tuyên bố về OTP/payment collection
nếu pipeline không có bằng chứng trực tiếp cho hành vi đó.
Hai generator giữ pool 5 biến thể riêng và tiếp tục dùng `_draft_rng(domain +
UTC date)`: cùng domain trong ngày không đổi text khi Streamlit rerun, còn domain
hoặc ngày khác có thể đổi biến thể. Mọi biến thể GSB nhắm tới browser warning;
mọi biến thể Cloudflare nhắm tới service/origin-provider abuse handling.

Registrar và registry cũng dùng formatter dựa trên dữ kiện: luôn giữ full URL/
path, không đưa kết quả scan không được xác minh hoặc VirusTotal không có detection ra ngoài, và không tự
khẳng định có credential/OTP/payment collection. Registrar được yêu cầu điều tra
rồi áp dụng biện pháp registrar-level phù hợp. Registry được yêu cầu điều tra và
phối hợp sponsoring registrar; draft chỉ được nói đã báo registrar khi caller có
delivery state xác nhận (`registrar_reported=True`, kèm ngày nếu có). Không chèn
raw WHOIS hoặc câu ICANN/ClientHold chung cho mọi ccTLD vào nội dung gửi.

#### Vận chuyển SMTP cho evidence

Mỗi SMTP account giữ port và chế độ bảo mật riêng. Port 465 dùng implicit TLS;
port 587 và port tùy chỉnh mặc định dùng STARTTLS. Chỉ đặt `starttls=false` cho
máy chủ SMTP nội bộ đã xác nhận không hỗ trợ TLS. Email thường có timeout 30
giây, còn email kèm manifest/ảnh cloaking có timeout 60 giây. Lỗi kết nối tạm
thời được mở kết nối mới và retry tối đa một lần với cùng Message-ID; lỗi xác
thực `535`, sender hoặc recipient không retry. Không kiểm tra thay đổi này bằng
SMTP thật trong test tự động; dùng mock để tránh gửi báo cáo ngoài ý muốn.

## Thống kê email hằng ngày

Menu **Thống kê email** chỉ phục vụ một việc: xem tổng mail nhận của đúng một
tài khoản trong một ngày địa phương (Inbox, Thư rác và tổng hai thư mục). Mặc
định là hôm nay. Nút **Kiểm tra mail nhận** tạo job nền bền vững; có thể chuyển
menu/F5, rồi quay lại để nạp cache `data/mail_statistics_cache.json`.

Collector dùng `INTERNALDATE`, mở rộng IMAP SEARCH rồi đổi sang múi giờ địa
phương trước khi lọc chính xác. Job này chỉ mở Inbox và Junk/Spam, không mở
Sent. Không tải body, không đổi cờ `Seen`, không gửi email và không lưu
credential. Account không có `imap_host` vẫn hiện rõ trạng thái **Không có
trong IMAP**, không bao giờ thử dùng SMTP host thay cho IMAP.

## Thống kê tổng quát theo account/khoảng ngày

Menu **Thống kê tổng quát** thay cho menu Sent Mail Evidence riêng. Người vận
hành chọn **một account** và một khoảng ngày, sau đó bấm **Đồng bộ & tính thống
kê**. Chỉ lúc đó tool mới đọc IMAP theo ba lớp độc lập:

1. Đếm Inbox, Sent và Thư rác trong cùng một kết nối, theo `INTERNALDATE` và
   khoảng ngày địa phương chính xác.
2. Đọc thư Sent trong bộ nhớ để lập index Message-ID, URL, Subject và metadata
   attachment evidence. Cache `data/sent_mail_evidence_cache.json` chỉ có
   metadata đã sanitize; không lưu body, credential hoặc bytes ảnh. Một thư
   Sent quan sát được không tự trở thành report: chỉ record khớp delivery rõ
   ràng trong `sent_log.csv` mới được tính vào hiệu quả/tỷ lệ, còn record chưa
   khớp chỉ có thể enrich evidence của delivery đó.
3. Đọc Inbox + Junk/Spam cùng phạm vi để ghép phản hồi NCC với report. Luồng
   **Phản hồi NCC** không bị thay đổi: page đó vẫn dành cho lọc, xem và phản hồi
   từng email. Thống kê tổng quát chỉ lấy email từ NCC đã nhận diện kèm domain/
   ticket, yêu cầu/kết quả rõ ràng hoặc delivery failure làm dữ liệu analytics; mail thường
   vẫn ở Provider Replies nhưng không làm sai tỷ lệ. Tool chỉ đọc/phân tích,
   không gửi reply.

`report_statistics.py` ghép delivery với reply ưu tiên Message-ID/ticket, rồi
full domain + provider/sender trong thời gian hợp lệ; một reply chỉ dùng một
lần. Domain trùng một mình không đủ để ghép. UI
hiển thị Inbox/Sent/Junk, tỷ lệ thư rác, report/gửi thành công, phản hồi,
takedown và tỷ lệ report có ảnh; cùng bảng evidence, kênh, provider/recipient,
subject/draft, outcome và Sent Mail → Provider Replies. Snapshot đã sanitize
được lưu tại `data/general_statistics_cache.json` theo account + khoảng ngày,
để mở lại không phải đồng bộ lần nữa. Lỗi một lớp (ví dụ Sent hoặc Junk) tạo
snapshot `partial` và giữ số liệu các lớp còn lại thay vì làm mất toàn bộ kết
quả. Snapshot chỉ giữ các aggregate/table field đã whitelist và lỗi đã redact,
không giữ body, credential, bytes ảnh hay raw IMAP exception. Delivery legacy
thiếu metadata evidence luôn là **unknown**, không suy đoán có hay không có ảnh.

Chạy script Python Playwright dưới đây để tự động hóa việc chụp ảnh toàn trang, trích xuất HTML nguồn, HAR log mạng và tính mã băm SHA256 để gửi báo cáo lạm dụng:

```python
import os, hashlib
from playwright.sync_api import sync_playwright

TARGET_URL = "https://brand-scam-login.xyz"
OUTPUT_DIR = "C:/Users/thang/.gemini/antigravity/scratch/domain-brand-protection-playbook/artifacts"
os.makedirs(OUTPUT_DIR, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(record_har_path=os.path.join(OUTPUT_DIR, "network.har"))
    page = ctx.new_page()
    page.goto(TARGET_URL, wait_until="networkidle")
    
    # Chụp ảnh toàn trang
    page.screenshot(path=os.path.join(OUTPUT_DIR, "screenshot.png"), full_page=True)
    
    # Lưu mã nguồn HTML
    with open(os.path.join(OUTPUT_DIR, "source.html"), "w", encoding="utf-8") as f:
        f.write(page.content())
    browser.close()

# Tính toán SHA256 bảo vệ tính toàn vẹn chứng cứ
sha = hashlib.sha256()
with open(os.path.join(OUTPUT_DIR, "screenshot.png"), "rb") as f:
    for chunk in iter(lambda: f.read(4096), b""): sha.update(chunk)
print(f"[+] SHA256: {sha.hexdigest()}")
```
