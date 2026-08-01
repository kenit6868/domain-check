# CẨM NANG VẬN HÀNH KỸ THUẬT SOC (TECHNICAL OPERATIONS GUIDE)
## HƯỚNG DẪN CHI TIẾT CẤU HÌNH, SCRIPTS VÀ BIỂU MẪU CHO 9 PHƯƠNG ÁN

---

### I. PHA 1: THU THẬP CHỨNG CỨ & CHẶN NHANH ĐẦU CUỐI (TỐC ĐỘ < 1 GIỜ)

#### 1. Chặn trình duyệt người dùng (Safe Browsing & SmartScreen)
*   **Google Safe Browsing Form**: [Báo cáo Google](https://safebrowsing.google.com/safebrowsing/report_phish/)
*   **Microsoft SmartScreen Form**: [Báo cáo Microsoft](https://www.microsoft.com/wdsi/support/report-unsafe-site)
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
