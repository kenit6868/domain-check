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
- `domain_worker.py`: precheck email/cloaking và worker batch nền có resume cho
  domain thường; job lưu tại `data/worker_jobs/`.
- `provider_replies.py`: đọc IMAP, phân loại phản hồi NCC, tạo reply theo
  thread và xử lý evidence.
- `mail_statistics.py`, `pages/11_Mail_Statistics.py`: đếm mailbox theo
  `INTERNALDATE`; page Thống kê email chỉ hiển thị tổng Inbox/Thư rác trong ngày.
- `report_statistics.py`: phân tích hiệu quả report theo đúng account và khoảng
  ngày; dùng `sent_log.csv` làm nguồn delivery, cache Sent Mail chỉ enrich
  evidence khi khớp mạnh và cache Provider Replies chỉ đưa reply liên quan vào
  delivery → reply → outcome; không mở IMAP trong bước phân tích, không gửi
  mail và không lưu body/credential.
- `sent_mail_evidence.py`, `general_statistics.py`,
  `pages/13_General_Statistics.py`: đồng bộ Sent metadata trong bộ nhớ và điều
  phối một lần sync Inbox/Sent/Junk + phản hồi NCC cho Thống kê tổng quát; cache
  chỉ chứa index/snapshot đã sanitize để thống kê/backfill delivery cũ.
- `link_status.py`, `domain_utils.py`: tiện ích kiểm tra link/domain.
- `cloaking_detector.py`, `cloaking_ui.py`: detector HTTP đa profile, xác minh
  Playwright thụ động, manifest/ảnh bằng chứng và UI dùng chung.
- `browser_evidence.py`: lõi Browser Evidence capture Playwright không tương tác
  (passive, DOM destination và verified navigation tùy chọn) cho report thường, ghi PNG + manifest/hash;
  ưu tiên DOM destination nguồn–đích cho domain thường rồi fallback passive; detector
  cloaking vẫn giữ Playwright thụ động đa profile.
- `cloaking_review_queue.py`, `cloaking_review_sender.py`,
  `pages/10_Cloaking_Review.py`: queue JSON/ledger bền vững, lớp preview + gửi
  SMTP trực tiếp và trang duyệt riêng cho case cloaking do worker cách ly.
- `pages/12_Domain_Evidence_Review.py`: page review riêng cho domain thường thiếu
  Browser Evidence, đọc preflight v4 theo ngày/full URL và gửi ảnh thủ công trực tiếp.
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
  chỉ xử lý đúng domain người vận hành đã chọn và xác nhận thủ công tại review.
- Worker chạy Playwright cả khi HTTP đã là `LIKELY` để chụp bằng chứng trực quan,
  gồm profile Googlebot Smartphone. Chọn tối đa hai ảnh đại diện cho cặp profile
  khác biệt mạnh nhất để hiển thị/đính kèm sau approve; các ảnh quan sát còn lại
  chỉ lưu nội bộ trong evidence.
- Queue cloaking phải bền vững qua refresh/job mới. Domain Worker chỉ tự động
  xử lý case không cloaking; mọi quyết định cloaking phải thực hiện tại
  Cloaking Review trên đúng record đã chọn. Quyết định `not_cloaking` phải
  loại khối evidence/attachment cloaking, rồi dùng Browser Evidence report thường
  riêng đã preview (nguồn–đích, passive fallback hoặc upload) trước khi gửi. UI
  chỉ hiển thị record của ngày địa phương hiện tại; record cũ vẫn giữ nội bộ để
  audit nhưng sang ngày mới phải check lại URL để tạo case mới.
- Nút precheck Domain Worker dùng schema v4: lookup email và cloaking chạy song
  song theo từng full URL, cloaking không dùng cache email và case cần duyệt chỉ
  enqueue/ghi preflight tăng dần khi có ít nhất một email nhận hợp lệ. Case
  cloaking không email phải vào `excluded_no_email`, không được migrate/hiển thị
  ở Cloaking Review; số đếm/link Domain Worker chỉ tính case có email của ngày
  hiện tại. Chỉ `ready` được chuyển sang job gửi thường; không chờ pipeline gửi
  mới phân loại cloaking.
- Sau recipient/cloaking precheck, domain thường phải capture Browser Evidence.
  Chỉ evidence hợp lệ mới vào `ready`; capture lỗi vào `evidence_review`, tách
  khỏi batch tự động. Check Domain và page `Domain Evidence Review` nhận 1–3 ảnh
  PNG/JPEG thủ công, preview thumbnail ngay; Domain Worker chỉ hiển thị số lượng/link
  và không có uploader inline. Review phải tạo dry-run preview exact theo từng
  account/recipient, giữ fingerprint draft + manifest trước SMTP; gửi lỗi giữ
  evidence và retry chỉ delivery còn thiếu. Đây là evidence report thường, không
  thay quy tắc 2–4 ảnh cloaking.
- Verified navigation là lớp opt-in riêng cho report thường: chỉ click anchor
  HTTP(S) hoặc button `data-href` không submit có nhãn Register/Login trong
  browser cô lập, chụp đúng ảnh trước/sau và ghi URL cuối + redirect 3xx vào
  manifest. Không nhập credential, type, submit hoặc tải file; không có URL mới
  hay gặp terminal page thì fail closed sang evidence thụ động/thủ công. Không
  dùng lớp này cho detector cloaking. Check Domain cho phép chọn chế độ này trong
  expander Browser Evidence, preview đúng hai ảnh và URL trước/sau click; cùng
  artifact đã preview được truyền cho cả gửi đơn và gửi tất cả. Widget/state phải
  giữ evidence qua rerun; nếu verified thất bại, vẫn dùng được Passive DOM hoặc
  uploader thủ công 1–3 ảnh mà không cần nút lưu trung gian.
- Cloaking Review tạo draft preview và gửi đồng bộ, trực tiếp bằng SMTP helper;
  không tạo worker job hoặc launch process. Nội dung đã preview phải chính là
  nội dung gửi. Lock ngắn theo queue ID chỉ ngăn hai phiên gửi cùng case và không
  khóa Domain Worker. `data/cloaking_send_jobs/`, `QUEUED_*` và job review cũ
  chỉ còn được nhận diện để sync/migrate delivery lịch sử.
- Queue cloaking dedupe theo ngày địa phương + full URL chuẩn hóa, không theo
  job/tài khoản SMTP; giữ source-job history, evidence mới nhất và terminal state.
  UI dùng một native dataframe với `ButtonColumn` theo từng dòng, không dùng
  checkbox `data_editor` hoặc row index làm source of truth. Callback phải lưu
  active queue ID qua Streamlit rerun để upload/checkbox/widget change không đổi
  case. `SENT` chỉ hiện trạng thái và không có action; `FAILED` cho retry,
  `PARTIAL` cho gửi tiếp. Không hiển thị KPI hoặc bộ lọc state riêng.
- Mỗi case cloaking giữ delivery ledger theo tài khoản gửi + email nhận + draft.
  Chỉ chuyển `SENT` khi mọi tài khoản thuộc phạm vi nguồn đã hoàn tất;
  `PARTIAL` vẫn nằm trong danh sách chờ/selectable và retry phải giữ nguyên các
  delivery `sent`/`already_sent`, mặc định ưu tiên tài khoản còn thiếu. Kết quả
  từng SMTP delivery phải checkpoint ngay để lần gửi gián đoạn không gửi lại
  delivery đã thành công.
- Khi xác nhận cloaking, evidence đã duyệt trong queue là nguồn sự thật; detector
  ở lần tạo draft không tái hiện được tín hiệu không được làm mất evidence này.
  Draft phải ghi kết luận xác nhận cloaking bằng tiếng Anh và chỉ gửi khi có
  manifest + hai ảnh hợp lệ, không rỗng, mỗi file tối đa 10 MB. Chế độ không
  cloaking phải loại evidence/attachment trước khi preview. Preview phải giữ
  fingerprint attachment và chặn gửi nếu file thay đổi trước SMTP.
- Path probe như `/vi-vn/` chỉ dùng khám phá, không cộng điểm cloaking. Verdict
  nội dung nhạy cảm tách khỏi verdict cloaking. Proxy/vantage không được lộ
  credential trong evidence; ảnh thủ công chỉ nâng tối đa `POSSIBLE` và luôn cần
  approve tại Cloaking Review trước khi gửi trực tiếp. Cặp ảnh thủ công đã xác
  nhận phải nâng cả `NO_SIGNAL`/`INCONCLUSIVE` lên `POSSIBLE`; result legacy có
  đủ operator evidence phải được chuẩn hóa khi đọc. Khi chưa đủ cặp ảnh, uploader
  tự mở, preview thumbnail ngay và nút tạo draft xác nhận cloaking phải bị khóa.
  Không dùng nút lưu ảnh riêng: validate toàn bộ batch trước khi ghi file, rồi
  commit evidence cùng thao tác tạo draft; không tự gửi email.
- URLScan đã được loại bỏ khỏi pipeline: không đọc API key, submit/poll, hiển thị
  link/kết quả hoặc tạo attachment từ dịch vụ này ở bất kỳ page/worker/CLI nào.
  Browser Evidence, Wayback và ảnh upload thủ công là các nguồn evidence được hỗ
  trợ; draft legacy còn dấu vết URLScan chỉ được scrub/chặn tại ranh giới gửi.
  Không tự sửa `config.ini` vì đây là file secret cục bộ; khóa cũ nếu còn tồn tại
  sẽ không được ứng dụng đọc.
- Cảnh báo phishing Cloudflare và trang lỗi trình duyệt/DNS là terminal page,
  không phải bằng chứng cloaking. Khi toàn bộ profile terminal, dùng
  `BLOCKED_OR_UNAVAILABLE`, bỏ manual review cloaking và tiếp tục gửi draft;
  không khẳng định domain đã bị thu hồi nếu chưa có WHOIS Hold/link status.
- Draft/email gửi nhà cung cấp phải dùng tiếng Anh; formatter external không
  được lấy nguyên label/detail tiếng Việt từ UI. Chỉ dữ liệu chứng cứ nguyên gốc
  như title hoặc matched keyword được phép giữ ngôn ngữ của website.
- Thống kê hiệu quả report phải chọn đúng một account và khoảng ngày. Page
  `pages/13_General_Statistics.py` là menu duy nhất đồng bộ dữ liệu analytics:
  một thao tác explicit đọc Inbox/Sent/Junk, Sent attachment metadata và phản
  hồi NCC cùng phạm vi, rồi lưu snapshot sanitize theo account + khoảng ngày.
  `report_statistics.py` vẫn là lớp phân tích local, không tự mở IMAP/gửi mail.
  Page `pages/9_Provider_Replies.py` giữ riêng workflow lọc/xem/trả lời NCC.
  Thư Sent chưa khớp delivery log và mail Inbox/Junk không liên quan không được
  tính vào tỷ lệ; snapshot phải whitelist field cần hiển thị và redact lỗi trước
  khi ghi.
  Delivery mới ghi metadata không bí mật (account, Message-ID, recipient, kênh,
  draft/subject, evidence source/count), không ghi body/credential. Reply chỉ
  nối cùng account theo Message-ID/ticket/domain/provider; record thiếu account
  bị loại. Evidence legacy chỉ có thể backfill khi tìm thấy bản copy trong Sent;
  nếu không thì hiển thị `unknown`, không suy đoán.
- Khi làm Streamlit, phải đọc `.agents/skills/developing-with-streamlit/SKILL.md`.
  Không thêm `streamlit.components.v1` mới; ưu tiên native widgets/component v2.
- Khi làm nghiệp vụ takedown, dùng skill
  `.agents/skills/phishing-takedown-tool/SKILL.md`.
- Sau khi sửa `phishing_toolkit.py`, `domain_worker.py`,
  `provider_replies.py`, `report_statistics.py`, `sent_mail_evidence.py`,
  `general_statistics.py`,
  `link_status.py` hoặc `domain_utils.py`, chạy toàn bộ `unittest`.

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

- 2026-09-09 — Gộp analytics email về hai menu rõ vai trò: **Thống kê email**
  chỉ mở/hiển thị tổng Inbox/Thư rác trong ngày; bỏ menu Sent Mail Evidence và
  thay bằng **Thống kê tổng quát**. Một nút sync theo account + khoảng ngày đọc
  Inbox/Sent/Junk, metadata evidence của Sent và phản hồi NCC, rồi tính tỷ lệ
  spam, gửi thành công, phản hồi, gỡ domain và evidence; không đổi workflow
  lọc/trả lời thủ công của Provider Replies, không gửi SMTP và không lưu body,
  credential hoặc bytes ảnh. Chỉ delivery trong `sent_log.csv` mới tính report;
  Sent chưa khớp chỉ enrich evidence, reply Inbox/Junk không liên quan bị loại,
  domain trùng một mình không nối reply, và snapshot whitelist/redact mọi field
  trước khi ghi cache. Thêm bộ đếm IMAP theo khoảng ngày và timeout cho provider
  sync; file chính: `general_statistics.py`, `mail_statistics.py`,
  `sent_mail_evidence.py`, `report_statistics.py`, `provider_replies.py`,
  `pages/11_Mail_Statistics.py`, `pages/13_General_Statistics.py`,
  `streamlit_app.py`, `PhishingTool.spec`, test analytics/AppTest; đã kiểm tra:
  265/265 full unittest, compileall, pip check và diff check; tài liệu:
  `README.md`, `03_Technical_Guide.md`, file này và skill dự án; lưu ý: chỉ mock
  IMAP/SMTP, không đồng bộ mailbox thật trong test.

- 2026-09-08 — Bổ sung thống kê hiệu quả report theo mailbox: mọi luồng gửi
  ghi metadata delivery không bí mật vào `data/sent_log.csv` (account,
  Message-ID, recipient, kênh, draft/subject, evidence source/count); Provider
  Replies follow-up ghi cùng ledger và giữ source message ID. Page **Thống kê
  email** bắt buộc chọn một tài khoản, job/cache Inbox/Sent/Junk được lọc theo
  account và có thêm khoảng ngày phân tích report, bảng evidence tự động/thủ
  công, provider/kênh/subject/draft, outcome và Sent Mail → Provider Replies.
  Cache cùng ngày được merge theo account nên check mail B không xóa kết quả mail A.
  `report_statistics.py` chỉ đọc local artifacts, loại reply thiếu account và
  giữ evidence legacy là `unknown`; không mở IMAP/gửi mail trong bước phân tích.
  File chính: `report_statistics.py`, `phishing_toolkit.py`,
  `provider_replies.py`, `mail_statistics.py`, `email_send_ui.py`,
  `domain_worker.py`, `cloaking_review_sender.py`,
  `pages/9_Provider_Replies.py`, `pages/11_Mail_Statistics.py`, `PhishingTool.spec`, test analytics
  và AppTest; đã kiểm tra: focused/AppTest, 248/248 full unittest, compileall,
  pip check và diff check; tài liệu: `README.md`,
  `03_Technical_Guide.md`, file này và skill dự án; lưu ý: chỉ mock IMAP/SMTP.

- 2026-09-08 — Phase 7.2 loại bỏ hoàn toàn tích hợp URLScan: xóa API
  submit/poll, khóa cấu hình trong `config.example.ini`, field/result khỏi
  pipeline và toàn bộ nút/link/attachment URLScan ở Check Domain, Quick Report
  và Provider Replies. Browser Evidence cùng upload thủ công là nguồn evidence
  duy nhất; draft legacy còn dấu vết scan được scrub/chặn tại ranh giới gửi,
  còn dữ liệu runtime và `config.ini` secret không bị sửa. File chính:
  `phishing_toolkit.py`, `provider_replies.py`, `pages/1_Check_Domain.py`,
  `pages/7_Quick_Report.py`, `pages/9_Provider_Replies.py`,
  `config.example.ini`, test core/UI; đã kiểm tra: 66 test tập trung,
  235/235 full unittest, compileall, pip check và diff check; tài liệu:
  `README.md`, `03_Technical_Guide.md`, file này và skill dự án; lưu ý: chỉ
  mock browser/SMTP, không mở URL hoặc gửi email thật.

- 2026-09-08 — Đồng bộ Browser Evidence nguồn–đích cho Provider Replies và
  Cloaking Review: thêm helper dùng chung ưu tiên hai ảnh URL nguồn/URL đích rồi
  fallback một ảnh nguồn; Provider Replies preview/gửi toàn bộ PNG + manifest và
  narrative DOM-open không claim click. Disposition `not_cloaking` loại evidence
  cloaking nhưng bắt buộc Browser Evidence report thường riêng, hỗ trợ upload 1–3
  ảnh, chèn block tiếng Anh vào draft và khóa fingerprint trước SMTP; file chính:
  `browser_evidence.py`, `provider_replies.py`, `pages/9_Provider_Replies.py`,
  `cloaking_review_sender.py`, `pages/10_Cloaking_Review.py`, test core/UI; đã
  kiểm tra: focused/AppTest, 236/236 full unittest, compileall, pip check và diff
  check; tài liệu: `README.md`, `03_Technical_Guide.md`, file này và skill dự án;
  lưu ý: chỉ mock browser/SMTP, không mở URL hoặc gửi email thật.

- 2026-09-08 — Tách hoàn toàn upload evidence khỏi Domain Worker và hoàn thiện
  Domain Evidence Review: Domain Worker chỉ còn hiển thị số lượng/liên kết; page
  review tạo dry-run preview một lần, hiển thị đúng To/Subject/body theo từng
  account + recipient, rồi gửi chính delivery plan đó sau khi kiểm tra fingerprint
  draft/ảnh. Ledger hiển thị trạng thái từng delivery; retry tự bỏ qua lượt đã gửi
  thành công trong ngày và chỉ xử lý account/recipient còn thiếu; không tạo job mới;
  file chính: `domain_worker.py`, `pages/6_Domain_Worker.py`,
  `pages/12_Domain_Evidence_Review.py`, test worker/UI; đã kiểm tra: focused test,
  AppTest, 231/231 full unittest, compileall, pip check và diff check; tài liệu:
  `README.md`, `03_Technical_Guide.md`, file này và skill dự án; lưu ý: chỉ mock
  SMTP/browser, không mở URL thật hoặc gửi report thật.

- 2026-09-08 — Domain Worker evidence capture: precheck v4 thử mở destination HTTP(S)
  từ control Register/Login trong DOM để chụp ảnh nguồn + đích; nếu không có URL tĩnh
  hoặc mở đích lỗi thì fallback capture thụ động, còn terminal browser/DNS source không
  bị đưa vào manual review. Thêm `pages/12_Domain_Evidence_Review.py` để gộp case
  thiếu ảnh theo full URL/ngày, upload 1–3 ảnh preview ngay và gửi trực tiếp khi worker
  vẫn chạy; gửi thành công đánh dấu các bản ghi trùng ở job khác để không tái xuất hiện;
  file chính: `domain_worker.py`, `browser_evidence.py`,
  `pages/6_Domain_Worker.py`, `pages/12_Domain_Evidence_Review.py`,
  `streamlit_app.py`, test worker/UI; đã kiểm tra: regression focused, AppTest,
  229/229 full unittest, compileall, pip check và diff check; tài liệu: `README.md`,
  `03_Technical_Guide.md`, file này và skill dự án; lưu ý: không mở URL thật hoặc
  gửi SMTP thật.

- 2026-09-08 — Phân loại ba case Browser Evidence: formatter dùng riêng nội dung
  cho control có HTTP(S) destination, control không có URL tĩnh và trang không có
  control auth; capture đếm aggregate field password/OTP/payment/identity đang hiển
  thị mà không đọc value, chỉ đưa claim “capable of collecting” khi có field thật;
  UI Check Domain hiển thị case và các indicator đã quan sát, Domain Worker dùng
  cùng lõi; file chính: `browser_evidence.py`, `pages/1_Check_Domain.py`, test
  evidence/worker/AppTest; đã kiểm tra: test tập trung, full unittest, compileall,
  pip check và diff check; tài liệu: `README.md`, `03_Technical_Guide.md`, file
  này và skill dự án; lưu ý: browser giả/mock, không mở URL nghi ngờ hoặc gửi SMTP.

- 2026-09-08 — Tối ưu nội dung Browser Evidence gửi nhà cung cấp: thay raw block
  “Technical Evidence” bằng phần tố cáo tiếng Anh “Observed Phishing Behavior
  and Supporting Evidence”, nêu control, `href`, bước tái hiện, URL đích/quan hệ
  cần điều tra, attachment và yêu cầu xử lý; phân biệt rõ inspect DOM thụ động với
  mở URL trực tiếp, không tự claim click/credential/OTP/payment; block được chèn
  trước chữ ký và áp dụng qua lõi chung cho Check Domain/Domain Worker; file chính:
  `browser_evidence.py`, `phishing_toolkit.py`, test evidence/worker; đã kiểm tra:
  test tập trung, full unittest, compileall, pip check và diff check; tài liệu:
  `README.md`, `03_Technical_Guide.md`, file này và skill dự án; lưu ý: không mở
  URL nghi ngờ hoặc gửi SMTP thật.

- 2026-09-08 — Browser Evidence Phase 2.1 bỏ click thật tại Check Domain: chế độ
  opt-in đổi thành “Mở URL từ DOM”, chụp trang nguồn rồi mở resolved href trong
  tab mới cùng BrowserContext/referrer để chụp trang đích; manifest/email ghi rõ
  không click, state khóa theo full URL và gate chặn evidence của URL/path khác;
  cập nhật draft theo bước đọc/stage toàn bộ để lỗi draft không gây cập nhật một
  phần; file chính: `browser_evidence.py`, `phishing_toolkit.py`,
  `pages/1_Check_Domain.py`, test core/AppTest; đã kiểm tra: test tập trung,
  full unittest, compileall, pip check và diff check; tài liệu: `README.md`,
  `03_Technical_Guide.md`, file này và skill dự án; lưu ý: chỉ browser giả/mock,
  không mở URL nghi ngờ hoặc gửi SMTP thật.

- 2026-09-08 — Browser Evidence Phase 2 tích hợp Check Domain với verified navigation
  opt-in: expander có lựa chọn Passive DOM/Verified click, tự chụp hai ảnh trước/sau
  control Register/Login an toàn, preview URL trước click/DOM href/URL cuối/redirect
  chain và truyền đúng artifact đã preview cho gửi đơn lẫn gửi tất cả; lỗi fallback
  sang capture thụ động hoặc upload 1–3 ảnh ngay, không có bước lưu riêng. Detector
  cloaking và Domain Worker vẫn thụ động; file chính: `pages/1_Check_Domain.py`,
  `browser_evidence.py`, `phishing_toolkit.py`,
  `tests/test_check_domain_browser_evidence.py`; đã kiểm tra: 7 test page,
  218/218 full unittest, compileall, pip check và AppTest mocked; tài liệu:
  `README.md`, `03_Technical_Guide.md`, file này và skill dự án; lưu ý: chỉ dùng
  browser giả, không mở URL nghi ngờ và không gửi SMTP thật; cơ chế click này đã
  được thay bằng mở URL từ DOM ở Phase 2.1 bên trên.

- 2026-09-08 — Browser Evidence Phase 1 mở rộng với verified navigation tùy chọn
  cho report phishing thường: tự chọn anchor HTTP(S)/button `data-href` Register/
  Login an toàn, chụp `source_before_click` và `destination_after_click`, hỗ trợ
  same-tab/popup, ghi URL cuối + redirect 3xx BrowserContext, tiêu đề/DOM control
  và SHA-256 vào manifest; từ chối control submit/javascript, URL không đổi và
  terminal page, dọn artifact khi lỗi. Detector cloaking và Domain Worker vẫn
  thụ động; Check Domain tích hợp opt-in ở Phase 2; file chính:
  `browser_evidence.py`, `phishing_toolkit.py`,
  `tests/test_browser_evidence.py`, `tests/test_check_domain_browser_evidence.py`;
  đã kiểm tra: 15 test Browser Evidence, 217/217 full unittest, compileall,
  pip check; tài liệu:
  `README.md`, `03_Technical_Guide.md`, file này và skill dự án; lưu ý: chỉ dùng
  browser giả, không mở URL nghi ngờ và không gửi SMTP thật.

- 2026-09-08 — Browser Evidence Phase 4 cho Check Domain/Domain Worker: fallback
  upload 1–3 ảnh PNG/JPEG tạo manifest hash, preview không nút lưu; worker
  preflight v4 capture domain thường, chỉ evidence hợp lệ vào batch và tách lỗi
  sang `evidence_review` để upload/gửi từng URL, giữ evidence khi SMTP lỗi và
  chuyển Cloaking Review nếu check lại có tín hiệu; file chính:
  `browser_evidence.py`, `phishing_toolkit.py`, `domain_worker.py`,
  `pages/1_Check_Domain.py`, `pages/6_Domain_Worker.py`, test core/worker/AppTest;
  đã kiểm tra: focused, 211/211 full unittest, compileall, pip check, spec và diff check;
  tài liệu: `README.md`, `03_Technical_Guide.md`, file này và skill dự án; lưu ý:
  chỉ mock SMTP/browser, không gửi email hoặc mở URL thật.

- 2026-09-08 — Cho phép gửi thủ công case `evidence_review` trong lúc Domain
  Worker còn chạy: bỏ khóa UI theo trạng thái job, thêm lock liên tiến trình và
  claim theo full URL; worker merge preflight không làm mất claim/evidence,
  completed manual send không bị re-add, phiên thứ hai bị chặn; file chính:
  `domain_worker.py`, `pages/6_Domain_Worker.py`, test worker/UI; đã kiểm tra:
  212/212 full unittest, compileall, pip check, spec và diff check; tài liệu:
  `README.md`, `03_Technical_Guide.md`, file này và skill dự án; lưu ý: chỉ mock
  SMTP/browser, không gửi email thật.

- 2026-09-08 — Chuẩn hóa nội dung registrar/registry: web form và email giữ full
  URL/path, bỏ URLScan và VirusTotal không detection, không tự khẳng định thu
  OTP/payment; registrar có 5 Subject và không lặp yêu cầu hold; registry không
  tự nói registrar đã bỏ qua, chỉ nhắc báo cáo trước khi caller xác nhận delivery,
  không chèn raw WHOIS/ICANN/ClientHold mặc định; Quick Report hiển thị text
  copy-ready cho registry; file chính: `phishing_toolkit.py`,
  `pages/7_Quick_Report.py`, test webform/quick report; đã kiểm tra: focused và
  204/204 full unittest; tài liệu: `README.md`, `03_Technical_Guide.md`, file này
  và skill dự án; lưu ý: không đọc secret, không gửi SMTP hoặc submit form thật.

- 2026-09-08 — Làm sạch nội dung web form: GSB/Cloudflare/registrar dùng đúng
  full URL/path, không chèn URLScan hoặc screenshot URLScan và không tự khẳng
  định hành vi lấy OTP/payment khi chưa có quan sát; GSB và Cloudflare có pool
  riêng 5 biến thể, chọn ổn định theo domain + ngày và nhắm đúng browser warning
  so với infrastructure/origin handling; giữ wording điều tra/xác nhận trước khi
  áp dụng policy; file chính: `phishing_toolkit.py`,
  `pages/1_Check_Domain.py`, `pages/7_Quick_Report.py`, test webform; đã kiểm tra:
  6 test tập trung, 198/198 full unittest, compileall, pip check, spec và diff
  check; tài liệu: `README.md`, `03_Technical_Guide.md`,
  file này và skill dự án; lưu ý: không submit web form/API hoặc gửi email thật.

- 2026-09-08 — Khôi phục nguyên trạng Quick Report sau đánh giá Phase 4: sửa
  `run_cdn_check()` còn tham chiếu nhầm biến `target_url` chưa khai báo khiến mọi
  card chỉ báo “Không thể check đầy đủ”; tự invalidate cache runtime lỗi một lần
  sau hot reload; page
  tiếp tục là quick-link web form/API, giữ đầy đủ GSB, SmartScreen, Netcraft,
  Cloudflare/CDN, registrar, TLD registry, Chống Lừa Đảo, Cốc Cốc và URLScan như
  trước; không thêm Browser Evidence, không bắt ảnh/upload và không khóa action;
  file `pages/7_Quick_Report.py` cùng UI form dùng chung đã được đối chiếu khớp
  phiên bản trước thay đổi; thêm regression test bắt pipeline phải trả đủ routing
  field CDN/registrar/registry mà không NameError; đã kiểm tra: 6 test tập trung,
  194/194 full unittest,
  compileall, pip check, spec và diff check; tài liệu: file này; lưu ý: không gọi
  report/API thật.

- 2026-09-08 — Browser Evidence Phase 3 cho Check Domain: thêm capture/preview
  bền qua rerun, chèn evidence tiếng Anh vào mọi draft và truyền đúng PNG +
  manifest cho gửi đơn/gửi tất cả; quality gate chặn thiếu Subject/recipient/full
  URL, placeholder, `NOT flagged`, khối URLScan cũ và attachment lỗi; URLScan chỉ còn hiển thị nội
  bộ, không chèn vào email; file chính: `browser_evidence.py`,
  `phishing_toolkit.py`, `email_send_ui.py`, `pages/1_Check_Domain.py`, test core/UI;
  đã kiểm tra: focused/full unittest, AppTest, compileall, pip check và spec;
  tài liệu: `README.md`, `03_Technical_Guide.md`, file này và skill dự án; lưu ý:
  không mở URL thật hoặc gửi SMTP thật.

- 2026-09-08 — Browser Evidence Phase 2 cho Provider Replies: thay capture DOM
  của page bằng lõi dùng chung, panel ảnh có redirect HTTP, UI giữ evidence qua
  rerun và preview requested/landing/destination; reply đúng thread đính kèm
  PNG + manifest chỉ khi hash còn hợp lệ, upload/URLScan vẫn là fallback tạm;
  file chính: `browser_evidence.py`, `provider_replies.py`,
  `pages/9_Provider_Replies.py`, test provider/UI; đã kiểm tra: focused/full
  unittest, AppTest, compileall và pip check; tài liệu: `README.md`,
  `03_Technical_Guide.md`, file này và skill dự án; lưu ý: chỉ mock SMTP/browser,
  không mở URL hoặc đọc/gửi email thật.

- 2026-09-08 — Browser Evidence Phase 1: thêm lõi capture Playwright chỉ đọc,
  ghi requested/landing URL, redirect HTTP, DOM control/destination vào PNG và
  manifest có SHA-256; loại terminal page, che credential và không tuyên bố
  redirect đã xác minh; chưa đổi page, URLScan hoặc luồng gửi; file chính:
  `browser_evidence.py`, `tests/test_browser_evidence.py`; đã kiểm tra: test tập
  trung, full unittest, compileall và pip check; tài liệu: `README.md`,
  `03_Technical_Guide.md`, file này và skill dự án; lưu ý: test dùng browser giả,
  không mở URL thật và không gửi email.

- 2026-09-01 — Thống kê email chạy nền qua menu: nút Kiểm tra tạo job bền vững
  không chứa credential, process riêng tiếp tục khi chuyển trang/F5, ghi trạng
  thái nguyên tử và cache kết quả để tự nạp khi quay lại; hỗ trợ launcher frozen;
  file chính: `mail_statistics.py`, `pages/11_Mail_Statistics.py`, `launcher.py`,
  test statistics/UI; đã kiểm tra: focused/full unittest, compileall, spec;
  tài liệu: `README.md`, `CLAUDE.md`, `03_Technical_Guide.md`, file này.

- 2026-09-01 — Phản hồi NCC đọc thêm Thư rác: đồng bộ quét Inbox và mailbox
  `\\Junk`/Junk/Spam, lưu nguồn mailbox trên từng email, hiển thị thống kê theo
  thư mục bằng cùng bộ đếm ngày địa phương của Thống kê email, loại trừ Sent và
  cô lập lỗi Junk khỏi Inbox; page tự reload module thống kê cũ và Seen all nhóm
  UID theo mailbox; file chính: `provider_replies.py`, `mail_statistics.py`,
  UID cache không phải số ASCII bị bỏ qua và STORE Seen chia batch 100;
  page có version handshake cho cả module provider để tránh gọi hàm Seen cũ;
  cache phản hồi giữ cả mail không liên quan để Seen all khớp tổng IMAP, còn
  bảng NCC lọc riêng và công khai số bị loại;
  `pages/9_Provider_Replies.py`, test provider; đã kiểm tra: test tập trung,
  full unittest và compileall; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này; lưu ý: chỉ mock IMAP, không đọc mail thật.

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

- 2026-09-01 — Thêm thống kê email theo ngày địa phương: menu mới chỉ đọc IMAP
  sau khi người dùng bấm Kiểm tra, đếm toàn bộ Inbox/Sent/Junk bằng `INTERNALDATE`,
  tự nhận diện thư mục `\\Sent`/`\\Junk`, quote tên mailbox Gmail có khoảng trắng,
  cô lập lỗi từng tài khoản, đánh dấu account thiếu `imap_host` là “Không có trong
  IMAP” mà không kết nối nhầm SMTP host, nhận cả FETCH metadata dạng bytes/tuple,
  loại session result cũ khác schema, hiển thị tổng nhận + thư rác và mặc định hôm nay;
  cache kết quả đã sanitize theo ngày tại `data/mail_statistics_cache.json`, tự
  nạp khi mở trang, cho xóa riêng ngày đang chọn bằng ghi atomic và tự reload core
  khi module version cũ còn bị giữ trong process Streamlit;
  file chính: `mail_statistics.py`, `pages/11_Mail_Statistics.py`,
  `streamlit_app.py`, `PhishingTool.spec`, test core/AppTest/navigation; đã kiểm
  tra: test tập trung, full unittest, compileall và py_compile spec; tài liệu:
  `README.md`, `CLAUDE.md`, `03_Technical_Guide.md`, file này; lưu ý: chỉ mock
  IMAP, không đọc hộp thư thật và không ghi credential.

- 2026-08-29 — Hoàn thiện detector cloaking theo path/profile/vantage: `/vi-vn/`
  404 không còn tạo false positive; tách verdict nội dung khỏi cloaking; HTTP có
  sáu profile + Client Hints/Googlebot, Playwright có ba profile và ba mốc quan
  sát; hỗ trợ vantage proxy không lộ credential, coverage gap fail closed sang
  manual review, upload 2–4 ảnh thủ công ở Check Domain/worker và đính kèm sau
  approve tại Cloaking Review; file chính: `cloaking_detector.py`, `cloaking_ui.py`,
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
  queue đã chuyển sang daily canonical URL, tự archive legacy duplicate (selection
  multi-row ban đầu đã được thay bằng single-row preview trước gửi); đã kiểm tra:
  117/117 unittest, 36 focused
  test, AppTest theo entrypoint chuyển
  Trang chủ/Domain Worker/Cloaking Review, AppTest 5 page, compileall, py_compile
  spec, pip check và diff check; tài liệu: `README.md`,
  `huong-dan-phat-hien-cloaking.md`, `03_Technical_Guide.md`, file này và skill.
- 2026-08-30 — Ảnh bằng chứng cloaking đại diện: bổ sung Playwright Googlebot
  Smartphone, chạy browser capture cả với HTTP `LIKELY`, tự chọn tối đa hai ảnh
  của cặp profile khác biệt mạnh nhất; email sau phê duyệt dùng lại evidence đã
  duyệt và đính kèm manifest + cặp ảnh, không gửi toàn bộ ảnh quan sát; file chính:
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
- 2026-08-31 — Precheck cloaking sớm và tách tiến trình gửi: nút Domain Worker
  chạy lookup recipient + detector cloaking đồng thời, ghi preflight v3 tăng dần
  và enqueue case ngay khi từng URL hoàn tất; chỉ domain thường vào job gửi,
  còn Cloaking Review độc lập nên có thể duyệt trong lúc precheck/worker thường
  đang chạy (job gửi review ban đầu đã được thay bằng gửi trực tiếp); file chính:
  `domain_worker.py`, `pages/6_Domain_Worker.py`,
  `pages/10_Cloaking_Review.py`, test worker/UI; đã kiểm tra: regression đồng
  thời/queue sớm/legacy lock, AppTest hai page và 137/137 full unittest; tài liệu:
  `README.md`, `03_Technical_Guide.md`,
  `huong-dan-phat-hien-cloaking.md`, file này và skill dự án; lưu ý: không gửi
  SMTP thật, không đọc/hiển thị secret runtime; skill đã kiểm tra thủ công vì
  `quick_validate.py` thiếu dependency `PyYAML` trong môi trường.
- 2026-08-31 — Thu gọn trạng thái cloaking trên Quick Report: bỏ hai callout
  Cloaking/Nội dung bị lặp bên ngoài expander, đưa verdict và điểm vào nhãn
  “Chi tiết kiểm tra cloaking”, chi tiết chỉ render một lần bên trong; file chính:
  `cloaking_ui.py`, `pages/7_Quick_Report.py`, `tests/test_cloaking_ui.py`; đã
  kiểm tra: 2 test UI tập trung, Streamlit AppTest, 138/138 full unittest và
  compileall; tài liệu: `README.md`, file này; lưu ý: không đổi detector,
  verdict, Playwright hay workflow gửi mail.
- 2026-09-01 — Cloaking Review preview, gửi trực tiếp và upload liền mạch: bỏ việc tạo/launch
  job gửi khỏi page, xử lý từng case với exact draft preview; xác nhận cloaking
  dùng evidence đã duyệt và bắt buộc manifest + hai ảnh hợp lệ, còn không
  cloaking loại sạch evidence/attachment; checkpoint từng delivery giữ retry an
  toàn và ledger nhiều account; bổ sung fallback upload tự mở, sửa
  `INCONCLUSIVE` + cặp ảnh thủ công thành `POSSIBLE`, chuẩn hóa case legacy và
  thu nhỏ ảnh operator; uploader mới giữ nguyên active case qua rerun, hiển thị
  thumbnail ngay, validate toàn bộ batch trước khi ghi và gộp lưu evidence vào
  nút tạo draft, không còn nút lưu trung gian; UI cuối chỉ còn một bảng case của
  hôm nay với action theo dòng, trạng thái gửi thành công/thất bại/partial và
  không cho mở lại `SENT`; page có fallback đọc JSON/lọc ngày nội bộ nếu tiến
  trình Streamlit còn cache module queue cũ sau hot reload, tránh lỗi thiếu
  `current_review_day`; file chính: `cloaking_review_sender.py`,
  `cloaking_review_queue.py`, `pages/10_Cloaking_Review.py`,
  `cloaking_detector.py`, `phishing_toolkit.py`,
  `domain_worker.py`, `pages/6_Domain_Worker.py`, `PhishingTool.spec`, test
  sender/UI/detector; đã kiểm tra: AppTest upload/thumbnail/state/commit,
  checkbox giữ active case, bảng sent/failed/partial, daily filter và mô phỏng
  module cũ bị cache, 153/153
  full unittest gồm AppTest, py_compile spec, compileall, pip check và diff
  check; tài liệu: `README.md`,
  `03_Technical_Guide.md`, `huong-dan-phat-hien-cloaking.md`, file này và skill
  dự án; lưu ý: chỉ mock SMTP, không gửi email thật, job review cũ chỉ còn để
  migrate/sync lịch sử; validator skill tự động không chạy được vì môi trường
  thiếu `PyYAML`, frontmatter và phạm vi invariant đã được kiểm tra thủ công.
- 2026-09-01 — Chặn Cloaking Review không có recipient: precheck vẫn chạy detector
  cho mọi full URL nhưng chỉ enqueue case cloaking có email nhận hợp lệ; case
  không email vào `excluded_no_email`/log trong ngày, migration không tạo lại
  record đó; Domain Worker chỉ đếm case có email của hôm nay và cả hai page ẩn
  record legacy không recipient; file chính: `domain_worker.py`,
  `cloaking_review_queue.py`, `pages/6_Domain_Worker.py`,
  `pages/10_Cloaking_Review.py`, test worker/queue/AppTest; đã kiểm tra: 59 test
  tập trung và 157/157 full unittest; tài liệu: `README.md`,
  `03_Technical_Guide.md`, `huong-dan-phat-hien-cloaking.md`, file này và skill
  dự án; lưu ý: không sửa/xóa cache hoặc queue runtime và không gửi SMTP thật.

## Baseline chất lượng hiện tại

Nhóm `link_status` đã thống nhất Cloudflare warning/HTTP 403 là `BLOCKED`, không
phải `LIVE` hay `DIE`; mock response không iterable được xử lý an toàn. Toàn bộ
test phải xanh trước khi bàn giao thay đổi lõi. Baseline hiện tại là 265 test.
Detector cloaking có test thuần cho scoring/profile/path/vantage, fake browser
cho Playwright và mock attachment worker; không dùng URL nghi ngờ hay SMTP thật
trong test.
