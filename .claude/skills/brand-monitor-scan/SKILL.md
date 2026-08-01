---
name: brand-monitor-scan
description: Chủ động quét tìm domain phishing mới giả mạo thương hiệu công ty, trước khi có ai report — chạy phishing_toolkit.py brandscan (biến thể typosquat) và related (tìm qua Certificate Transparency) rồi triage kết quả. Kích hoạt khi người dùng yêu cầu "brand scan", "quét thương hiệu", giám sát định kỳ/hàng tuần, muốn "tìm" hoặc "phát hiện" domain phishing mới một cách chủ động, hoặc hỏi có gì thay đổi kể từ lần quét trước.
---

# Quét chủ động giám sát thương hiệu

Khác với `investigate-phishing-domain` (kiểm tra 1 domain đã bị nghi ngờ sẵn), skill này quét tìm các
domain chưa ai gắn cờ. Dùng cho bước "8" định kỳ được mô tả trong `plan_phishing_takedown.md`.

## Các bước

1. **Lấy 2 input bắt buộc từ người dùng nếu chưa biết:**
   - Domain thật, hợp pháp của công ty (để so sánh typosquat)
   - Tên thương hiệu/từ khóa (để tìm chứng chỉ — có thể khác domain, vd. tên sản phẩm)

   Đừng đoán mò — hỏi lại nếu thực sự chưa rõ, vì quét sai domain sẽ lãng phí cả lượt chạy.

2. **Chạy cả 2 lượt quét:**
   ```bash
   python3 phishing_toolkit.py brandscan <domain-that>
   python3 phishing_toolkit.py related "<từ khóa thương hiệu>"
   ```
   `brandscan` chỉ trả về các biến thể đã được đăng ký (cờ `-r` của dnstwist đã được cài sẵn) — mọi
   dòng trong output đều đáng xem xét. `related` có thể trả về nhiều nhiễu (domain hợp pháp tình cờ
   nhắc tới từ khóa, vd. bài báo, công ty không liên quan) — đừng coi mọi kết quả là mối đe dọa.

3. **Triage trước khi đề xuất hành động.** Với mỗi domain ứng viên từ 1 trong 2 lượt quét, một lượt
   kiểm tra nhanh sẽ tốt hơn là report theo phản xạ:
   - Vừa đăng ký gần đây → đáng ngờ hơn
   - Đã resolve ra IP đang hoạt động / đã có sẵn chứng chỉ SSL → đáng ngờ hơn (kẻ tấn công đang dựng
     hạ tầng, không chỉ đăng ký giữ chỗ)
   - Trông giống 1 doanh nghiệp hợp pháp không liên quan, tình cờ trùng 1 từ → bỏ qua

4. **Chuyển những gì đáng điều tra tiếp** sang skill/quy trình `investigate-phishing-domain`
   (`phishing_toolkit.py check <domain>`) thay vì report thẳng từ kết quả quét — kết quả quét chỉ cho
   biết domain đó tồn tại và trông giống, không phải nó đang thực sự phishing.

5. **Tóm tắt kết quả ngắn gọn**: bao nhiêu ứng viên từ mỗi lượt quét, bao nhiêu qua được vòng triage
   nhanh, và cái nào bạn đang đề xuất chạy `check` đầy đủ. Đừng dump nguyên output thô của
   dnstwist/crt.sh không qua lọc — làm vậy sẽ mất hết ý nghĩa của việc triage.
