# Domain Check Tool — Phát hiện & báo cáo domain phishing

Bộ công cụ hỗ trợ 2 người rà soát, xác minh và báo cáo (takedown) các domain
giả mạo thương hiệu công ty để lừa đảo.

## Cài đặt

```bash
pip install -r requirements.txt
cp config.example.ini config.ini
```

Mở `config.ini`, điền:
- `vt_api_key` — API key VirusTotal (miễn phí tại virustotal.com/gui/my-apikey). Để trống nếu chưa có, tool vẫn chạy được.
- `gsb_api_key` — API key Google Safe Browsing (tùy chọn).
- `brand_name`, `contact_name`, `contact_email` — dùng để điền sẵn vào email báo cáo.

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

Web UI gọi thẳng cùng các hàm trong `phishing_toolkit.py` mà CLI dùng (không viết
lại logic riêng), nên kết quả giữa CLI và web luôn khớp nhau.

## Quy trình đầy đủ

Xem `plan_phishing_takedown.md` — quy trình 8 bước từ phát hiện, xác minh,
thu thập bằng chứng, báo cáo theo đúng thứ tự ưu tiên, tới theo dõi kết quả.

## Cấu trúc file

```
phishing_toolkit.py       - Tool chính (check / related / brandscan)
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

Worker chạy bằng process riêng nên vẫn tiếp tục nếu đóng hoặc refresh tab trình
duyệt. Trang này hiển thị tiến độ, kết quả gửi của từng domain và có nút yêu cầu
dừng. Draft VNCERT mặc định không tự gửi; chỉ bật nếu toàn bộ danh sách thực sự
nhắm tới nạn nhân tại Việt Nam. Dữ liệu trạng thái được lưu trong
`worker_jobs/` và thư mục này không được commit.
