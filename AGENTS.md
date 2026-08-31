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
- `cloaking_review_queue.py`, `pages/10_Cloaking_Review.py`: queue JSON bền vững
  và trang duyệt/gửi riêng cho case cloaking do worker cách ly.
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
- SMTP chọn transport theo từng account: port 465/`ssl=true` dùng implicit TLS;
  port khác mặc định STARTTLS, chỉ dùng SMTP thường khi có `starttls=false`.
  Email evidence dùng timeout 60 giây, email thường 30 giây và gửi bằng
  `send_message`; chỉ retry một lần lỗi kết nối tạm thời với cùng Message-ID,
  không retry lỗi auth/sender/recipient.
- Cloaking luôn chạy HTTP đa profile trước. Playwright chỉ là lớp xác minh thụ
  động cho kết quả chưa chắc chắn; không click, type hoặc submit. Worker không
  tự gửi `LIKELY`/`POSSIBLE`/`INCONCLUSIVE`: tách chúng vào danh sách cloaking,
  chỉ retry đúng các domain người vận hành đã tích chọn và xác nhận thủ công.
- Worker chạy Playwright cả khi HTTP đã là `LIKELY` để chụp bằng chứng trực quan,
  gồm profile Googlebot Smartphone. Chọn tối đa hai ảnh đại diện cho cặp profile
  khác biệt mạnh nhất để hiển thị/đính kèm sau approve; các ảnh quan sát còn lại
  chỉ lưu nội bộ trong evidence.
- Queue cloaking phải bền vững qua refresh/job mới. Domain Worker chỉ tự động
  xử lý case không cloaking; mọi quyết định cloaking phải thực hiện tại
  Cloaking Review trên đúng record đã tích. Quyết định `not_cloaking` phải
  loại khối evidence và attachment cloaking trước khi gửi report thường.
- Queue cloaking dedupe theo ngày địa phương + full URL chuẩn hóa, không theo
  job/tài khoản SMTP; giữ source-job history, evidence mới nhất và terminal state.
  UI chọn case bằng native dataframe multi-row selection, không dùng checkbox
  `data_editor` chỉ để selection.
- Mỗi case cloaking giữ delivery ledger theo tài khoản gửi + email nhận + draft.
  Chỉ chuyển `SENT` khi mọi tài khoản thuộc phạm vi nguồn đã hoàn tất;
  `PARTIAL` vẫn nằm trong danh sách chờ/selectable và retry phải giữ nguyên các
  delivery `sent`/`already_sent`, mặc định ưu tiên tài khoản còn thiếu.
- Path probe như `/vi-vn/` chỉ dùng khám phá, không cộng điểm cloaking. Verdict
  nội dung nhạy cảm tách khỏi verdict cloaking. Proxy/vantage không được lộ
  credential trong evidence; ảnh thủ công chỉ nâng tối đa `POSSIBLE` và luôn cần
  approve + retry trước khi worker gửi.
- Cảnh báo phishing Cloudflare và trang lỗi trình duyệt/DNS là terminal page,
  không phải bằng chứng cloaking. Khi toàn bộ profile terminal, dùng
  `BLOCKED_OR_UNAVAILABLE`, bỏ manual review cloaking và tiếp tục gửi draft;
  không khẳng định domain đã bị thu hồi nếu chưa có WHOIS Hold/link status.
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
  Trang chủ/Check Domain/Quick Report/worker; bản đầu từng tự gửi `LIKELY` sau
  Playwright và giữ `POSSIBLE`/`INCONCLUSIVE` (hành vi gửi đã được thay thế bởi
  cơ chế cách ly mọi cloaking ngày 2026-08-30); file chính:
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
- 2026-08-29 — Hoàn thiện detector cloaking theo path/profile/vantage: `/vi-vn/`
  404 không còn tạo false positive; tách verdict nội dung khỏi cloaking; HTTP có
  sáu profile + Client Hints/Googlebot, Playwright có ba profile và ba mốc quan
  sát; hỗ trợ vantage proxy không lộ credential, coverage gap fail closed sang
  manual review, upload 2–4 ảnh thủ công ở Check Domain/worker và đính kèm sau
  approve + retry; file chính: `cloaking_detector.py`, `cloaking_ui.py`,
  `phishing_toolkit.py`, `domain_worker.py`, `pages/1_Check_Domain.py`,
  `pages/6_Domain_Worker.py`, `pages/7_Quick_Report.py`, `config.example.ini`;
  đã kiểm tra: 95/95 unittest, focused gates, py_compile và AppTest 4 page; tài
  liệu: `README.md`, `huong-dan-phat-hien-cloaking.md`, file này và skill dự án;
  lưu ý: vantage thật cần proxy điều tra do người vận hành cấu hình.
- 2026-08-30 — Loại terminal page khỏi cloaking và thu gọn ảnh: nhận diện cảnh
  báo phishing Cloudflare cùng trang lỗi trình duyệt/DNS, gắn
  `BLOCKED_OR_UNAVAILABLE`, xóa nghi ngờ cloaking cũ khi mọi Playwright profile
  đều terminal, cho worker gửi bình thường mà không đính ảnh lỗi; gallery ảnh
  Playwright dùng thumbnail 160 px trên hàng ngang; file chính:
  `cloaking_detector.py`, `cloaking_ui.py`, `domain_worker.py`, test detector và
  worker; đã kiểm tra: 100/100 unittest, focused gate và AppTest 4 page; tài liệu:
  `README.md`, `huong-dan-phat-hien-cloaking.md`, `03_Technical_Guide.md`, file
  này và skill dự án.
- 2026-08-30 — Bổ sung form báo cáo cộng đồng: thêm nút mở Chống Lừa Đảo và Cốc
  Cốc Safe trong Browser Blocking của Check Domain/Quick Report; chỉ mở tab mới,
  không tự submit; file chính: `community_report_ui.py`,
  `pages/1_Check_Domain.py`, `pages/7_Quick_Report.py`, test UI dùng chung; đã
  kiểm tra: focused unittest/py_compile và full suite/AppTest; tài liệu:
  `README.md`, `huong-dan-phat-hien-cloaking.md`, `03_Technical_Guide.md`, file
  này; lưu ý: không đổi invariant của skill dự án.
- 2026-08-30 — Tách Cloaking Review khỏi Domain Worker: mọi `LIKELY`,
  `POSSIBLE`, `INCONCLUSIVE` và coverage gap được ghi vào queue JSON bền vững;
  trang review riêng cho phép tích đúng record rồi gửi kèm evidence, gửi report
  thường không evidence cloaking hoặc bỏ qua; Domain Worker chỉ còn luồng tự
  động và link/số lượng chờ duyệt; thay `components.v1.html` cũ bằng
  `st.html`; file chính: `cloaking_review_queue.py`, `domain_worker.py`,
  `phishing_toolkit.py`, `pages/6_Domain_Worker.py`, `pages/10_Cloaking_Review.py`,
  `streamlit_app.py`, test queue/worker/navigation; đã đăng ký Cloaking Review trong
  `st.navigation` và thêm regression test bắt mọi internal `st.page_link` chưa có route;
  queue đã chuyển sang daily canonical URL, tự archive legacy duplicate và UI đã
  chuyển sang native multi-row selection; đã kiểm tra: 117/117 unittest, 36 focused
  test, AppTest theo entrypoint chuyển
  Trang chủ/Domain Worker/Cloaking Review, AppTest 5 page, compileall, py_compile
  spec, pip check và diff check; tài liệu: `README.md`,
  `huong-dan-phat-hien-cloaking.md`, `03_Technical_Guide.md`, file này và skill.
- 2026-08-30 — Ảnh bằng chứng cloaking đại diện: bổ sung Playwright Googlebot
  Smartphone, chạy browser capture cả với HTTP `LIKELY`, tự chọn tối đa hai ảnh
  của cặp profile khác biệt mạnh nhất và chụp lại ở lần gửi sau phê duyệt; email
  đính kèm manifest + cặp ảnh, không gửi toàn bộ ảnh quan sát; file chính:
  `cloaking_detector.py`, `domain_worker.py`, `pages/6_Domain_Worker.py`, test
  detector/worker; UI ghi rõ ảnh tự chụp và ẩn upload thủ công sau công tắc dự
  phòng; đã kiểm tra: focused 53 test, full suite, AppTest 4 page, compileall và
  pip check; tài liệu: `README.md`,
  `huong-dan-phat-hien-cloaking.md`, `03_Technical_Guide.md`, file này và skill.
- 2026-08-30 — Ổn định SMTP khi gửi evidence lớn: gửi `EmailMessage` bằng
  `send_message`, timeout 60 giây cho attachment/30 giây cho email thường, retry
  một lần với cùng Message-ID khi kết nối tạm thời bị ngắt và ghi rõ stage lỗi;
  tự chọn implicit TLS cho port 465/`ssl=true`, STARTTLS cho port khác, hỗ trợ
  `starttls=false` cho SMTP thường; file chính: `phishing_toolkit.py`,
  `tests/test_email_attachments.py`, `config.example.ini`; đã kiểm tra: 63 test
  SMTP/worker/provider tập trung, 122/122 full unittest, dry-route hai account
  runtime đã ẩn danh, compileall và pip check; tài liệu: `README.md`,
  `03_Technical_Guide.md`, file này và skill dự án; lưu ý: chỉ dùng mock, không
  gửi SMTP thật và không đưa secret runtime vào tài liệu.
- 2026-08-30 — Theo dõi gửi Cloaking Review theo từng tài khoản: queue schema v3
  thêm delivery ledger account/recipient/draft, trạng thái `PARTIAL`, chỉ hoàn
  tất khi mọi account nguồn đã gửi và tự phục hồi lượt `already_sent_today` từ
  worker event cũ; UI hiển thị email nhận, account đã gửi/còn chờ, tiến độ và mặc
  định chọn account còn thiếu; file chính: `cloaking_review_queue.py`,
  `domain_worker.py`, `pages/10_Cloaking_Review.py`, test queue/worker/AppTest;
  đã kiểm tra: 44 test tập trung và 130/130 full unittest (gồm AppTest case 1/2
  account); tài liệu: `README.md`, `03_Technical_Guide.md`,
  `huong-dan-phat-hien-cloaking.md`, file này và skill dự án; lưu ý: không gửi
  SMTP thật, migration chạy idempotent khi mở Cloaking Review; skill được kiểm
  tra thủ công vì `quick_validate.py` thiếu dependency `PyYAML` trong môi trường.

## Baseline chất lượng hiện tại

Nhóm `link_status` đã thống nhất Cloudflare warning/HTTP 403 là `BLOCKED`, không
phải `LIVE` hay `DIE`; mock response không iterable được xử lý an toàn. Toàn bộ
test phải xanh trước khi bàn giao thay đổi lõi. Baseline hiện tại là 130 test.
Detector cloaking có test thuần cho scoring/profile/path/vantage, fake browser
cho Playwright và mock attachment worker; không dùng URL nghi ngờ hay SMTP thật
trong test.
