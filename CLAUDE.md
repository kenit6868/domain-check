# CLAUDE.md

File này cung cấp hướng dẫn cho Claude Code (claude.ai/code) khi làm việc với code trong repo này.

## Mục đích dự án

Bộ công cụ nội bộ nhỏ (team bảo mật/bảo vệ thương hiệu gồm 2 người) dùng để phát hiện, xác minh và
báo cáo các domain phishing giả mạo thương hiệu công ty. Có 2 giao diện dùng chung 1 lớp logic: một
bộ script CLI Python độc lập (`phishing_toolkit.py`, `domain_check.py`) và một giao diện web
Streamlit multipage chạy local (`streamlit_app.py` + `pages/`) cho cùng team 2 người đó. Cả hai đều
không phải dịch vụ hosted — đều chạy local, thao tác thủ công. `plan_phishing_takedown.md` là quy
trình vận hành viết tay mà cả 2 giao diện đều hỗ trợ.

## Lệnh chạy

Không có build step, không có test suite (ngoài các script `streamlit.testing.v1.AppTest` chạy tay
để verify UI — xem bên dưới), không cấu hình linter trong repo này.

```bash
# Setup
pip install -r requirements.txt
cp config.example.ini config.ini   # sau đó điền vt_api_key / gsb_api_key / brand_name / thông tin liên hệ

# Chạy — CLI
python3 phishing_toolkit.py check <domain>              # điều tra đầy đủ 1 domain
python3 phishing_toolkit.py related "<từ khóa thương hiệu>"   # tìm domain anh em qua crt.sh
python3 phishing_toolkit.py brandscan <domain-that>      # quét typosquat bằng dnstwist
python3 domain_check.py <domain>                         # bản nhẹ, không cần API key

# Chạy — giao diện web
streamlit run streamlit_app.py    # mở http://localhost:8501
```

Trên Windows, `pip install` đôi khi đặt console script (`streamlit.exe`, `dnstwist.exe`) vào thư mục
Scripts không nằm trong PATH, gây lỗi "not recognized" dù package đã cài đúng — dùng
`python -m streamlit run streamlit_app.py` (hoặc `python -m dnstwist ...`) làm phương án dự phòng
không phụ thuộc PATH. Hàm `brand_scan()` của `phishing_toolkit.py` đã tự gọi dnstwist theo cách này
(xem bên dưới) vì cùng lý do.

Không có test suite tự động cho CLI. Khi sửa 1 hàm, verify thủ công bằng cách chạy subcommand liên
quan với 1 domain thật hoặc domain biết chắc an toàn (vd. `google.com`), và kiểm tra `case_log.csv`
cùng `reports/*.txt` được ghi đúng. Với các trang Streamlit, `streamlit.testing.v1.AppTest` có thể
điều khiển 1 trang ở chế độ headless (`AppTest.from_file("pages/1_Check_Domain.py")`, `.run()`, set
giá trị widget, `.click()`, `.run()` lại, rồi kiểm tra `at.exception`/`at.get(...)`) mà không cần
trình duyệt — dùng để verify cả 6 trang render được và kết quả submit form khớp với output CLI cho
cùng 1 domain.

## Kiến trúc

`phishing_toolkit.py` là công cụ chính: 1 file argparse CLI duy nhất với 3 subcommand (`check`,
`related`, `brandscan`), mỗi cái được hậu thuẫn bởi các hàm cấp module thay vì class. Các phần chính:

- **Config** (`load_config`): đọc `config.ini` (đã gitignore — chứa API key) bằng
  `cfg.read(CONFIG_PATH, encoding="utf-8")`, fallback sang biến môi trường `VT_API_KEY`/`GSB_API_KEY`,
  fallback tiếp sang chuỗi placeholder nếu không có gì được set. Mọi tính năng cần API key đều degrade
  nhẹ nhàng (in "skipped") thay vì báo lỗi khi thiếu key. `encoding="utf-8"` tường minh này quan
  trọng trên Windows: `config.example.ini` có comment/giá trị tiếng Việt, và configparser nếu không
  chỉ định sẽ fallback về codepage locale của OS (cp1252), gây `UnicodeDecodeError` với bất kỳ byte
  non-ASCII nào trong file.
- **Pipeline subcommand `check` — `run_check(target, submit, cfg) -> dict`**: soi chứng chỉ SSL
  (`get_cert_info`, dùng `ssl`/`socket` thô, parse bằng `cryptography`) → WHOIS (`get_whois_info`, qua
  `python-whois`) → phát hiện Cloudflare (`is_cloudflare`, check nameserver có `cloudflare.com`
  không) → tra domain trên VirusTotal (`check_virustotal`, VT API v3) → tra Google Safe Browsing
  (`check_safebrowsing`, v4 `threatMatches:find` — **chỉ tra cứu, không có API công khai để submit
  report**) → ghi 1 dòng vào `case_log.csv` (`log_case`) → sinh sẵn email báo cáo abuse vào `reports/`
  (`generate_email_drafts`, cộng thêm `generate_openphish_draft` — xem bên dưới). `run_check` là
  **nguồn logic duy nhất** cho pipeline này: `cmd_check` (CLI) và mọi trang Streamlit chạy check đều
  gọi thẳng hàm này, chỉ khác nhau ở cách hiển thị dict trả về — không ai viết lại pipeline, nên CLI
  và web UI không bao giờ lệch kết quả. Các key trong dict trả về: `domain, cert, whois, cloudflare,
  virustotal, virustotal_submit, safebrowsing, reputation, ca_note, log_row, log_error, drafts,
  drafts_error`. `log_case` và `generate_email_drafts`/`generate_openphish_draft` được gọi trong
  `try/except` để bắt lỗi vào `log_error`/`drafts_error` thay vì để exception văng ra ngoài — điều
  này cần thiết sau khi phát hiện lúc test với `case_log.csv` đang mở trong Excel (Windows khóa file):
  trước khi sửa, file log bị khóa sẽ làm *toàn bộ* hàm raise exception trước khi kịp return, âm thầm
  làm mất kết quả SSL/WHOIS/VirusTotal/Safe Browsing đã lấy được, ở cả CLI lẫn web UI. Không được bỏ
  try/except này khi sửa `run_check` — một trục trặc I/O ở file log/draft không bao giờ được phép phá
  hỏng 1 cuộc điều tra đã hoàn thành.
- **Feed cộng đồng (VirusTotal/PhishTank/OpenPhish)** — `03_Technical_Guide.md` Phần 1 mục 7. Cố ý
  **không** xử lý đồng nhất 3 kênh này, vì chỉ 1 trong 3 có thể tự động hóa được:
  - **VirusTotal** đã có sẵn `check_virustotal`/`submit_virustotal`/`virustotal_submit` từ trước tính
    năng này — không đổi gì, chỉ đưa lên vị trí hiển thị nổi bật hơn trong mục kênh báo cáo (xem
    bên dưới).
  - **PhishTank** hiện không có API submit công khai đáng tin cậy và miễn phí — xử lý y hệt Safe
    Browsing: 1 link thủ công (`https://phishtank.org/`) trong output mục kênh báo cáo, không gọi
    API, không sinh draft file. Không thêm hàm `check_phishtank`/`submit_phishtank`; nếu sau này
    PhishTank có API submit ổn định, thêm 1 hàm lookup cùng dạng với `check_virustotal` rồi nối vào
    `run_check` và `compute_reputation` — nhưng đó là thay đổi tương lai, không phải bây giờ.
  - **OpenPhish** chỉ nhận report qua email (`submit@openphish.com`), không có API — xử lý y hệt các
    draft registrar/CA: `generate_openphish_draft(domain, vt, cfg) -> str` (định nghĩa ngay sau
    `generate_email_drafts`, cố ý tách thành hàm riêng thay vì thêm nhánh vào hàm đó, để không phải
    đổi signature của `generate_email_drafts` và các nơi gọi/test đang phụ thuộc vào nó) ghi
    `reports/<domain>_openphish_report.txt` và được append vào chung list `drafts` mà `run_check` đã
    trả về sẵn — không thêm key mới vào dict, nên `pages/5_Report_Drafts.py` tự động thấy file mới vì
    nó chỉ liệt kê `*.txt` trong `REPORTS_DIR`.
  - Mục "Khuyến nghị kênh báo cáo" (`cmd_check` trong `phishing_toolkit.py`, và đoạn tương ứng trong
    `pages/1_Check_Domain.py`) gộp Safe Browsing và 3 feed cộng đồng thành 1 mục đánh số ("2. Cộng
    đồng bảo mật") nằm *giữa* Safe Browsing và CA/Registrar/Cloudflare — không xen vào giữa CA/
    Registrar, vì đó là nhóm khác (gỡ hạ tầng, không phải chặn ở tầng trình duyệt/cộng đồng). Các dòng
    in điều kiện CA/Registrar/Cloudflare vốn có được giữ nguyên, chỉ đổi số thứ tự (3/4/5 thay vì
    2/3/4). Bảng ưu tiên ở bước 4 của `plan_phishing_takedown.md` cũng được thêm 1 dòng mới ở **ưu
    tiên 1** (cùng nhóm với Safe Browsing, không phải số ưu tiên riêng) vì cùng lý do.
- **Tổng hợp uy tín** (`compute_reputation(vt, gsb) -> dict`): gộp kết quả VirusTotal và Safe Browsing
  thành 1 verdict duy nhất — `flagged` (VT malicious>0 hoặc GSB gắn cờ), `suspicious` (chỉ VT
  suspicious>0), `unknown` (cả VT và GSB đều lỗi/skip/không có dữ liệu), hoặc `clean` (đã kiểm tra,
  chưa bên nào gắn cờ). Trả về `{"verdict", "label", "reasons"}`. Đây thuần túy là **trình bày lại
  tín hiệu đã có từ bên thứ 3**, không phải 1 phán quyết mới về phishing — cố ý không nói "domain này
  là phishing" kể cả ở verdict `flagged`, và `clean`/`unknown` luôn kèm cảnh báo rõ ("chưa bị gắn cờ
  ≠ an toàn") ở cả CLI (`cmd_check`) lẫn web UI (`streamlit_app.py` quick-check,
  `pages/1_Check_Domain.py`), để nhất quán với nguyên tắc xác minh-trước-khi-report ở bước 2 của
  `plan_phishing_takedown.md`. Không ghi vào `case_log.csv` — schema CSV đã có sẵn các dòng thật trên
  đĩa với 11 cột cũ, và `csv.DictWriter` trong `log_case` không tự viết lại header, nên thêm field
  thứ 12 vào `log_row` sẽ âm thầm làm lệch cột với bất kỳ `case_log.csv` có sẵn nào. Hiển thị theo
  màu: `st.error` cho `flagged`, `st.warning` cho `suspicious`, `st.info` cho `unknown`, `st.success`
  cho `clean` (web); text thường ở CLI.
- Mục "1. Google Safe Browsing" trong khuyến nghị kênh báo cáo (`cmd_check`,
  `pages/1_Check_Domain.py`) đã thêm 1 dòng con Microsoft SmartScreen (report thủ công tại
  `microsoft.com/wdsi/support/report-unsafe-site/`, cùng nhóm chặn trình duyệt/OS, không có API
  submit — xử lý giống PhishTank: text tĩnh, không hàm/key mới) — khớp với
  `03_Technical_Guide.md` mục 1 và ưu tiên 1 của `plan_phishing_takedown.md` bước 4.
- **Nội dung mô tả mẫu cho Google Safe Browsing** (`generate_safebrowsing_report_text(domain,
  cfg) -> str`, đặt ngay sau `check_safebrowsing()`): Safe Browsing không có API submit report,
  chỉ report thủ công qua form web `https://safebrowsing.google.com/safebrowsing/report_phish/`.
  Trước đây mục "1. Google Safe Browsing" trong khuyến nghị kênh báo cáo chỉ có link tới form,
  người dùng phải tự gõ nội dung mô tả — hàm này sinh sẵn 1 đoạn tiếng Anh ngắn (domain + brand
  name điền sẵn từ `cfg['brand_name']`) để copy thẳng vào ô mô tả của form. Khác mọi
  `generate_*_draft()` khác: **KHÔNG ghi file** vào `reports/` — đây là text dán vào 1 ô textbox
  của form web, không phải nội dung email gửi qua mail client/SMTP, nên cũng KHÔNG xuất hiện
  trong danh sách gửi email của `email_send_ui.py`/`parse_draft_email()`. Chỉ trả về string,
  hiển thị trực tiếp bằng `print()` (CLI, mục "1. Google Safe Browsing") hoặc `st.code()`
  (`pages/1_Check_Domain.py`, cùng chỗ) để tận dụng nút copy-to-clipboard có sẵn khi hover.
- **Bảng chính sách abuse của CA** (`CA_ABUSE_NOTES`): dict cứng liệt kê CA nào thực sự xử lý report
  abuse vì lý do phishing/malware và CA nào chỉ xử lý mis-issuance (vd. Google Trust Services **không**
  nhận report phishing — đây là chủ ý đã xác nhận, không phải bug). `match_ca_notes` tra chuỗi issuer
  của cert vào bảng này để quyết định có nên sinh draft email report CA hay không. Đã thêm entry
  `"godaddy"` (GoDaddy có xử lý report phishing/malware khi là CA cấp chứng chỉ — khác trường hợp
  GoDaddy là registrar, đã xử lý riêng qua `generate_email_drafts()`).
- **Nhận diện CDN & dò IP gốc** (`03_Technical_Guide.md` mục 2) — 2 key mới trong dict trả về của
  `run_check`: `cdn_detected` (list) và `origin_ip_scan` (dict `{subdomain: ip hoặc None}`). Theo
  đúng nguyên tắc đã áp dụng cho `reputation`: **không ghi 2 key này vào `case_log.csv`** — cột
  `cloudflare` trong `log_row`/CSV giữ nguyên ý nghĩa/kiểu dữ liệu cũ (boolean, chỉ từ
  `is_cloudflare()`), mọi thông tin CDN chi tiết hơn chỉ nằm trong dict trả về, không đụng schema CSV
  đã có dữ liệu thật trên đĩa.
  - **`CDN_ABUSE_CONTACTS`** (đặt gần `CA_ABUSE_NOTES`): dict `report_url`/`note` cho Cloudflare,
    Fastly, Akamai, CloudFront, Stormwall, DDoS-Guard. Khác với `CA_ABUSE_NOTES`, entry `"cloudflare"`
    ở đây **chỉ** cung cấp text report_url dùng chung khi hiển thị — việc phát hiện có dùng Cloudflare
    hay không vẫn hoàn toàn do `is_cloudflare()` (tra nameserver) đảm nhiệm như cũ, không đổi.
  - **`detect_cdn(domain) -> list`**: phát hiện CDN **khác Cloudflare** (Fastly/Akamai/CloudFront/
    Stormwall/DDoS-Guard) — cố ý tách biệt và không thay thế `is_cloudflare()`, vì chỉ Cloudflare dùng
    nameserver riêng biệt, còn khách hàng Fastly/Akamai/CloudFront thường vẫn giữ nameserver của
    registrar (nameserver-only sẽ không phát hiện được các CDN này). Kết hợp 2 nguồn tín hiệu: chuỗi
    CNAME qua `dns.resolver` (khớp suffix `fastly.net`/`akamaiedge.net`/`akamai.net`/`cloudfront.net`)
    và header HTTP HEAD (`Server`/`Via`/`X-Cache`/`X-Served-By`/`X-Amz-Cf-Id`, gọi cả `http://` và
    `https://`, `verify=False` vì domain nghi vấn — `requests.packages.urllib3.disable_warnings()`
    được gọi 1 lần ở đầu file để tắt warning ồn ào từ việc này). Mỗi nguồn tín hiệu tự bắt exception
    riêng, không raise ra ngoài. Đã verify cả 2 nguồn tín hiệu hoạt động đúng trên case thật:
    `raw.githubusercontent.com` → `fastly` (qua header), `*.cloudfront.net` → `cloudfront`.
  - **`scan_common_subdomains(domain) -> dict`**: resolve wordlist cố định (`COMMON_SUBDOMAINS =
    mail/cpanel/direct/ftp/dev/staging/webmail/secure/panel`) để gợi ý IP gốc lộ ra ngoài CDN. Chạy
    **song song** qua `concurrent.futures.ThreadPoolExecutor`, dùng `dns.resolver.resolve(fqdn, "A",
    lifetime=timeout)` khi có dnspython, fallback `socket.gethostbyname` nếu không. **Lý do quan
    trọng phải chạy song song + dùng dns.resolver**: bản đầu tiên dùng `socket.gethostbyname` tuần
    tự bọc `socket.setdefaulttimeout()` — hóa ra `setdefaulttimeout()` KHÔNG áp dụng được cho
    `gethostbyname()` (giới hạn đã biết của Python: timeout đó chỉ áp dụng cho socket
    connect/send/recv, không áp dụng cho resolve DNS qua thư viện hệ thống của OS), nên quét 9
    subdomain tuần tự thực đo mất **77s** dù "timeout" được set = 3.0. Đổi sang `dns.resolver` với
    `lifetime=` sửa được việc timeout vô tác dụng, nhưng DNS trong môi trường test có độ trễ thật
    ~4-5s/query nên tuần tự vẫn mất 27-36s — đổi tiếp sang chạy song song (`ThreadPoolExecutor`,
    `max_workers=len(COMMON_SUBDOMAINS)`) đưa tổng thời gian xuống còn ~4-22s (bị chặn bởi lookup
    chậm nhất, không phải tổng cả 9 lookup cộng dồn). So sánh IP subdomain với IP chính của domain
    (`cert.get("ip")`) để tìm "candidate origin IP" được làm ở **nơi hiển thị**
    (`cmd_check`/`pages/1_Check_Domain.py`), không phải trong hàm này — hàm này không có quyền truy
    cập `cert`, giữ thuần túy chỉ resolve DNS.
  - **Hiển thị** (`cmd_check`, `pages/1_Check_Domain.py`): mục "5. Cloudflare" cũ mở rộng thành
    "5. CDN" — luôn hiện Cloudflare trước (nếu `cf` true, dùng `CDN_ABUSE_CONTACTS["cloudflare"]`),
    rồi tới từng CDN khác trong `cdn_detected`. Origin-IP candidate (subdomain có IP khác `cert["ip"]`)
    hiện ở 1 đoạn **riêng biệt** sau mục "6. Hosting/ISP" (xem bên dưới), không nằm trong danh sách
    kênh báo cáo đánh số — chỉ là gợi ý, ghi rõ "cần xác minh thêm".
  - Cả `detect_cdn`/`scan_common_subdomains` được gọi trong `run_check` bọc `try/except` riêng (cùng
    nguyên tắc với `log_case`/`generate_email_drafts`): lỗi ở bước CDN/origin-IP (domain không resolve
    được gì cả) không được làm hỏng kết quả SSL/WHOIS/VirusTotal đã lấy được trước đó.
- **Hosting/ISP takedown cho IP gốc** (`03_Technical_Guide.md` mục 3, Pha 2) — bước tiếp theo sau
  `origin_ip_scan` ở trên, dùng khi phát hiện IP gốc lộ ra ngoài CDN. Thêm 1 key mới trong dict trả về
  của `run_check`: `origin_ip_whois` (dict `{ip: {...}}`, rỗng nếu không có candidate) — **không ghi
  vào `case_log.csv`**, cùng nguyên tắc với `reputation`/`cdn_detected`/`origin_ip_scan`.
  - **`get_ip_whois(ip) -> dict`**: tra WHOIS của **địa chỉ IP** (registry ARIN/RIPE/APNIC... qua RDAP,
    thư viện `ipwhois`) — hoàn toàn khác `get_whois_info()` (domain WHOIS qua `python-whois`), không
    thay thế nhau, dễ nhầm nếu không đọc kỹ tên hàm. Dùng `ipwhois` (RDAP thuần Python qua mạng) thay
    vì shell ra lệnh `whois <IP>` như `03_Technical_Guide.md` gợi ý, vì lệnh `whois` không có sẵn mặc
    định trên Windows — sẽ lặp lại đúng vấn đề PATH đã gặp với streamlit/dnstwist trước đó (xem mục
    Commands). Bắt buộc gọi `lookup_rdap(depth=2)` chứ không phải `depth=1` mặc định — RDAP ở depth=1
    chỉ trả về entity role `"registrant"`, entity role `"abuse"` (chứa abuse email thật) chỉ xuất hiện
    khi tăng depth. `contact.email` trong response RDAP là 1 **list** dict `[{"value": "..."}]`, không
    phải string thẳng — lấy `emails[0]["value"]`. Ưu tiên tên tổ chức từ `contact.name` của entity đầu
    tiên có sẵn (người đọc được, vd "Google LLC") hơn `network.name` (thường là handle ngắn khó đọc,
    vd "GOGL") — khác thứ tự ưu tiên literal "network.name hoặc contact.name" ban đầu, đổi vì mục đích
    thực tế là điền vào dòng "Dear {org} Abuse Team" của draft, cần tên dễ đọc. Toàn bộ bọc
    `try/except`, trả `{"error": str(e)}` thay vì raise — RDAP có thể fail (IP private, không có
    record, timeout...).
  - **`generate_hosting_draft(domain, ip, ip_whois, cfg) -> path`**: sinh draft DMCA/AUP giống pattern
    `generate_email_drafts()`/`generate_openphish_draft()`, ghi `reports/<domain>_hosting_report.txt`.
    Nêu rõ trong nội dung draft rằng IP này chỉ là "candidate origin server... please verify
    independently" — không khẳng định chắc chắn, giữ nguyên tắc xác minh trước khi report.
  - **Trong `run_check`**: khối tính `origin_ip_whois`/gọi `generate_hosting_draft` đặt **sau** khối
    draft OpenPhish (không phải ngay sau `origin_ip_scan` như bản nháp yêu cầu đầu tiên) — vì cần biến
    `drafts`/`drafts_error` đã tồn tại để append/gộp lỗi vào, đặt sớm hơn sẽ `NameError`. Chỉ xử lý
    **1 IP đầu tiên** khi có nhiều candidate (`sorted(candidate_ips)[0]`, sort để kết quả ổn định giữa
    các lần chạy) — tránh spam nhiều file draft cho 1 domain trong bản đầu tiên này.
  - **Hiển thị**: mục "6. Hosting/ISP (IP gốc)" trong `cmd_check`/`pages/1_Check_Domain.py`, đặt SAU
    mục "5. CDN". Nếu có `origin_ip_whois`: IP, tổ chức, abuse email, ASN, link draft. Nếu
    `origin_ip_scan` không tìm thấy candidate nào: 1 dòng ghi chú ngắn, KHÔNG coi là lỗi (đúng yêu cầu
    phụ thuộc — tính năng này chỉ chạy được khi bước #2 CDN/origin-IP tìm ra ít nhất 1 candidate).
  - Đã verify với domain thật: `google.com` (subdomain `mail.google.com` trỏ IP khác IP chính, dùng để
    test code path "có candidate") → `get_ip_whois` trả đúng org "Google LLC", abuse email
    "network-abuse@google.com", ASN 15169; và `chass.ru.com` (không có candidate nào) → hiện đúng dòng
    "Không phát hiện IP gốc khác", không lỗi, không sinh file draft thừa.
  - **Chưa làm** (theo đúng scope của lần thêm tính năng này): traceroute/BGP/Spamhaus (Pha leo thang
    IV.A, chỉ cần khi hosting phớt lờ — chưa tới lúc), và **không** tự động gửi email — chỉ sinh draft,
    vẫn cần người xác nhận trước khi gửi.
- **Leo thang Registry cho ccTLD lạ + report VNCERT** (`03_Technical_Guide.md` mục 5 và mục 8) — key
  mới `registry_contact` trong dict trả về của `run_check` (không ghi vào `case_log.csv`, cùng nguyên
  tắc đã áp dụng nhiều lần trước).
  - **3 loại WHOIS khác nhau trong file này, đừng nhầm lẫn**: `get_whois_info()` (domain WHOIS qua
    thư viện `python-whois`, tự chọn server theo TLD nó biết sẵn) → `get_ip_whois()` (IP WHOIS qua
    RDAP/`ipwhois`, cho địa chỉ IP) → **`query_whois_server()`/`iana_referral()`/
    `lookup_registry_contact()`** (WHOIS thô qua raw socket port 43, nói chuyện trực tiếp với 1 WHOIS
    server bất kỳ) — cần loại thứ 3 này vì `python-whois` không hỗ trợ truy vấn tùy ý tới 1 server chỉ
    định, không tra được ccTLD lạ/mới ngoài danh sách nó biết.
  - **`query_whois_server(server, query, timeout=8.0) -> str`**: dùng `socket.create_connection()` thô
    (cùng phong cách với `get_cert_info()`), gửi `f"{query}\r\n"`, đọc tới khi socket đóng, decode
    `utf-8` với `errors="ignore"` (WHOIS server nước ngoài có thể trả ký tự khó decode). Không raise —
    lỗi trả về chuỗi rỗng.
  - **`CCTLD_REGISTRY_CONTACTS`** (đặt gần `CDN_ABUSE_CONTACTS`): bảng tĩnh tra TRƯỚC, không cần mạng
    — cn/in/io/jp/kr/ru/uk/eu/tw/hk/us/me/xyz/top/club/co theo đúng danh sách mục 5 bước 3, kèm `note`
    cho yêu cầu đặc biệt (vd .cn cần bản dịch nhãn hiệu công chứng, .jp cần xác thực nhãn hiệu tại
    Nhật — chỉ ghi chú vào draft, KHÔNG tự động hóa việc dịch/xác thực).
  - **`iana_referral(tld)`**: hỏi `whois.iana.org` (qua `query_whois_server`), regex parse dòng
    `whois:`/`refer:` để lấy hostname WHOIS server của registry — ưu tiên `whois:` hơn `refer:`. Trả
    `None` nếu không parse được.
  - **`lookup_registry_contact(domain) -> dict`**: tra `CCTLD_REGISTRY_CONTACTS` trước, fallback
    `iana_referral()` khi TLD không có trong bảng. Trả `{"source": "static_table", ...}` /
    `{"source": "iana_referral", "whois_server", "raw"}` / `{"source": "not_found"}`. Khác
    `get_whois_info()`: kết quả từ `iana_referral` trả **nguyên văn** (`raw`), KHÔNG parse thành field
    có cấu trúc — format WHOIS mỗi registry quốc gia rất khác nhau, không đáng viết parser riêng cho
    từng cái, con người tự đọc abuse email trong `raw`. Giới hạn: chỉ xử lý TLD 1 nhãn qua
    `rsplit(".", 1)[-1]` (vd `.jp`) — domain dạng `.co.uk`/`.com.cn` không được xử lý đặc biệt (thường
    vẫn ra kết quả đúng vì registry của các ccTLD 2 nhãn phổ biến này trùng registry của TLD 1 nhãn
    tương ứng, nhưng đây là trùng hợp, không phải cố ý hỗ trợ).
  - **Bug phát hiện khi test — PHẢI loại trừ gTLD cổ điển**: `whois.iana.org` trả về referral cho
    **mọi** TLD nó biết, kể cả gTLD như `.com`/`.net` (không chỉ ccTLD) — nếu không chặn,
    `lookup_registry_contact("example.com")` sẽ đi vào nhánh `iana_referral` thay vì `not_found`,
    khiến tool tự "leo thang Registry" cho mọi domain `.com` bình thường, sinh draft rác. Thêm
    `_SKIP_REGISTRY_ESCALATION_TLDS = {"com", "net", "org", "info", "biz", "name", "pro", "mobi"}` —
    các gTLD này đã có đầy đủ UDRP + registrar-level report (mục "4. Registrar" đã có), không cần leo
    thang Registry riêng. Đã verify: `.com`/`.net` → `not_found` (không sinh file), `.cc` (ccTLD lạ
    không có trong bảng tĩnh) → `iana_referral` với `whois_server = ccwhois.verisign-grs.com`.
  - **`generate_registry_draft(domain, registry_info, cfg) -> path | None`**: trả `None` (không sinh
    file) khi `source == "not_found"`. Nội dung khác nhau theo `source`: `static_table` điền tên
    registry/abuse email/note trực tiếp; `iana_referral` không có field cấu trúc nên đính kèm nguyên
    văn WHOIS thô vào cuối draft để người đọc tự tìm abuse email.
  - **`generate_vncert_draft(domain, cert, vt, cfg) -> path`**: LUÔN sinh (không điều kiện, giống
    `generate_openphish_draft`), vì tool không tự biết domain có nhắm vào nạn nhân Việt Nam hay không
    — dòng cảnh báo "CHỈ gửi nếu..." nằm ngay đầu file, người dùng tự quyết định trước khi gửi. Nội
    dung viết tiếng Việt (khác các draft khác viết tiếng Anh) vì VNCERT là tổ chức trong nước.
  - **Trong `run_check`**: đặt sau khối Hosting/ISP (cần `drafts`/`drafts_error`/`cert`/`vt` đã tồn
    tại). Có `try/except` bọc ngoài gán fallback `registry_info = {"source": "not_found"}` nếu bản
    thân khối này raise bất ngờ, để key `registry_contact` trong dict trả về luôn tồn tại.
  - **Hiển thị** (`cmd_check`, `pages/1_Check_Domain.py`): 2 mục mới "5. Registry (ccTLD)" và
    "6. VNCERT" chèn ngay SAU mục "4. Registrar" (đúng yêu cầu: đây là bước leo thang, đặt gần
    Registrar) — mục CDN/Hosting-ISP vốn là 5/6 bị đẩy xuống thành **7/8**. VNCERT luôn hiện link
    draft + cảnh báo "chỉ gửi nếu nhắm vào VN"; Registry hiện thông tin nếu tra được, hoặc gợi ý tra
    thủ công tại `iana.org/domains/root/db` nếu TLD chưa hỗ trợ.
  - Đã verify với domain thật `yahoo.co.jp`: tra đúng bảng tĩnh ("JPRS: info@jprs.jp" + note xác thực
    nhãn hiệu Nhật), VNCERT draft luôn sinh, và tình cờ phát hiện `direct.yahoo.co.jp` trỏ IP khác IP
    chính — đúng kịch bản "subdomain lộ IP gốc" thật ngoài đời (không phải giả lập).
  - **Chưa làm** (đúng scope): KHÔNG xử lý ccTLD 2 nhãn có register riêng biệt thật sự khác (vd trường
    hợp registry `.co.uk` khác hẳn registry `.uk` — hiếm nhưng có thể xảy ra), KHÔNG tự động dịch nhãn
    hiệu công chứng cho `.cn` (chỉ ghi chú), KHÔNG tự động gửi email tới CERT quốc gia khác VN (chỉ là
    link tham khảo `FIRST` trong `plan_phishing_takedown.md`, không code hóa).
- **Subcommand `related`**: query `crt.sh` (Certificate Transparency log, public, không cần API key)
  tìm chứng chỉ có SAN/CN chứa từ khóa, để lộ ra các domain "anh em" cùng 1 chiến dịch phishing.
- **Subcommand `brandscan`**: shell ra dnstwist qua `subprocess.run([sys.executable, "-m",
  "dnstwist", "-r", "-t", "60", "-f", "json", domain], timeout=600)` để sinh các biến thể
  gõ-nhầm/homograph của 1 domain hợp pháp và lọc ra những cái đang được đăng ký. Gọi qua
  `python -m dnstwist` thay vì lệnh `dnstwist` trần vì pip cài console script ra ngoài PATH trên 1 số
  hệ thống (đặc biệt Windows) — `-m` luôn hoạt động vì dùng chung interpreter. Số thread được tăng từ
  mặc định 16 của dnstwist lên 60 và timeout từ 180s lên 600s vì dnstwist resolve DNS cho **mọi**
  biến thể bất kể cờ `-r` (`-r` chỉ lọc *output* hiển thị), và với 1 brand phổ biến đây là hàng nghìn
  lượt tra cứu — ở mức 180s/16 thread cũ, việc timeout xảy ra thường xuyên kể cả với domain nhỏ như
  `github.io`. Cần gói `dnspython` (đã có trong `requirements.txt`) nếu không dnstwist sẽ log "DNS
  features are limited" và resolve chậm hẳn đi.
- **Side effect ghi file**: `case_log.csv` và `reports/*.txt` được tạo tương đối theo thư mục chứa
  chính script (`BASE_DIR`), không phải theo CWD, và đã được gitignore — đây là dữ liệu làm việc,
  không phải source code.
- **Xử lý "TLD giả" trong WHOIS** (`FAKE_TLD_SUFFIXES`, `whois_query_domain`): các domain như `ru.com`,
  `uk.com`, `us.com` v.v. là second-level domain được CentralNic/đối tác bán lại như thể TLD riêng —
  1 domain phishing như `chass.ru.com` thực chất là subdomain của `ru.com`, và chính `ru.com` mới là
  cái được đăng ký (có registrar/abuse contact riêng). `whois.whois()` với chuỗi 3-nhãn đầy đủ sẽ tra
  sai registry (vd. VeriSign cho `.com`) và trả về "No match". `whois_query_domain` rút gọn về 2 nhãn
  cuối trước khi gọi WHOIS khi chúng khớp 1 hậu tố TLD-giả đã biết. Pattern này rất phổ biến trong hạ
  tầng phishing thật (rẻ, WHOIS lỏng lẻo hơn ccTLD thật) — phát hiện khi test với 1 domain bị gắn cờ
  thật (`chass.ru.com`, 11/91 engine VirusTotal báo malicious). Fix tương tự đã áp dụng y hệt trong
  `domain_check.py` theo ghi chú về duplicate bên dưới.

- **Gửi báo cáo qua email thật (SMTP)** — trước đây `generate_*_draft()` chỉ SINH file `.txt`,
  người dùng phải tự copy nội dung sang client email của mình để gửi. Thêm khả năng gửi thẳng
  1 draft đã sinh sẵn qua SMTP, với gate xác nhận thủ công bắt buộc (không có đường nào tự động
  gửi hàng loạt không qua duyệt — giữ đúng nguyên tắc xác minh-trước-khi-report xuyên suốt dự án).
  - **`[smtp]` trong `config.ini`** (`host`/`port`/`username`/`password`): đọc trong
    `load_config()` cùng pattern với `vt_api_key`/`gsb_api_key` — thiếu thì tính năng gửi email
    chỉ bị disable (báo rõ ở UI/CLI), không lỗi. Với Gmail bắt buộc dùng "App Password" (Google
    chặn SMTP bằng mật khẩu thường từ 2022). `config.ini` đã gitignore sẵn nên không cần mã hóa
    riêng cho password ở bản đầu — `config.example.ini` chỉ có comment nhắc không điền thật vào
    đó.
  - **`parse_draft_email(path) -> {"to", "subject", "body"}`**: đọc 1 file draft đã sinh, tách
    "To:"/"Subject:"/body. KHÔNG giả định "To:"/"Subject:" luôn ở 2 dòng đầu file — quét toàn
    file tìm dòng "To:" đầu tiên (có thể không tồn tại, xem dưới) và "Subject:" đầu tiên, dừng
    tại đó, rồi lấy body là phần sau dòng trống đầu tiên **ngay sau dòng Subject** (không phải
    dòng trống đầu tiên của cả file) — cần thiết vì `generate_vncert_draft()` ghi 1 đoạn cảnh
    báo (có dòng trống ở giữa) đứng TRƯỚC khối header, nếu cắt ở dòng trống đầu tiên sẽ lẫn "to"/
    "subject" thật vào phần đầu "body". `to` trả về `None` (không gửi được qua email) nếu dòng
    "To:" không tồn tại — **draft CA report (`generate_email_drafts`) cố ý không có dòng "To:"**
    vì nhiều CA (vd Sectigo, DigiCert) dùng web form report abuse thay vì email, `report_url`
    trong `CA_ABUSE_NOTES` có thể là URL chứ không phải địa chỉ email — hoặc nếu giá trị "to" là
    1 placeholder cần tra cứu thủ công (chứa "[TRA ABUSE EMAIL" hoặc "[KHÔNG CÓ", vd draft
    registrar/hosting/registry khi chưa tra được abuse email cụ thể).
  - **`send_report_email(to, subject, body, cfg) -> {"success", "error"?}`**: dùng `smtplib` +
    `email.mime.text.MIMEText` (thư viện chuẩn, không cần cài thêm) — `starttls()`, `login()`,
    `sendmail()` từ `smtp_username` tới `to`. Bọc try/except toàn bộ, không raise — nơi gọi
    (Streamlit/CLI) luôn nhận dict để hiển thị/ghi log. Không tự có gate xác nhận nào — CHỈ được
    gọi sau khi người dùng đã xác nhận thủ công ở nơi gọi.
  - **`sent_log.csv`** (`SENT_LOG_PATH`, đã thêm vào `.gitignore` cùng `case_log.csv`): log
    RIÊNG cho việc gửi email, ghi qua `log_sent(row)` — KHÔNG thêm cột vào `case_log.csv`, đúng
    nguyên tắc không đổi schema CSV đã có dữ liệu thật (đã áp dụng cho `reputation`/`cdn_detected`/
    `registry_contact`...). Mỗi dòng: `timestamp, domain, draft_file, to, subject, success,
    error` — ghi cho MỌI lần bấm gửi, kể cả thất bại. `domain_from_draft_filename()` suy ngược
    domain từ tên file draft (tra theo hậu tố `_registrar_report.txt`/`_ca_report.txt`/... đã
    biết) để điền cột `domain`.
  - **UI dùng chung — `email_send_ui.py` (`render_send_email_ui(path, cfg, key_prefix)`)**: 1
    module riêng ở project root (KHÔNG phải page — không có `st.set_page_config`, không nằm
    trong `pages/`, chỉ là component được import), tách khỏi `phishing_toolkit.py` vì cần phụ
    thuộc `streamlit` (module đó cố ý không phụ thuộc streamlit, dùng chung cho cả CLI). Gọi
    `parse_draft_email()`; nếu `to` hợp lệ VÀ `[smtp]` đã cấu hình đủ: hiện checkbox "Tôi đã đọc
    và xác nhận nội dung, domain này thực sự là phishing" — nút "Gửi email này" mặc định
    `disabled=True`, chỉ hết disable khi checkbox được tick (`disabled=not confirmed`) — đây là
    gate quan trọng nhất, không được bỏ qua khi sửa hàm này. Nếu `to` không hợp lệ (draft dạng
    web-form như CA report, hoặc chưa tra được abuse email): hiện `st.info` hướng người dùng qua
    mục "Khuyến nghị kênh báo cáo" thay vì cố hiện nút gửi — file draft không lưu lại
    `report_url` gốc trong nội dung nên UI không tự suy ngược lại được URL chính xác, cố ý chấp
    nhận hạn chế này thay vì đoán sai.
    - **Dùng ở CẢ 2 nơi**: `pages/1_Check_Domain.py` (ngay trong mỗi `st.expander` của draft, ngay
      sau khi check xong — theo yêu cầu UX là gửi được luôn tại chỗ, không phải nhảy sang trang
      khác) và `pages/5_Report_Drafts.py` (khi mở lại 1 draft cũ). Bắt buộc truyền `key_prefix`
      khác nhau ("check" / "drafts") để key của checkbox/nút Streamlit không đụng nhau nếu cùng
      1 file draft được render ở nhiều nơi trong cùng 1 lần chạy.
    - Lý do tách thành hàm dùng chung thay vì copy-paste vào từng trang (khác với chủ trương
      "pages gọi thẳng hàm phishing_toolkit, không thêm wrapper" đã áp dụng cho các trang khác):
      logic gate xác nhận là phần **quan trọng nhất về an toàn** của tính năng này — copy 2 nơi
      dễ bị sửa 1 chỗ quên chỗ kia, làm gate bị yếu đi âm thầm ở 1 trang.
  - **CLI tương ứng**: `python3 phishing_toolkit.py send <đường-dẫn-draft>` (`cmd_send`) — in
    nội dung sắp gửi, `input("Gửi email này? (y/N): ")` xác nhận thủ công trước khi gọi
    `send_report_email()`, cùng nguyên tắc gate với Streamlit.
  - Đã verify bằng cách tự sinh draft mẫu (registrar có "To:", CA report không có "To:", VNCERT
    có đoạn cảnh báo trước header) rồi chạy qua `parse_draft_email()` — cả 3 dạng parse đúng
    "to"/"subject"/"body", kể cả case VNCERT có preamble. Chưa test gửi SMTP thật (cần tài khoản
    Gmail App Password thật) — người dùng cần tự verify bước gửi thật bằng cách sửa tạm "to"
    thành email cá nhân trước khi dùng với abuse contact thật.
  - **Chưa làm** (ngoài scope bản đầu): không mã hóa password SMTP trong config.ini (dựa vào
    gitignore), không rotate/nhiều tài khoản SMTP, không retry tự động khi gửi lỗi, không tự động
    suy ngược `report_url` gốc cho draft không có "To:" (CA report) — UI chỉ trỏ người dùng sang
    mục "Khuyến nghị kênh báo cáo" đã có sẵn.

`domain_check.py` là 1 script độc lập đơn giản hơn, ra đời sớm hơn (chỉ có SSL issuer/serial + WHOIS +
phát hiện Cloudflare, không cần API key, không log, không sinh email). Được giữ lại làm phương án
dự phòng không cần setup gì để check nhanh thủ công. Nó duplicate vài hàm helper từ
`phishing_toolkit.py` một cách có chủ ý (không import từ đó) — nếu sửa bug ở 1 trong 2 file (vd.
logic parse cert), phải kiểm tra xem bug tương tự có tồn tại ở file kia không.

`plan_phishing_takedown.md` là quy trình con người mà các script được xây để hỗ trợ: quy trình 8 bước
(phát hiện → xác minh → thu thập bằng chứng → báo cáo theo thứ tự ưu tiên Safe Browsing > registrar >
CDN > CA → theo dõi → ghi log → chủ động quét). Thứ tự ưu tiên báo cáo và bảng CA trong
`phishing_toolkit.py` phải luôn nhất quán với tài liệu này — nếu sửa 1 cái, phải cập nhật cái kia.

## Giao diện web Streamlit (`streamlit_app.py`, `pages/`)

Ứng dụng Streamlit multipage chạy local cho cùng team 2 người, được thêm vào như 1 lựa chọn thay thế
CLI (không phải thay thế hoàn toàn — cả 2 luôn đồng bộ vì dùng chung các hàm của
`phishing_toolkit.py`). Chạy bằng `streamlit run streamlit_app.py` (hoặc
`python -m streamlit run ...` nếu `streamlit` không nằm trong PATH).

- **`streamlit_app.py`** (trang chủ): `st.set_page_config` đặt tên/icon cho toàn app (theo convention
  của Streamlit), 1 form check nhanh gọi thẳng `pt.run_check()` và hiển thị vài `st.metric`, cộng với
  10 dòng gần nhất của `case_log.csv` qua `pandas.read_csv`.
- **`pages/1_Check_Domain.py`**: tương đương đầy đủ của CLI `check`. Gọi `pt.run_check(target,
  submit_vt, cfg)` — cùng hàm mà `cmd_check` dùng — rồi render dict bằng `st.metric` (các số VT/GSB/
  Cloudflare nổi bật), helper `show_dict()` (dict → `st.table` 2 cột qua 1 `pandas.DataFrame` tạm,
  tránh dump dict thô bằng `st.write`), cùng đoạn text kênh báo cáo theo thứ tự ưu tiên như CLI, và
  các khối `st.code` cho draft đã sinh. Hiển thị `log_error`/`drafts_error` từ `run_check` bằng
  `st.error` thay vì để crash.
- **`pages/2_Related_Domains.py`**: gọi `pt.crtsh_related(keyword)` (giống CLI `related`), render kết
  quả bằng `st.dataframe`.
- **`pages/3_Brand_Scan.py`**: gọi `pt.brand_scan(domain, limit)` (giống CLI `brandscan`). Hiện
  `st.warning` ngay từ đầu báo là brand phổ biến có thể mất tới 10 phút (khớp ngân sách timeout của
  `run_check`/`brand_scan` đã ghi ở trên) và bọc lệnh gọi trong `st.spinner`.
- **`pages/4_Case_Log.py`**: đọc `case_log.csv` bằng `pandas`, lọc theo `status` qua `st.multiselect`,
  và dùng `st.data_editor` với mọi cột trừ `status` được set `disabled=` để team chỉ sửa được đúng
  cột đó; nút "Lưu thay đổi" ghi đè lại toàn bộ dataframe (không lọc) bằng
  `df.to_csv(pt.LOG_PATH, index=False)`. Việc lọc/sửa chỉ nằm ở tầng UI — không thêm hàm mới nào vào
  `phishing_toolkit.py` cho việc này, vì `case_log.csv` là dữ liệu làm việc, không phải logic.
- **`pages/5_Report_Drafts.py`**: liệt kê file `*.txt` trong `pt.REPORTS_DIR`, hiển thị nội dung file
  được chọn bằng `st.code` (có sẵn nút copy-to-clipboard khi hover — không cần thêm dependency
  clipboard nào) cộng với 1 `st.download_button`. Bên dưới: gọi
  `email_send_ui.render_send_email_ui(path, cfg, key_prefix="drafts")` — xem mục SMTP ở trên
  (phần "UI dùng chung") để biết chi tiết gate xác nhận checkbox bắt buộc trước khi nút gửi hết
  disabled. Trang này KHÔNG còn tự viết logic gửi email inline nữa (đã refactor sang
  `email_send_ui.py` để dùng chung với `1_Check_Domain.py`).
- **`pages/1_Check_Domain.py`**: mỗi draft trong khối "Email báo cáo đã tạo sẵn" (`st.expander`)
  giờ có thêm `email_send_ui.render_send_email_ui(path, cfg, key_prefix="check")` ngay bên dưới
  nội dung draft — gửi được ngay tại chỗ sau khi check xong, không cần qua trang Report Drafts
  (UX ban đầu bắt gửi qua trang riêng, người dùng phản hồi bất tiện vì phải tự nhớ tên domain
  rồi tìm lại đúng file draft ở trang khác).
  - **Bug phát hiện khi thêm tính năng này — kết quả check biến mất khi tick checkbox xác nhận**:
    toàn bộ phần hiển thị kết quả (kể cả chính draft/checkbox/nút gửi) trước đó nằm trong
    `if go:` (`go` = giá trị của `st.form_submit_button`). Checkbox/nút gửi trong
    `render_send_email_ui()` nằm NGOÀI `st.form`, nên tick nó kích hoạt Streamlit chạy lại toàn
    bộ script; ở lần chạy lại đó `go` luôn là `False` (form không được submit lại) → cả khối
    `if go:` bị bỏ qua → toàn bộ kết quả biến mất ngay lập tức, chỉ còn lại form trống với nút
    "Kiểm tra". Fix: lưu `result`/`cfg` vào `st.session_state["check_domain_result"]`/
    `["check_domain_cfg"]` bên trong `if go:`, rồi tách phần hiển thị ra thành 1 khối riêng
    `if "check_domain_result" in st.session_state:` chạy độc lập với `go` — đọc lại được ở MỌI
    lần script chạy lại, không chỉ lần vừa submit form. Đây là pitfall kinh điển của Streamlit
    (widget ngoài form luôn trigger rerun toàn script) — bất kỳ widget mới nào thêm vào NGOÀI
    `st.form` ở trang này trong tương lai cũng phải lưu state cần giữ lại vào
    `st.session_state`, không được giả định biến cục bộ của lần chạy trước vẫn còn.
- **Pattern import**: mỗi trang đều có
  `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` trước
  `import phishing_toolkit as pt`, để trang luôn resolve được module từ project root bất kể working
  directory của Streamlit là gì. Không có lớp wrapper/service nào được thêm vào — các trang gọi thẳng
  hàm trong `phishing_toolkit`, đúng theo chỉ dẫn "giữ đơn giản" khi UI này được xây dựng.
- **Dùng `width="stretch"` thay vì `use_container_width=True`**: phiên bản Streamlit đã cài (1.59.x)
  cảnh báo `use_container_width` đã deprecated sau 2025-12-31; mọi lệnh gọi `st.dataframe`/
  `st.data_editor` trong app này dùng tham số `width="stretch"` mới hơn thay thế. Giữ đúng convention
  này khi thêm widget mới.
- **Đã verify bằng `streamlit.testing.v1.AppTest`** (không cần trình duyệt): cả 6 trang render không
  lỗi; `1_Check_Domain.py` và CLI `check` chạy cùng 1 domain thật (`chass.ru.com`) cho ra số liệu
  VirusTotal/WHOIS/CA giống hệt nhau; `2_Related_Domains.py` so với CLI `related "openai"` cũng khớp
  (35 domain cả 2 lần).

## Skill riêng của dự án (`.claude/skills/`)

Có 2 skill Claude Code đi kèm dự án này, tự động kích hoạt trong các phiên Claude Code mở tại đây:

- **`investigate-phishing-domain`** — quy trình phản ứng cho 1 domain đã bị nghi ngờ sẵn: chạy
  `check`, xác minh trước khi đề xuất report, chỉ tới các kênh báo cáo theo thứ tự ưu tiên và email
  draft đã tự sinh trong `reports/`.
- **`brand-monitor-scan`** — quét chủ động (`brandscan` + `related`) tìm các domain chưa ai report,
  có bước triage trước khi chuyển các ứng viên sang `investigate-phishing-domain`.

Cả 2 đều mã hóa nguyên tắc xác minh-trước-khi-report từ bước 2 của `plan_phishing_takedown.md` — đừng
để skill nào (hoặc 1 lần sửa skill sau này) nhảy thẳng từ "tool đã chạy" sang "đề xuất report" mà
không có con người xác nhận domain thực sự đang giả mạo thương hiệu.

## Chỉ dẫn bảo trì (quan trọng)

**Sau bất kỳ thay đổi code nào trong dự án này, cập nhật CLAUDE.md này trong cùng phiên làm việc**
(thêm/chỉnh phần liên quan ở trên) để phiên Claude tiếp theo hiểu được trạng thái hiện tại của dự án
chỉ bằng cách đọc file này, không cần đọc lại toàn bộ source. Giữ phần cập nhật ngắn gọn — mô tả cái
gì đã đổi và vì sao nó quan trọng về mặt kiến trúc, không phải diff từng dòng.
