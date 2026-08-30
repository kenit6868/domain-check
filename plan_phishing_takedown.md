# Plan: Quy trình phát hiện & báo cáo (takedown) domain phishing

## 1. Mục tiêu

Xây dựng quy trình chuẩn để phát hiện, xác minh và báo cáo các domain đang giả mạo thương hiệu/dịch vụ nhằm lừa đảo (phishing), giúp gỡ bỏ hoặc chặn cảnh báo cho các domain đó nhanh nhất có thể.

## 2. Phạm vi

Áp dụng cho các domain nghi ngờ giả mạo thương hiệu công ty, có dấu hiệu:
- Tên miền gần giống domain thật (typosquatting, thêm/bớt ký tự, đổi TLD)
- Có trang đăng nhập/form thu thập thông tin giả mạo giao diện chính thức
- Được lan truyền qua email, SMS, mạng xã hội với nội dung mạo danh

## 3. Công cụ sử dụng

| Công cụ | Mục đích |
|---|---|
| `phishing_toolkit.py check <domain>` | SSL issuer/serial, WHOIS, Cloudflare, VirusTotal, Safe Browsing check, tự ghi log CSV + sinh sẵn email báo cáo |
| `phishing_toolkit.py related <keyword>` | Tra crt.sh tìm domain "anh em" cùng chiến dịch phishing |
| `phishing_toolkit.py brandscan <domain>` | Dùng dnstwist chủ động phát hiện domain giả mạo thương hiệu trước khi có người báo cáo |
| Google Safe Browsing Report | Vẫn cần submit thủ công qua form (chưa có API report công khai) |
| WHOIS / ICANN Lookup | Xác định registrar và abuse contact (đã tích hợp trong tool) |

Cài đặt trước khi dùng:
```
pip install requests python-whois cryptography dnstwist
cp config.example.ini config.ini   # rồi điền VT/GSB API key + thông tin thương hiệu
```

## 4. Quy trình từng bước

### Bước 1 — Phát hiện domain nghi ngờ
Nguồn phát hiện: báo cáo từ người dùng/khách hàng, giám sát brand mention, kết quả từ VirusTotal/crt.sh khi rà soát domain tương tự thương hiệu.

### Bước 2 — Xác minh đây thực sự là phishing
**Không báo cáo khi chưa xác minh.** Checklist xác minh:
- [ ] Truy cập domain (khuyến nghị qua máy ảo/sandbox, không dùng máy thật để tránh rủi ro)
- [ ] Chụp screenshot toàn bộ trang, đặc biệt form nhập liệu
- [ ] Xác nhận nội dung có sao chép logo/giao diện/tên thương hiệu công ty không
- [ ] Xác nhận có form thu thập thông tin nhạy cảm (mật khẩu, OTP, thẻ ngân hàng...) không
- [ ] Chạy `phishing_toolkit.py check <domain>` để lấy issuer, serial, WHOIS, Cloudflare, VirusTotal, Safe Browsing — tool tự ghi log + sinh email báo cáo
- [ ] (Tùy chọn) Chạy `phishing_toolkit.py related <tên thương hiệu>` để tìm thêm domain cùng chiến dịch

### Bước 3 — Thu thập bằng chứng
Tập hợp thành 1 bộ hồ sơ cho mỗi domain:
- Screenshot trang giả mạo (có timestamp)
- URL đầy đủ (bao gồm path nếu có)
- Kết quả `phishing_toolkit.py check` (đã tự lưu vào `case_log.csv`)
- Link báo cáo VirusTotal
- Nguồn phát hiện (ai báo cáo, kênh nào, hoặc do `brandscan` chủ động phát hiện)

### Bước 4 — Báo cáo song song theo mức ưu tiên

| Ưu tiên | Kênh | Ghi chú |
|---|---|---|
| 1 | Google Safe Browsing | `https://safebrowsing.google.com/safebrowsing/report_phish/` — hiệu quả nhanh nhất, chặn cảnh báo trên Chrome/Firefox/Safari |
| 1 | Microsoft SmartScreen | `https://www.microsoft.com/wdsi/support/report-unsafe-site-guest/` — cùng nhóm chặn trình duyệt/OS với Safe Browsing, report thủ công (không có API submit công khai), chặn cảnh báo trên Edge/Windows |
| 1 | Cộng đồng bảo mật (VirusTotal/PhishTank/OpenPhish) | Cùng nhóm "chặn trình duyệt/cộng đồng" với Safe Browsing, xử lý song song. **VirusTotal**: có API submit, `phishing_toolkit.py check --submit` tự gọi. **PhishTank**: không có API submit công khai đáng tin cậy, report thủ công tại `phishtank.org`. **OpenPhish**: chỉ nhận qua email, tool tự sinh sẵn draft `reports/<domain>_openphish_report.txt` gửi `submit@openphish.com` |
| 2 | Registrar (theo WHOIS) | Có thể tạm ngưng/thu hồi domain hoàn toàn. Tra abuse email qua `domain_check.py` hoặc `lookup.icann.org` |
| 2b | Registry (ccTLD) | **Leo thang khi registrar-level không đủ hoặc bị phớt lờ**, đặc biệt với ccTLD lạ mà UDRP của ICANN không áp dụng hoàn toàn. Tool tự tra `lookup_registry_contact()`: bảng tĩnh (`CCTLD_REGISTRY_CONTACTS`) trước, fallback IANA referral (`iana_referral()`) cho ccTLD không có trong bảng — **loại trừ gTLD cổ điển .com/.net/.org...** (đã có đủ UDRP + registrar report ở trên, không cần leo thang thêm). Tự sinh draft `reports/<domain>_registry_report.txt` |
| 2c | VNCERT | **Chỉ gửi nếu domain thực sự nhắm vào nạn nhân tại Việt Nam** — tool không tự xác định được điều này, tự đánh giá trước khi gửi. Tool luôn tự sinh sẵn draft `reports/<domain>_vncert_report.txt` gửi `report@vncert.vn` |
| 3 | Hosting/CDN (Cloudflare, Fastly, Akamai, CloudFront, Stormwall, DDoS-Guard) | Tool tự nhận diện CDN đang che domain (`detect_cdn()`, kết hợp CNAME + header HTTP) và tự tra `report_url` tương ứng. Với CDN/proxy có thể ẩn IP gốc, tool còn tự quét 1 wordlist subdomain thường gặp (`mail/cpanel/ftp/dev/staging/webmail/secure/panel/direct`) để gợi ý IP gốc khả nghi (`origin_ip_scan` — chỉ là gợi ý, cần xác minh thêm) |
| 3b | Hosting/ISP của IP gốc | **Bước tiếp theo sau khi tìm được IP gốc qua `origin_ip_scan` ở trên** — nếu có ít nhất 1 IP khác IP chính của domain, tool tự tra IP WHOIS (`get_ip_whois()`, RDAP) lấy tên tổ chức + abuse email của ISP/hosting, rồi tự sinh sẵn draft DMCA/AUP takedown (`reports/<domain>_hosting_report.txt`). Nếu không tìm thấy IP gốc nào khả nghi, bỏ qua bước này |
| 4 | CA (SSL issuer) | **Chỉ áp dụng nếu CA có nhận report phishing** (xem bảng bước 5). Google Trust Services KHÔNG nhận loại report này |

### Bước 5 — Kiểm tra CA có nhận report không

| CA | Nhận report phishing? |
|---|---|
| Google Trust Services | Không |
| Let's Encrypt | Hạn chế (chủ yếu mis-issuance) |
| Sectigo/Comodo | Có |
| DigiCert | Có |
| ZeroSSL | Có |

→ Nếu issuer là Google Trust Services/Let's Encrypt, **bỏ qua bước report CA**, dồn lực vào Safe Browsing + registrar.

### Bước 6 — Theo dõi kết quả
- Ghi log ngày report, kênh report, trạng thái phản hồi
- Kiểm tra lại sau 24–72h: domain có bị Safe Browsing gắn cờ chưa (test bằng cách mở domain trên Chrome), SSL có bị revoke không (`domain_check.py` lại)
- Nếu sau 5–7 ngày không có phản hồi từ registrar, gửi follow-up hoặc escalate qua kênh pháp lý

### Bước 7 — Lưu trữ & báo cáo nội bộ
`phishing_toolkit.py check` đã tự động ghi mỗi case vào `case_log.csv` (domain, ngày phát hiện, issuer, registrar, kết quả VT/GSB, trạng thái). Mở file này bằng Excel để báo cáo định kỳ cho sếp/team, chỉ cần cập nhật cột `status` khi case được xử lý xong.

### Bước 8 — Chủ động phát hiện sớm (không chờ bị report)
Định kỳ (khuyến nghị hàng tuần) chạy:
```
python3 phishing_toolkit.py brandscan <domain-thật-của-công-ty>
python3 phishing_toolkit.py related <tên-thương-hiệu>
```
để tìm domain giả mạo mới xuất hiện trước khi chúng kịp lừa nạn nhân, thay vì chỉ xử lý bị động khi có người báo cáo.

## 5. Phân công (điền theo thực tế 2 người)

| Vai trò | Người phụ trách | Công việc |
|---|---|---|
| Phát hiện & xác minh | | Rà soát, chụp bằng chứng, chạy tool |
| Báo cáo & theo dõi | | Gửi report tới các kênh, theo dõi phản hồi |

## 6. Rủi ro cần lưu ý

- Không truy cập trang phishing bằng thiết bị/tài khoản thật — dùng máy ảo hoặc trình duyệt cô lập để tránh bị nhiễm mã độc hoặc lộ thông tin.
- Không tự ý "tấn công ngược" hay can thiệp vào hạ tầng domain đó — chỉ báo cáo qua kênh chính thức.
- Đảm bảo bằng chứng thu thập chính xác trước khi report, tránh report nhầm domain hợp pháp.
## Xử lý trường hợp nghi cloaking / sneaky redirect

Dùng menu **Cloaking Report** với URL, keyword Google, ảnh PC bình phong và ảnh
mobile/Google có nội dung vi phạm. Công cụ chạy bốn profile PC/mobile ×
direct/Google referrer, thu status, URL đích, header, kích thước, hash và HTML,
sau đó chọn cặp HTML mạnh nhất tương ứng evidence. Nếu không có khác biệt đủ mạnh
thì khóa report. Nếu đạt, công cụ xác định registrar/kênh abuse, tạo nội dung email
và gói evidence gồm hai ảnh, hai HTML cùng curl summary.

Nếu curl chỉ trả cùng trang bình phong, công cụ tự thử tầng rendered fallback bằng
Chromium desktop trực tiếp và iPhone giả lập mở Google, search keyword rồi click kết
quả đúng hostname. Tầng này lưu DOM sau khi JavaScript chạy; ngoài click kết quả
Google, không click site đích, đăng nhập hoặc thao tác giao dịch. Chỉ mở khóa
report khi visible content/final URL tạo thành cặp đối chứng rõ ràng. Nếu fallback
vẫn cùng trang bình phong, giữ trạng thái chưa đủ evidence và không gửi.

Không đăng nhập, đăng ký, nạp tiền, tải file hay bấm nút có thể tạo giao dịch.
Menu không tự gửi: người vận hành phải duyệt evidence và chủ động bấm **Gửi report**.

Nếu được phép truy cập URL nghi ngờ, dùng nút **Tạo đầy đủ hồ sơ**.
Công cụ chạy curl read-only theo bốn profile PC/mobile × direct/Google referrer;
mọi request đều giới hạn redirect, timeout và dung lượng rồi cho tải HTML.
Khác biệt được xét theo thiết bị, referrer và `x-matched-path` đúng theo flow curl.
Email chỉ được gửi sau cú bấm **Gửi report** rõ ràng. Registrar bắt buộc web form
thì mở đúng form để người vận hành nộp, không tự động hóa CAPTCHA hoặc submit form.
Header như `x-matched-path` và tham chiếu `best-traffic.pages.dev/traffic_dr.js`
được ghi vào evidence nếu xuất hiện, nhưng không được dùng riêng lẻ để khẳng định
cloaking hoặc cùng chủ thể vận hành.
