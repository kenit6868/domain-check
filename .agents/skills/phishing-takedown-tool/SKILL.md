---
name: phishing-takedown-tool
description: Thực hiện thay đổi hoặc đánh giá workflow Domain Check Tool về kiểm tra phishing, báo cáo takedown, worker, SMTP/IMAP và evidence; không dùng cho công việc Streamlit thuần túy.
---

# Phishing Takedown Tool

Dùng skill này khi thay đổi hoặc đánh giá pipeline kiểm tra phishing, draft và
report, SMTP/IMAP, evidence, link status hoặc worker. Với layout/widget/state
Streamlit, dùng `developing-with-streamlit` thay vì skill này; nếu một page đổi
cả UI lẫn nghiệp vụ, dùng cả hai skill.

## Invariant cần giữ

- `run_check()` là pipeline dùng chung giữa CLI và UI. Không tạo một pipeline
  kiểm tra domain khác trong page Streamlit.
- Một lỗi provider bên ngoài, log hoặc sinh draft phải được cô lập để kết quả
  điều tra còn lại vẫn được trả về.
- Worker phải giữ tính resume, chống gửi trùng và trạng thái job có thể đọc lại.
  Mọi thay đổi các phần này cần có test hồi quy.
- Gửi email, submit report, đọc IMAP và mở URL nghi ngờ đều là hành động ngoài
  hệ thống: chỉ thực hiện khi người dùng cho phép rõ ràng; mặc định là draft/
  preview.
- Khi đổi phân loại link, chốt semantics của `LIVE`, `BLOCKED`, `DIE`,
  `GEO-BLOCK`, `TEMP ERROR` và `UNREACHABLE`, rồi đồng bộ code, test và UI.
- Detector cloaking phải thụ động: HTTP dùng session tách biệt theo profile;
  Playwright không click, type hoặc submit. Chỉ chạy Playwright sau lớp HTTP,
  ưu tiên cho `POSSIBLE`/`INCONCLUSIVE`.
- Khác biệt giữa URL gốc và một path probe như `/vi-vn/` chỉ là khám phá đường
  dẫn, không được cộng điểm cloaking. Phân loại nội dung nhạy cảm phải tách khỏi
  verdict cloaking.
- Credential proxy/vantage chỉ tồn tại trong cấu hình runtime; không ghi vào UI,
  error, manifest hoặc draft. Nếu server khai báo biến theo quốc gia và thiết bị
  nhưng chưa có vantage ngoài mạng hiện tại, worker phải fail closed sang manual
  review thay vì coi `NO_SIGNAL` là đủ an toàn.
- Bằng chứng ảnh do người vận hành tải lên phải kiểm tra signature, giới hạn số
  lượng/kích thước, chỉ nâng tối đa lên `POSSIBLE` và không tự phê duyệt. Worker
  chỉ đính kèm/gửi sau bước approve + retry rõ ràng.
- Trang terminal do provider/trình duyệt tạo ra (cảnh báo phishing Cloudflare,
  DNS/browser error như “Không thể truy cập trang web này”) không được dùng làm
  chênh lệch cloaking. Khi mọi profile đều terminal, trả
  `BLOCKED_OR_UNAVAILABLE`, bỏ manual review cloaking và cho worker tiếp tục gửi
  draft bình thường; không diễn giải trạng thái này thành bằng chứng chắc chắn
  domain đã bị thu hồi nếu chưa có WHOIS Hold/link status xác nhận.
- Worker chỉ tự gửi case cloaking khi verdict cuối là `LIKELY` và bằng chứng đã
  được ghi/đính kèm. `POSSIBLE`/`INCONCLUSIVE` phải dừng riêng domain để duyệt;
  chỉ retry gửi sau khi URL nằm trong `approved_cloaking_targets`.
- Mọi nội dung do tool soạn để gửi nhà cung cấp phải dùng tiếng Anh. Không tái
  sử dụng trực tiếp label/detail tiếng Việt của UI trong draft; dữ liệu quan sát
  nguyên gốc như page title hoặc matched keyword có thể giữ nguyên làm bằng chứng.

## Tài liệu và kiểm tra

- Đọc `AGENTS.md` trước; chỉ mở `CLAUDE.md` khi cần invariant/hành vi cũ,
  `README.md` khi thay đổi cách dùng và playbook khi thay đổi quy trình.
- Không đưa giá trị `config.ini` vào kết quả; chỉ dùng `config.example.ini` để
  xem schema cấu hình.
- Chạy test liên quan và toàn bộ `python -m unittest discover -s tests -v` sau
  thay đổi nghiệp vụ. Với cấu hình/build, chạy thêm `compileall` và `pip check`
  khi phù hợp.
- Tuân theo quy tắc cập nhật tài liệu trong `AGENTS.md`; không lặp lại quy tắc
  chung đó tại đây.
