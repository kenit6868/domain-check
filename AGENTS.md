# Domain Check Tool — hướng dẫn dự án cho Codex

## Mục tiêu

Đây là công cụ phòng thủ nội bộ để điều tra domain nghi phishing và chuẩn bị
báo cáo takedown hợp pháp. Dự án có CLI, giao diện Streamlit cục bộ, worker
xử lý batch, gửi SMTP và theo dõi phản hồi nhà cung cấp qua IMAP.

## Bản đồ dự án

- `phishing_toolkit.py`: lõi nghiệp vụ và CLI (`check`, `related`,
  `brandscan`, `send`); thực hiện enrichment domain, draft report, evidence và
  helper SMTP.
- `streamlit_app.py`, `streamlit_home.py`, `pages/`: điểm vào và các luồng UI
  Streamlit.
- `domain_worker.py`: worker batch nền có resume, lưu trạng thái tại
  `data/worker_jobs/`.
- `provider_replies.py`: đọc IMAP, phân loại phản hồi NCC, tạo reply theo
  thread và xử lý evidence.
- `link_status.py`, `domain_utils.py`: tiện ích kiểm tra link/domain.
- `cloaking_detector.py`, `cloaking_ui.py`: detector HTTP đa profile, xác minh
  Playwright thụ động, manifest/ảnh bằng chứng và UI dùng chung.
- `tests/`: bộ kiểm thử `unittest`.
- `README.md`: hướng dẫn người dùng; `CLAUDE.md`: ghi chú triển khai;
  `03_Technical_Guide.md` và `plan_phishing_takedown.md`: playbook vận hành.

## Lệnh chuẩn

```powershell
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
python -m unittest discover -s tests -v
python -m compileall -q .
python -m pip check
```

Không tự khởi động Streamlit nếu người dùng chưa yêu cầu. Build Windows dùng
`build_app.bat` và `PhishingTool.spec`.

## An toàn và quyền hạn

- `config.ini` là file secret cục bộ: không đọc giá trị để hiển thị, không
  trích dẫn, commit, upload hoặc đưa vào source/tài liệu. Chỉ dùng
  `config.example.ini` làm ví dụ.
- Không tạo artifact chứa `config.ini` thật nếu chưa có yêu cầu triển khai rõ
  ràng và người dùng hiểu rủi ro secret.
- Không tự gửi email, submit report bên ngoài, truy cập IMAP hoặc mở URL nghi
  ngờ nếu chưa được yêu cầu. Ưu tiên draft/preview.
- Chỉ phát triển mục đích phòng thủ; không thêm tính năng khai thác, né tránh,
  thu thập credential hoặc quét xâm nhập.
- Không xóa/sửa dữ liệu runtime (`data/`, reports, log, worker state) ngoài
  phạm vi yêu cầu cụ thể.

## Quy ước phát triển

- CLI và Streamlit phải dùng chung hàm lõi; không nhân bản logic check domain
  trong các page UI.
- Giữ cơ chế cô lập lỗi: lỗi API ngoài hoặc lỗi ghi log không được làm hỏng cả
  check/job.
- Mọi request mạng phải có timeout. `verify=False` chỉ dành cho một số probe
  domain nghi ngờ; không áp dụng cho API tin cậy.
- Cloaking luôn chạy HTTP đa profile trước. Playwright chỉ là lớp xác minh thụ
  động cho kết quả chưa chắc chắn; không click, type hoặc submit. Worker không
  gửi `POSSIBLE`/`INCONCLUSIVE` nếu chưa có phê duyệt thủ công được lưu trong job.
- Draft/email gửi nhà cung cấp phải dùng tiếng Anh; formatter external không
  được lấy nguyên label/detail tiếng Việt từ UI. Chỉ dữ liệu chứng cứ nguyên gốc
  như title hoặc matched keyword được phép giữ ngôn ngữ của website.
- Khi làm Streamlit, phải đọc `.agents/skills/developing-with-streamlit/SKILL.md`.
  Không thêm `streamlit.components.v1` mới; ưu tiên native widgets/component v2.
- Khi làm nghiệp vụ takedown, dùng skill
  `.agents/skills/phishing-takedown-tool/SKILL.md`.
- Sau khi sửa `phishing_toolkit.py`, `domain_worker.py`,
  `provider_replies.py`, `link_status.py` hoặc `domain_utils.py`, chạy toàn bộ
  `unittest`.

## Chọn skill đúng phạm vi

| Loại công việc | Skill cần dùng |
|---|---|
| Layout, widget, state, cache, theme, component hoặc hiệu năng Streamlit | `developing-with-streamlit` |
| Pipeline check, draft/report, SMTP/IMAP, evidence, link status hoặc worker | `phishing-takedown-tool` |
| Một Streamlit page có thay đổi cả UI lẫn nghiệp vụ takedown | Dùng **cả hai** skill theo thứ tự: Streamlit trước, takedown sau |

`AGENTS.md` luôn là bối cảnh chung của repo. Không lặp lại toàn bộ nội dung của
nó trong skill; skill chỉ chứa những invariant đặc thù cần được nạp theo loại
công việc.

## Bắt buộc cập nhật tài liệu sau thay đổi

Sau **mọi thay đổi có ý nghĩa** (tính năng mới, sửa hành vi, thay đổi cấu hình,
luồng UI, endpoint, vận hành, kiểm thử hoặc kiến trúc), trước khi bàn giao:

1. Xác định tài liệu bị ảnh hưởng và cập nhật cùng thay đổi mã nguồn.
2. Cập nhật `README.md` khi thay đổi cài đặt, cách dùng hoặc UI.
3. Cập nhật `CLAUDE.md` khi thay đổi chi tiết triển khai, invariant hoặc quyết
   định kỹ thuật.
4. Cập nhật `03_Technical_Guide.md` hoặc `plan_phishing_takedown.md` khi thay
   đổi quy trình vận hành/takedown.
5. Cập nhật file này khi kiến trúc, lệnh chuẩn, quy tắc an toàn, quality
   baseline hoặc vị trí file thay đổi.
6. Cập nhật skill dự án khi workflow lặp lại hoặc ràng buộc đặc thù thay đổi.

Không cần tạo churn tài liệu cho thay đổi thuần định dạng hoặc refactor không
đổi hành vi. Khi không cập nhật tài liệu, nêu ngắn gọn lý do trong bàn giao.
Không bao giờ đưa secret từ `config.ini` vào bất kỳ tài liệu nào.

## Definition of Done — bắt buộc trước khi bàn giao

Một tính năng mới, thay đổi hành vi hoặc bug fix chỉ được coi là hoàn thành khi:

1. Đã kiểm tra yêu cầu, các luồng ảnh hưởng và giữ tương thích với CLI/UI nếu
   chúng dùng chung nghiệp vụ.
2. Đã chạy test trực tiếp cho phần sửa; với thay đổi lõi, worker, email, link
   status hoặc parser phải chạy toàn bộ `python -m unittest discover -s tests -v`.
3. Đã chạy kiểm tra nền phù hợp: tối thiểu `python -m compileall -q .`; chạy
   `python -m pip check` khi thay đổi dependency/cấu hình/build.
4. Với UI Streamlit, đã kiểm tra luồng người dùng liên quan bằng AppTest hoặc
   chạy app cục bộ khi người dùng cho phép. Với thao tác SMTP/IMAP/report thật,
   chỉ kiểm tra bằng mock/dry-run/draft trừ khi được cấp quyền gửi thật.
5. Đã xem lại lỗi, warning và kết quả test; không được tuyên bố hoàn thành khi
   test liên quan còn đỏ mà không báo rõ nguyên nhân và mức ảnh hưởng.
6. Đã cập nhật tài liệu và phần “Trạng thái thay đổi gần đây” bên dưới.

## Trạng thái thay đổi gần đây

Phần này là bản ghi ngắn gọn để một phiên Codex sau có thể hiểu trạng thái dự
án mà không phải đọc lại toàn bộ source. Sau mỗi thay đổi có ý nghĩa, thêm hoặc
cập nhật một mục theo mẫu:

```text
YYYY-MM-DD — <tính năng/sửa lỗi>: <hành vi hiện tại>; file chính: <danh sách>;
đã kiểm tra: <test/lệnh>; tài liệu: <file đã cập nhật>; lưu ý: <nếu có>.
```

Giữ tối đa khoảng 10 mục gần nhất. Khi mục cũ trở thành kiến thức ổn định, gộp
nội dung quan trọng vào “Bản đồ dự án”, “Quy ước phát triển” hoặc tài liệu phù
hợp rồi bỏ mục cũ. Không ghi secret, dữ liệu case, email thật hoặc URL nghi ngờ
vào phần này.

- 2026-08-25 — Khởi tạo ngữ cảnh Codex: thêm `AGENTS.md` và skill
  `phishing-takedown-tool`; đã kiểm tra diff và cấu trúc skill thủ công; tài
  liệu: chính file này và `.agents/skills/phishing-takedown-tool/SKILL.md`;
  lưu ý: validator tự động của skill chưa chạy vì môi trường thiếu `PyYAML`.
- 2026-08-29 — Phát hiện cloaking đa profile: thêm detector HTTP dùng chung cho
  Trang chủ/Check Domain/Quick Report/worker; worker tự nâng cấp case chưa rõ
  bằng Playwright thụ động, tự gửi `LIKELY` kèm manifest/ảnh và giữ
  `POSSIBLE`/`INCONCLUSIVE` để duyệt rồi retry; file chính:
  `cloaking_detector.py`, `cloaking_ui.py`, `phishing_toolkit.py`,
  `domain_worker.py`, `pages/1_Check_Domain.py`, `pages/6_Domain_Worker.py`,
  `pages/7_Quick_Report.py`; đã kiểm tra: 76 test trước Playwright, 80/80 test
  cuối (gồm fake Playwright/attachment/worker), Chromium local smoke, AppTest
  4 page, compileall và pip check; tài liệu: `README.md`,
  `huong-dan-phat-hien-cloaking.md`, file này
  và skill dự án; lưu ý: cần `python -m playwright install chromium` trên máy mới.
- 2026-08-29 — Chuẩn hóa ngôn ngữ evidence gửi nhà cung cấp: formatter cloaking
  dùng profile label và signal description tiếng Anh riêng, không lấy chuỗi
  tiếng Việt của UI; dữ liệu quan sát gốc vẫn được bảo toàn; file chính:
  `cloaking_detector.py`, `tests/test_cloaking_detector.py`; đã kiểm tra: test
  formatter chuyên biệt và 81/81 full suite; tài liệu: `README.md`, file này và
  skill dự án.

## Baseline chất lượng hiện tại

Nhóm `link_status` đã thống nhất Cloudflare warning/HTTP 403 là `BLOCKED`, không
phải `LIVE` hay `DIE`; mock response không iterable được xử lý an toàn. Toàn bộ
test phải xanh trước khi bàn giao thay đổi lõi. Detector cloaking có test thuần
cho scoring/profile comparison và fake browser cho Playwright; không dùng URL
nghi ngờ hay SMTP thật trong test.
