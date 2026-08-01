---
name: investigate-phishing-domain
description: Điều tra 1 domain phishing nghi ngờ trong dự án domain-check-tool — chạy phishing_toolkit.py check, xác minh domain thực sự đang giả mạo thương hiệu trước khi đề xuất bất kỳ report nào, và đưa ra đúng kênh báo cáo cùng email đã điền sẵn. Kích hoạt skill này bất cứ khi nào người dùng nhắc tới 1 domain đáng ngờ, 1 trang phishing/lừa đảo có thể có, muốn "check", "verify", hoặc "investigate" 1 domain, hoặc dán tên domain vào trong ngữ cảnh dự án này. Cũng kích hoạt với "tra domain", "kiểm tra domain", "domain này có phải phishing không".
---

# Điều tra 1 domain phishing nghi ngờ

Dự án này (`domain-check-tool`) tồn tại để giúp 1 team nhỏ xác minh và báo cáo các domain giả mạo
thương hiệu công ty nhằm lừa đảo. Đọc `plan_phishing_takedown.md` ở gốc dự án 1 lần vào đầu phiên
làm việc nếu chưa đọc — đó là quy trình chuẩn mà skill này thực hiện từng bước.

## Các bước

1. **Chuẩn hóa input.** Bỏ protocol/path khỏi bất kỳ thứ gì người dùng đưa, chỉ giữ lại domain trần.

2. **Chạy tool.**
   ```bash
   python3 phishing_toolkit.py check <domain>
   ```
   Chỉ riêng lệnh này đã cho bạn issuer/serial SSL, WHOIS/registrar, phát hiện Cloudflare, kết quả
   VirusTotal và Safe Browsing (nếu đã cấu hình API key trong `config.ini`), và tự động ghi 1 dòng
   vào `case_log.csv` cùng sinh sẵn email báo cáo draft vào `reports/`.

3. **Không đề xuất report domain chỉ vì tool chạy thành công.** Chạy tool chỉ thu thập dữ kiện — nó
   không xác nhận đây là phishing. Trước khi đề xuất bất kỳ report abuse nào, đi qua checklist xác
   minh từ bước 2 của `plan_phishing_takedown.md` cùng người dùng:
   - Domain có giả mạo thương hiệu thật về mặt hình ảnh/cấu trúc không (tên gõ-nhầm, sao chép
     logo/giao diện, form đăng nhập giả)?
   - VirusTotal đã gắn cờ chưa, hay domain còn quá mới/chưa được index?
   - Có nguồn đáng tin cho lý do domain này bị nghi ngờ không (người dùng báo cáo, giám sát thương
     hiệu, v.v.)?

   Nếu người dùng chưa xác nhận điều này và chưa có cờ VirusTotal/Safe Browsing nào, nói rõ điều đó
   và gợi ý họ xem screenshot bằng trình duyệt/máy ảo cô lập thay vì suy đoán ác ý chỉ từ dữ liệu
   WHOIS/SSL. Các dữ kiện hạ tầng domain (ai đăng ký, CA nào cấp chứng chỉ) không bao giờ là bằng
   chứng phishing tự thân.

4. **Sau khi đã xác minh**, đi qua thứ tự ưu tiên báo cáo từ chính output của tool và từ
   `plan_phishing_takedown.md`: Google Safe Browsing đầu tiên (bảo vệ ở tầng trình duyệt nhanh nhất),
   registrar thứ hai, CDN/hosting (vd. Cloudflare) thứ ba, CA cuối cùng — và chỉ khi CA đó có nhận
   report phishing. Bảng `CA_ABUSE_NOTES` trong `phishing_toolkit.py` đã biết sẵn Google Trust
   Services không nhận report phishing; tin vào đó thay vì giải thích lại chính sách CA từ đầu mỗi
   lần.

5. **Chỉ người dùng tới các draft đã sinh sẵn** trong `reports/<domain>_*.txt` thay vì tự viết lại
   email report bằng tay — chúng đã được điền sẵn tên thương hiệu và thông tin liên hệ từ
   `config.ini`. Chỉ cần nói người dùng điền vào chỗ placeholder bằng chứng
   (`[đính kèm screenshot trang giả mạo]`) trước khi gửi.

6. **Nếu `config.ini` chưa tồn tại**, bảo người dùng chạy `cp config.example.ini config.ini` rồi điền
   vào — đừng tự bịa API key hay thông tin công ty.

## Khi có gì đó trong tool cần sửa

Nếu bạn sửa `phishing_toolkit.py` hoặc `domain_check.py` trong lúc làm việc này (fix bug, thêm field
mới, v.v.), cập nhật `CLAUDE.md` trong cùng lượt theo đúng chỉ dẫn bảo trì ở cuối file đó — các phiên
sau phụ thuộc vào việc nó luôn cập nhật.
