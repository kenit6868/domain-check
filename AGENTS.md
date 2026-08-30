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

- 2026-08-29 — Thêm menu Cloaking Report: phân mức evidence và tạo draft
  review-only, không tự truy cập URL hay gửi report; file chính:
  `cloaking_report.py`, `pages/10_Cloaking_Report.py`, `streamlit_app.py`; đã
  kiểm tra: 4 unit test mới và AppTest đạt, compileall đạt, toàn suite 58/63
  đạt với 5 lỗi baseline `link_status`; tài liệu: `README.md`, `CLAUDE.md`,
  `plan_phishing_takedown.md`; lưu ý: chỉ thấy game/từ khóa lệch chưa đủ kết
  luận cloaking.

- 2026-08-29 — Siết quality gate Cloaking Report: chỉ tạo draft khi đủ ảnh PC,
  mobile, Google, thông số F12 + curl, HTML/chứng từ, đối chứng mạnh và ghi chú
  tái hiện; file chính: `cloaking_report.py`, `pages/10_Cloaking_Report.py`,
  `tests/test_cloaking_report.py`; đã kiểm tra: 6/6 unit test và AppTest đạt,
  compileall đạt, toàn suite 60/65 đạt với 5 lỗi baseline `link_status`; tài
  liệu: `README.md`, `CLAUDE.md`, `plan_phishing_takedown.md`; lưu ý: menu không
  lưu evidence hoặc tự submit report.

- 2026-08-29 — Tự động evidence kỹ thuật cho Cloaking Report: thêm probe HTTP
  read-only PC/mobile × direct/Google referrer, SSRF guard, redirect/timeout/body
  limit, tự điền tóm tắt và tải HTML trong session; file chính:
  `cloaking_probe.py`, `pages/10_Cloaking_Report.py`,
  `tests/test_cloaking_report.py`; đã kiểm tra: 7/7 unit test và AppTest đạt,
  compileall đạt, toàn suite 61/66 đạt với 5 lỗi baseline `link_status`; tài
  liệu: `README.md`, `CLAUDE.md`, `plan_phishing_takedown.md`; lưu ý: probe
  không thay thế ảnh trình duyệt hoặc xác nhận thủ công nội dung vi phạm.

- 2026-08-29 — Sửa autofill evidence Cloaking: ghi output probe vào session
  trước rerun và khóa read-only các ô HTTP/curl/ghi chú, người dùng không còn
  phải chép tay; file chính: `pages/10_Cloaking_Report.py`; đã kiểm tra: AppTest,
  7/7 unit test và compileall đạt, toàn suite 61/66 đạt với 5 lỗi baseline
  `link_status`; tài liệu: `README.md`, `CLAUDE.md`; lưu ý: ba ảnh trình duyệt
  vẫn upload thủ công.

- 2026-08-29 — Chuyển Cloaking probe sang curl thật: chạy curl bằng argv không
  qua shell cho PC/mobile × direct/Google, validate từng redirect, thu command,
  header, HTML, hash và fingerprint traffic_dr.js; file chính:
  `cloaking_probe.py`, `pages/10_Cloaking_Report.py`; đã kiểm tra: 8/8 unit
  test, AppTest và compileall đạt, toàn suite 62/67 đạt với 5 lỗi baseline
  `link_status`; tài liệu: `README.md`, `CLAUDE.md`,
  `plan_phishing_takedown.md`; lưu ý: fingerprint chỉ là evidence hỗ trợ.

- 2026-08-29 — Cloaking Report auto-only: bỏ toàn bộ upload/checkbox/ô kỹ thuật
  thủ công, thêm Chromium probe read-only và gói ZIP draft+screenshot+DOM+network
  metadata+curl HTML; file chính: `cloaking_browser.py`,
  `pages/10_Cloaking_Report.py`, `cloaking_probe.py`; đã kiểm tra: 9/9 unit test,
  AppTest, compileall, Chromium about:blank và pip check đạt, toàn suite 63/68
  đạt với 5 lỗi baseline `link_status`; tài liệu: `README.md`, `CLAUDE.md`,
  `plan_phishing_takedown.md`; lưu ý: browser Google profile click kết quả thật
  (curl vẫn dùng referrer), và công cụ không tự gửi report; Playwright + Chromium đã
  cài cho môi trường Python hiện tại.

- 2026-08-29 — Chặn draft Cloaking thiếu đối chứng: bỏ so sánh hash responsive,
  chỉ cho report khi browser bắt được cả profile bình phong và profile vi phạm
  trong một cặp device/referrer; file chính: `cloaking_browser.py`,
  `pages/10_Cloaking_Report.py`, `tests/test_cloaking_report.py`; đã kiểm tra:
  11/11 unit test, AppTest, compileall và pip check đạt, toàn suite 65/70 đạt
  với 5 lỗi baseline `link_status`; tài liệu: `README.md`, `CLAUDE.md`,
  `plan_phishing_takedown.md`; lưu ý: bốn profile cùng game sẽ khóa draft.

- 2026-08-29 — Browser Google click thật cho Cloaking: thay profile referrer bằng
  mở Google Search, tìm anchor đúng hostname và click; CAPTCHA/không có kết quả
  làm profile fail thay vì fallback; file chính: `cloaking_browser.py`,
  `pages/10_Cloaking_Report.py`, `tests/test_cloaking_report.py`; đã kiểm tra:
  12/12 unit test, AppTest, compileall và pip check đạt, toàn suite 66/71 đạt
  với 5 lỗi baseline `link_status`; tài liệu: `README.md`, `CLAUDE.md`,
  `plan_phishing_takedown.md`; lưu ý: chỉ click kết quả Google, không click site đích.

- 2026-08-30 — Cloaking Report evidence-to-send: input URL+keyword+hai ảnh, tự
  chọn hai HTML đối chứng; curl giống nhau thì fallback Chromium desktop trực tiếp
  và iPhone search Google/click đúng hostname để lấy DOM sau render; xác định
  registrar/kênh abuse, tạo ZIP và gửi email có
  attachment chỉ sau cú bấm rõ ràng; file chính: `cloaking_workflow.py`,
  `cloaking_probe.py`, `cloaking_render_probe.py`, `pages/10_Cloaking_Report.py`,
  `phishing_toolkit.py`, `tests/test_cloaking_report.py`; đã kiểm tra: 15/15 unit
  test, AppTest, compileall, Chromium render và pip check đạt, toàn suite 69/74
  đạt với 5 lỗi baseline `link_status`;
  tài liệu: `README.md`, `CLAUDE.md`, `plan_phishing_takedown.md`, `AGENTS.md`;
  lưu ý: provider bắt buộc web form không được gửi SMTP hoặc tự submit.

## Baseline chất lượng hiện tại

Bộ test hiện có lỗi tại nhóm `link_status`: một số mock chưa cung cấp
`iter_content` iterable, và phân loại Cloudflare warning chưa khớp kỳ vọng test.
Khi sửa khu vực này, cần chốt chính sách trạng thái rồi đồng bộ code và test.
