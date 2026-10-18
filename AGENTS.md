# Domain Check Tool — hướng dẫn dự án cho Codex

## Mục tiêu

Đây là công cụ phòng thủ nội bộ để điều tra domain nghi phishing và chuẩn bị
báo cáo takedown hợp pháp. Dự án có CLI, giao diện Streamlit cục bộ, worker
xử lý batch, gửi SMTP và theo dõi phản hồi nhà cung cấp qua IMAP.

## Bản đồ dự án

- `backend/compat/`: source canonical của `phishing_toolkit.py`,
  `cloaking_detector.py`, `browser_evidence.py`, `quick_report_actions.py`, `provider_replies.py`.
  Các file cùng tên ở root chỉ còn wrapper tương thích legacy. Khi sửa lõi,
  sửa tại backend/compat và chạy cả legacy/backend test; chưa xóa root runtime.

- `phishing_toolkit.py`: lõi nghiệp vụ và CLI (`check`, `related`,
  `brandscan`, `send`); thực hiện enrichment domain, draft report, evidence và
  helper SMTP.
- `streamlit_app.py`, `streamlit_home.py`, `pages/`: điểm vào và các luồng UI
  Streamlit.
- `domain_worker.py`: precheck email/cloaking và worker batch nền có resume cho
  domain thường; job lưu tại `data/worker_jobs/`.
- `backend/src/domaincheck/{core/worker_precheck,application/domain_worker,api/domain_worker}.py`,
  `frontend/src/features/domain-worker/`: DW-01 web precheck owned, queue PostgreSQL,
  draft email tham khảo; CLI `--job-type domain_worker_precheck` hoặc
  `run_domain_worker_precheck.bat`. `application/domain_worker_preview.py` tạo exact
  email preview cho ready theo account/recipient, private immutable artifacts,
  logo/images/manifest và version/hash bindings. `application/worker_send.py` chạy
  SMTP batch normal; `application/worker_batches.py` giữ lifecycle/owner lock,
  lọc lịch sử trước precheck và workspace recovery/history API. Cloaking chưa send web.
  `application/worker_review.py` và React `ReviewQueuePage.tsx` phục vụ queue owned
  trong ngày. `application/evidence_review.py`, `api/evidence_review.py` và React
  `EvidenceReviewPage.tsx` phục vụ ảnh thủ công/exact preview/gửi SMTP trực tiếp,
  retry ledger chung; `application/delivery_dispatch.py` chia sẻ SMTP/checkpoint/Sent
  với Domain Worker. Review không tạo worker job, không nới owner/day/account scope.
- `lead_triage.py`, `pages/15_Lead_Triage.py`: tách ghi chú hạ tầng được dán,
  đối chiếu DNS/IP, dùng precheck/pipeline/report Domain Worker, thêm backend
  context, capture DOM chung Phản hồi NCC và gửi report đã duyệt qua SMTP;
  bản dịch tiếng Việt chỉ phục vụ đối chiếu; snapshot review cục bộ ở
  `data/lead_triage_cache.json`.
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
- `backend/src/domaincheck/{api,application}/cloudflare_worker.py`,
  `application/cloudflare_runner.py`, `domaincheck/cloudflare_runner.py` and
  `frontend/src/features/cloudflare-worker/`: owned Dashboard Cookie web batches.
  Account ID is in Mail settings; run `run_cloudflare_worker.bat` alongside API.
  Cookie stays in a private runner RAM session; durable metadata uses existing
  CloudflareRecord/ReviewSnapshot, private report payloads and safe checkpoints.
- `cloudflare_form_worker.py`, `cloudflare_profile_bridge.py`,
  `cloudflare_abuse_api.py`,
  `chrome_extension/cloudflare-profile-worker/`, `pages/14_Cloudflare_Form_Worker.py`:
  worker form Cloudflare, ledger ngày và extension chạy trên Chrome profile thật.
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

Web backend cài Playwright qua `backend/pyproject.toml`; khi bật capture, cài Chromium
bằng `.\backend\.venv\Scripts\python.exe -m playwright install chromium`. Worker dùng
đúng backend venv và kiểm tra launch browser trắng trước khi claim job. Admin và user
đều sửa được permission/tên/mô tả trong UI/API; role save tự bổ sung code permission
đã đăng ký nhưng thiếu trong DB. Sync thường giữ các lựa chọn này cho cả hai role.
API khởi động tự seed catalog sau migration, trước nhận request; lock PostgreSQL
chung với CLI chống seed đồng thời. Quyền mới chỉ thêm vào catalog, không tự grant
cho role cũ. Chỉ dọn mã obsolete trong RETIRED_PERMISSION_CODES và binding của mã
đó, không reset quyền hợp lệ hoặc xóa permission lạ. Catalog hiện có 52 mã.
Audit Log web ở api/application/audit_log.py và frontend/features/audit-log,
migration 0005_audit_log. Cá nhân cần audit_logs.view; toàn hệ thống/vô danh
cần thêm view_all, không mặc định cho user. Auth/session, job, SMTP và HTTP
command có log riêng; HTTP accepted không đồng nghĩa gửi thành công.

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

- 2026-10-18 — Sửa badge cột Kết quả Audit Log bị CSS bảng ghi đè thành block/màu chữ muted: dùng StatusBadge chung, giữ nhãn audit và phân biệt tone thành công/tiếp nhận/lỗi/chưa rõ.

- 2026-10-18 — Sửa Domain Worker tự khôi phục batch hôm trước: workspace fallback chỉ chọn ngày hiện tại theo timezone account, FE loại URL/session pointer cũ và cập nhật khi qua ngày. Lịch sử mở explicit/read-only, giữ active lock và ledger; không xóa runtime hoặc chạy precheck/gửi thật. FE Domain Worker 24/24 và backend regression 3/3 đạt; backend full 147 đạt/173 skip do chưa cấu hình PostgreSQL cô lập; legacy 420 giữ 4 failure/7 error baseline. Build/typecheck/lint/compileall/diff check đạt.

- 2026-10-18 — Chuyển toast toàn bộ web từ góc phải dưới lên góc phải trên bằng cấu hình AppToaster; giữ hành vi/thời lượng thông báo.

- 2026-10-18 — Quick Report chuyển copy URL sát cạnh URL, chỉ hiển thị icon có tooltip; giữ badge trạng thái/cache riêng, không đổi nghiệp vụ.

- 2026-10-18 — Quick Report thêm tải ZIP Web Form Assistant và thông báo cập nhật theo version handshake/manifest. Source vẫn ở chrome_extension/cloudflare-profile-worker; FE tooling đóng gói khi build/dev, deploy cả dist/downloads. Cài/cập nhật thủ công trong Chrome; không đổi report/submit. Focused FE 31/31, build/typecheck/lint/compileall/pip check đạt; ZIP 20 file khớp source, đủ entrypoint và CRC hợp lệ.

- 2026-10-18 — Thêm favicon SVG khiên xanh/dấu kiểm cho web, khai báo tại frontend/index.html; asset tĩnh qua Vite public, không đổi nghiệp vụ.

- 2026-10-18 — Check Link Status dùng icon copy gọn và ba bộ đếm Lỗi DNS/Domain Hold/CF Phishing trên bảng; đếm toàn bộ kết quả lần check hiện tại, cập nhật theo stream và giữ tổng khi lọc. Không đổi backend/probe.

- 2026-10-18 — Thêm Copy cạnh URL kết quả và URL chuyển hướng trong Check Link Status web, dùng CopyButton chung; giữ link mở tab mới, không đổi backend/probe.

- 2026-10-18 — Đồng bộ UI lỗi phiên đăng nhập, 403 và 404 bằng StatusPage dùng chung; hỗ trợ sáng/tối/mobile, thử lại phiên và điều hướng Dashboard. Route lạ đi qua AppShell trước khi hiện 404; giữ kiểm tra quyền tại từng page. Frontend 142/142 đạt; build/typecheck/lint và compileall đạt. Chưa kiểm tra trực quan bằng trình duyệt.

- 2026-10-18 — Audit Log web với ba tab, lọc/phân trang/chi tiết và own/all scope.
  Migration 0005 giữ log cũ, thêm anonymous và event_key chống lặp phiên; catalog52.
  Login/logout/revoke/expiry quan sát, command và kết quả job/SMTP có audit metadata
  allowlist; không body/token/Cookie/password/IP/UA. Link Status chỉ audit count,
  không lưu URL/kết quả. Không tự grant role cũ hoặc xóa/backfill lịch sử.
  Chi tiết: docs/testing/audit-log-web-2026-10-18.md.
  PostgreSQL sạch: 308 đạt/2 skip (310 test); audit 9/9; FE 129/129. Build/typecheck/
  lint/compileall/pip check đạt; fixture desktop/mobile không JS error/tràn trang.
  Legacy 420 giữ 4 failure/7 error baseline; migration/seed đã áp dụng, API 200.

- 2026-10-18 — Dọn 7 permission không có chức năng web: check_domain.preview/send,
  link_status.export và lead_triage.view/check/preview/send. Catalog/DB còn 50 mã.
  Seed dọn binding obsolete, giữ grant hợp lệ/session/worker authz; tự chạy khi
  API khởi động sau migration, transaction + advisory lock chung với CLI. Deploy
  mới seed role mặc định; deploy sau tự đăng ký mã mới, không tự grant role cũ.
  Không sửa migration lịch sử, không gửi mail/report hoặc đổi runtime nghiệp vụ.
  PostgreSQL sạch + smoke deploy đầu/restart: 299 đạt/2 skip (301 test);
  legacy 420 giữ 4 failure/7 error baseline; compileall/pip check/diff check đạt.
  Đã sync DB hiện tại về 50 mã, retired còn 0; API health 200.

- 2026-10-18 — Audit permission web: DB đủ 57/57 mã, không đổi grant/role.
  FE sửa Gán role cần roles.view, guard submit Check Domain và link cấu hình mail
  Cloudflare. Artifact draft cần view + preview (Quick Report: submit_netcraft),
  chặn tải trực tiếp vượt preview redaction. Regression 97 route/action + CSRF,
  11 direct page và 7 loại draft; FE 125/125. Chi tiết phạm vi/chức năng dự phòng:
  docs/testing/permission-audit-2026-10-18.md. Không thao tác mail/report thật.
  Backend PostgreSQL sạch: 295 đạt/2 skip (297 test); legacy 420 giữ baseline
  4 failure/7 error. Build/typecheck/lint/compileall/diff check đạt.

- 2026-10-18 — Đổi nhãn menu “Tài khoản gửi mail” thành “Cấu hình mail”
  trên sidebar/mobile; giữ route /settings/mail và quyền hiện tại.

- 2026-10-18 — Sidebar/mobile navigation dùng icon riêng cho từng menu:
  ScanEye cho Cloaking Review, Images cho Evidence Review, CloudCog cho
  Cloudflare Worker, Link2 cho Link Status và Reply cho Phản hồi NCC.
  Thay đổi hiển thị, giữ route/quyền; không đổi backend.

- 2026-10-18 — Check Link Status có cột Chuyển hướng riêng: mã 3xx và link
  URL đích mở tab mới cho từng hop, không có hop hiện “—”; HTTP giữ mã cuối.
  Chỉ đổi frontend, dùng dữ liệu redirect hiện có, không đổi backend/probe.

- 2026-10-18 — URL trong bảng Check Link Status web mở tab mới khi nhấn,
  dùng target=_blank và rel=noopener noreferrer. Chỉ đổi UI, không đổi probe/backend.

- 2026-10-18 — Sửa Check Link Status báo lỗi DNS giả khi DNS trực tiếp timeout.
  A/AAAA và guard HTTP/RDAP dùng DNS hệ thống với deadline 3s; vẫn chặn IP
  nội bộ/mixed và kiểm tra từng redirect. DNS riêng chỉ fallback khi OS lỗi.
  URL mẫu được check theo yêu cầu: DNS_OK, HTTP 301 rồi HTTP 200 ở URL đích.
  Focused backend 16/16; compileall đạt; legacy 420 giữ 4 failure/7 error.
  Không gửi report/mail, không sửa config.ini hoặc dữ liệu runtime.

- 2026-10-18 — Bỏ nút và toàn bộ logic xuất CSV tại Check Link Status web theo
  yêu cầu; giữ kiểm tra tạm thời và bộ lọc trạng thái. Không đổi backend/runtime.

- 2026-10-18 — Check Link Status chuyển sang request-local NDJSON stream, không
  lưu/đọc kết quả DB hoặc workspace theo người dùng; F5/rời trang xóa kết quả tạm.
  Bỏ giới hạn 200 URL và 100.000 ký tự; mỗi lần check lấy quan sát mới. Executor
  chung tối đa 5 probe/API process, chỉ 5 worker/request và queue giới hạn,
  ngừng schedule khi disconnect. Filter giữ nguyên; CSV tạo tại browser theo
  đúng dòng đang lọc. Giữ auth/quyền/CSRF, không xóa snapshot runtime cũ.
  Backend PostgreSQL sạch: 290 đạt/1 Redis skip (291 test), wheel 3/3;
  frontend 113/113, focused backend 12/12, frontend 4/4. lint/typecheck/build/
  compileall/pip check đạt; QA fixture desktop/mobile, F5 xóa kết quả. Legacy
  420 giữ baseline 4 failure/7 error. Không probe/gửi report thật.

- 2026-10-18 — Check Link Status web: owned snapshot/progress, RDAP/WHOIS + DNS +
  HTTP tách lớp; PSL offline tldextract 5.3.0; CF abuse warning/challenge/access,
  URL 404/410 và lỗi hạ tầng phân biệt. React có filter tên miền/website kết hợp,
  filtered CSV bind run ID, F5 và explicit recheck. Bounded subprocess 45s,
  concurrency 5/API process, stale 90s; restart giữ partial và cần check lại.
  Không migration/mail/Cookie hoặc sửa runtime legacy. Báo cáo kiểm thử tại
  docs/testing/link-status-web-2026-10-18.md.
  PostgreSQL sạch + wheel: 288 đạt/1 Redis skip (289 test), wheel 3/3;
  frontend 113/113, riêng workflow 4/4; lint/typecheck/build/compileall/pip check
  đạt. QA fixture desktop/mobile không lỗi/tràn ngang toàn trang. Legacy 420
  giữ baseline 4 failure/7 error; không probe URL nghi vấn hoặc gửi report thật.

- 2026-10-18 - Read-only Cloudflare identity audit: recent batch Account ID
  matches new settings; report email comes from owner profile. Added transport
  regression for new Cookie/account with unchanged reporter email; core 5/5 passed.
  No production behavior, runtime records or credentials changed.


- 2026-10-18 - Fixed Account ID priority: unreadable/missing curr-account cannot
  block a valid configured ID. Readable differing IDs/batch binding still block.
  Canonical parser supports bounded wrappers and explicit/nested account fields;
  avoids confusing user IDs and requires exact 32-hex configuration. No real POST.
  Backend clean PostgreSQL + fresh wheel: 277 passed/1 Redis skip (278 tests),
  including 21 Cloudflare tests; legacy 420 retains 4 failure/7 error baseline.
  Wheel smoke, compileall, pip check and diff check passed; idle runner reloaded.


- 2026-10-18 - Cloudflare runner uses explicit error codes for missing/invalid
  Cookie account, settings mismatch and batch mismatch. Fresh precheck permits
  a different valid account when settings are empty; old batch binding remains.
  Read-only live metadata inspected, idle runner reloaded; no real submission.
  Full isolated backend 274 passed/2 skip (276 tests), including all 19 Cloudflare
  regressions; compileall/diff check passed. No frontend behavior changed.


- 2026-10-18 - Cloudflare precheck has measured progress/current URL/completion
  and indeterminate preparation. Send panel shows bound Account ID; Cookie fallback
  remains intentional. Regression: no configured/cookie account means zero requests.
  Focused backend 17/17 and frontend 9/9 passed; no live provider submission.


- 2026-10-18 - Cloudflare Worker web: owned Cookie Dashboard batches, Account ID
  in Mail settings, dedicated private RAM-session runner and durable checkpoints.
  Shared annotation parser, immutable report/profile bindings, current permissions,
  pause/cancel/retry/reload/day recovery, accepted/unknown protection. No DB
  migration/new dependency; legacy Dashboard/API now canonical in backend/compat.
  Launch run_cloudflare_worker.bat; default private port 8789. No real submission.
  Clean backend + wheel: 272 passed/1 Redis skip (273 tests); frontend 106/106;
  lint/typecheck/build/compileall/pip check passed. Legacy 420 keeps 4 failures/
  7 errors baseline. Browser fixtures 1440px/390px passed without page overflow.
  Report: docs/testing/cloudflare-worker-web-2026-10-18.md.


- 2026-10-17 - Provider Replies web uses dynamic per-account reply_mailboxes,
  add/remove folder rows, exact names and message-based provider detection. Legacy
  selection remains until explicit save; Sent separate, totals dynamic, no migration.
  Backend isolated PostgreSQL: 255 passed/2 skipped; frontend 99 existing + 1 new
  test passed; lint/build/compileall passed. Legacy 420 retains baseline
  4 failures/7 errors under system Python. Backend venv lacks legacy dependencies.
  No live mail access or sending.


- 2026-10-17 - Provider Replies list adds status color badges and decorative icons,
  retaining labels and reduced-motion support. UI only, no mail/backend changes.


- 2026-10-17 — Phản hồi NCC giữ dòng sau gửi, cập nhật cache trạng thái tại chỗ
  rồi đối chiếu backend; filter needs_reply không loại sent/replied_in_sent.
  Email gốc đóng mặc định. Capture có show_browser mặc định true riêng page này,
  mở Chromium cô lập trên desktop backend; worker/review khác giữ headless cũ.
  Child capture giữ reason code an toàn để hiện timeout/thiếu browser/lỗi điều
  hướng thay vì lỗi chung; giới hạn 180 giây, không lộ stderr hay dùng profile cá nhân.
  FE workflow 7/7 và adapter/mail diagnostics 12/12 đạt, typecheck/lint/build/
  compileall đạt; smoke browser headed about:blank và UI desktop/mobile fixture đạt.
  Không migration/dependency mới; không mail/URL nghi vấn thật.
  Full backend PostgreSQL sạch 254 đạt/2 skip (256 test); legacy 420 giữ baseline
  4 failure/7 error sau chạy lại. Không có lỗi mới trong các test liên quan.

- 2026-10-17 — Phản hồi NCC: tiến độ IMAP thực theo thư mục/số thư qua owned
  snapshot và GET sync-progress, phục hồi qua reload; account lock vẫn giữ,
  run ID cũ không ghi đè mới, completed sau commit, stale báo gián đoạn.
  Mặc định lọc Cloudflare/cần phản hồi; NCC khác chỉ xem. Bỏ gate phải có
  delivery lịch sử cho Cloudflare hợp lệ ở capture/preview/send; giữ thread,
  source account, evidence/hash, explicit approval và UNKNOWN ledger.
  Nút DOM capture hiện kèm lý do thiếu URL/quyền/flag, preview ảnh/control URL.
  Backend PostgreSQL sạch 252 đạt/2 skip; riêng workflow 15/15; legacy 420 giữ
  baseline 4 failure/7 error. Visual QA desktop/mobile bằng fixture, không
  SMTP/IMAP/probe thật; không migration/dependency mới.
  Frontend 99/99 đạt khi chạy tuần tự sau timeout do tải song song; workflow
  7/7, typecheck/lint/build/compileall đạt.

- 2026-10-17 — Phản hồi NCC: giới hạn chiều cao bảng, sticky header, mở case tự
  đến phần xử lý và có nút về danh sách giữ draft; polling không cuộn lại.
  Regression danh sách 30 case kiểm tra focus và giữ draft; không đổi backend/mail.

- 2026-10-17 — Provider Replies web: owned IMAP sync theo account/ngày và folder,
  pure parser/draft legacy canonical chuyển vào backend/compat (root wrapper giữ),
  source-match/thread, seen UIDVALIDITY, original body private artifact, existing
  ReviewCase/Snapshot metadata. Exact preview + upload/capture + legal gate, threaded
  shared MIME/SMTP/Sent/ledger, retry failed và UNKNOWN riêng; reconcile header Sent
  không suy diễn delivery. React menu/trang giữ case, compact layout và partial errors.
  Không migration/dependency/permission mới, không SMTP/IMAP/probe thật.
    Backend PostgreSQL sạch 250 đạt/2 skip (252 test), Provider Replies 13/13,
    FE 96/96; lint/typecheck/build/compileall/pip check và wheel 3/3 đạt.
    Visual QA desktop/mobile bằng fixture; API health 200, route protected 401.
    Legacy 420 giữ baseline 4 failure/7 error. Chạy full backend phải dùng DB
    test sạch: dùng lại DB còn fixture gây sai test admin/queue toàn cục.

- 2026-10-17 — Rà soát tổng luồng bằng DB cô lập/mock: thêm journey test ghép
  precheck, batch thường, hai Review, hai account, partial retry và lọc input lại.
  Phát hiện/sửa route discovery bỏ qua Check cloaking_review/evidence_review dù
  ledger đã sent; nay lọc theo đủ route/account chung, giữ phần thiếu và scope ngày.
  Báo cáo tại docs/testing/flow-audit-2026-10-17.md; không mail/probe/runtime thật.
  Backend DB sạch 237 đạt/2 skip; journey + send 28/28; FE 92/92; wheel 3/3;
  lint/typecheck/build/compileall/pip check đạt. Legacy 420 giữ 4 failure/7 error
  khi chạy lại; lần đầu thêm lỗi lock CSV chập chờn trên Windows, đã ghi báo cáo.

- 2026-10-16 — Điều tra trực tiếp slotcatalog.co: 4 profile browser HTTP 200,
  text similarity 0.997, LIKELY từ IOC đơn profile khiến selector bỏ hết ảnh.
  Canonical selector nay giữ cặp comparison_only từ profile khỏe/nonterminal,
  không tự khẳng định khác biệt; Review có ghi chú và clear lỗi capture cũ.
  Đã nhập hai ảnh chụp thực tế vào đúng owned case, không tạo draft/gửi email.
  FE 92/92, backend PostgreSQL 236 đạt/2 skip, wheel cô lập 3/3;
  lint/typecheck/build/compileall/pip check đạt. Legacy 420 giữ baseline
  4 failure/7 error; regression selector mới đạt. Reinstall backend và reload
  worker lúc idle; không sửa config.ini hoặc thay DNS/proxy.

- 2026-10-16 — Sửa thiếu capture cloaking trong precheck web: worker tự chụp
  candidate bằng detector passive cũ sau HTTP/contact, kể cả LIKELY; no-email
  không chụp. Giữ cặp profile khác biệt mạnh nhất + ảnh khác nội bộ, Review hiện
  sẵn ảnh/tên profile và nút chụp lại; không auto approve/send. Capture ngoài DB
  transaction, lỗi giữ queue và thông báo, không mất evidence đã duyệt.
  Backend PostgreSQL sạch 236 đạt/2 skip; cloaking 15/15; FE 92/92 (2 workers);
  wheel 3/3, lint/typecheck/build/compileall đạt. Legacy 419 giữ 4 failure/7 error.
  Worker all/precheck đã reload lúc idle; không probe URL/gửi mail thật.

- 2026-10-16 — Cloaking Review web triển khai current-day owned detail/images,
  ba quyết định, upload atomic hai ảnh đối chiếu hoặc 1–3 ảnh report thường riêng,
  exact preview + approve/send sync, skip, passive capture process riêng với ảnh
  candidate chờ duyệt. Tái dùng preview/MIME/SMTP/Sent/ledger Domain Worker;
  giữ sent/unknown, case lock, partial retry và original detector observations.
  Không migration, dependency hoặc permission mới; không gửi/probe thật.
  FE 91/91; backend PostgreSQL sạch 231 đạt/2 skip; Cloaking Review 12/12 gồm
  regression nguồn HTTP mới, capture subprocess và ảnh nội bộ không attach.
  Wheel cô lập 3/3, typecheck/lint/build/compileall/pip check đạt. Browser fixture
  desktop 1440px/mobile 390px không lỗi/tràn ngang. Legacy 419 giữ 4 failure/7 error.

- 2026-10-16 — Sửa Evidence Review legacy ẩn tài khoản sau preview đầu: source
  jobs đều không ghi scope thì tiếp tục hiện mọi owned enabled account, preview
  explicit bổ sung union scope, giữ ledger; source job có scope vẫn giới hạn cũ.
  Backend PostgreSQL sạch 221 đạt/2 skip, Evidence Review 10/10; compileall đạt.
  Legacy 419 giữ baseline 4 failure/7 error. Không migration hoặc SMTP thật.

- 2026-10-16 — Evidence Review sửa lỗi case legacy thiếu required_account_ids:
  cho mở case, chọn explicit tài khoản enabled thuộc owner và bind tại preview
  dưới case lock. Không chạy precheck lại, không suy đoán default account; scope
  đã lưu tiếp tục bắt buộc, giữ exact preview/hash và ledger chống gửi trùng.
  Xác nhận case thực tế mở được, có 2 tài khoản owned để chọn; không gửi thật.
  Backend 220 đạt/2 skip, FE 88/88, lint/typecheck/build/compileall đạt;
  legacy 419 giữ baseline 4 failure/7 error. Không migration/cấu hình mới.

- 2026-10-16 — Domain Evidence Review web đối chiếu legacy: case ngày/full URL,
  1–3 ảnh thumbnail/atomic, tài khoản nguồn, exact preview rồi gửi SMTP sync không
  job. Shared dispatcher/ledger/Message-ID/Sent, partial retry giữ sent/unknown;
  scoped advisory lock, owned image route, reset confirmation khi sửa ảnh/account,
  reload preview/images/deliveries. Sent đủ nguồn mới ẩn case; source ready không
  treo batch nhờ workspace đối chiếu ledger, không sửa source check/version khi đọc
  trạng thái để giữ binding của exact preview/Sent. Không migration/dependency/config
  mới, không email/probe thật.
  FE 88/88; backend PostgreSQL cô lập 219 đạt/2 skip, wheel cô lập mới 3/3;
  lint/typecheck/build/compileall/pip check đạt. Legacy 419 giữ baseline
  4 failure/7 error. Browser fixture desktop/mobile 390px không tràn ngang trang;
  reload worker lúc idle, API health 200.

- 2026-10-16 — Thu gọn account cell: chỉ email một lần, badge/sent counts cùng
  dòng, ẩn zero errors/unknown và bỏ Chi tiết gửi/khung card. Giữ nguyên metadata
  delivery/API/ledger và trạng thái từng account, không đổi nghiệp vụ SMTP.
  FE 85/85 với regression label=email không lặp, per-account partial/retry/reload;
  lint/typecheck/build/compileall đạt, không gửi mail thật.

- 2026-10-16 — Sửa send-summary 409 sau pause: chuyển control-mode guard khỏi
  summary sang _start. Pause/cancel đọc tổng kết/ledger bình thường, giữ chặn
  start sai trạng thái. Regression đọc summary không đổi mode/counts và kiểm tra
  read-only source lỗi thực tế đạt; không gửi mail hoặc sửa runtime/job hiện tại.
  Backend PostgreSQL cô lập 211 đạt/2 skip; focused pause/cancel summary + chặn
  start đạt. Legacy 419 giữ baseline 4 failure/7 error; compileall và API health
  đạt. Không migration/config mới.

- 2026-10-16 — Thu gọn Theo dõi gửi email thành flex-wrap strip chung batch,
  domain progress, email counters và countdown compact; giảm heading/padding,
  bỏ lịch batch tiếp theo lặp dòng. Không đổi nghiệp vụ/job/timer lifecycle.
  FE 85/85, focused 22/22 với countdown compact; lint/typecheck/build/compileall
  đạt. Không thay backend/database hoặc gửi mail thật.

- 2026-10-16 — Gộp UI precheck/send trong một vùng và một bảng domain; khi gửi
  thu KPI precheck vào details, chỉ nhóm thống kê email/batch. Chi tiết lỗi, số lần
  thử và Message-ID nằm trong account cell; bỏ bảng SMTP thứ hai. Observer cache
  send-summary giúp đồng bộ ID trước workspace polling, không thêm request/API.
  FE 85/85; regression một table/ẩn KPI precheck khi gửi, per-account retry,
  details lỗi/Message-ID, pause/reload và lịch sử đạt. lint/typecheck/build/
  compileall đạt. Không sửa backend/database/SMTP trong thay đổi UI này.

- 2026-10-16 — Gửi batch tách Tạm dừng/Tiếp tục/Ngừng hẳn qua control_mode JSON,
  reuse cooperative stop checkpoint, pause giữ owner workspace và khôi phục F5;
  cancel chặn resume/retry/start source đó, giữ ledger/pending/history. /pause alias
  /stop, /cancel cùng permission stop+CSRF; không schema/config mới, không gửi thật.
  FE 85/85; backend PostgreSQL cô lập 211 đạt/2 skip, gồm pause/resume/checkpoint
  in-flight cancel và chặn restart. lint/typecheck/build/compileall đạt; legacy
  419 giữ baseline 4 failure/7 error. API health 200.

- 2026-10-16 — Bảng ready cập nhật delivery theo job query dùng chung; trạng thái
  domain + khối kết quả từng account (tên/email, đã gửi/tổng, lỗi/chưa rõ), KPI sent
  chỉ tăng khi mọi account/recipient/channel SENT. Start/retry/reload giữ nguồn DB;
  missing delivery không được suy ra sent. Chi tiết delivery có Gửi từ rõ ràng.
  Không đổi API/database/SMTP. FE 84/84, gồm hai account/hai recipient, partial→retry
  complete/KPI/reload; lint/typecheck/build/compileall đạt, không gửi mail thật.

- 2026-10-16 — Lọc no-email từ Check DB trước precheck theo owner/ngày/domain;
  no_email_count/reason riêng, metadata cũ mặc định 0. A/B no-email + C/D sent
  được loại trước probe, chỉ E/F tạo job. Không cache lỗi/chưa rõ/mâu thuẫn,
  không bỏ phần route gửi còn thiếu; all-filtered không tạo job, giữ ngày hiện có.
  FE 83/83, backend PostgreSQL cô lập 209 đạt/2 skip; regression latest terminal
  theo updated_at, khác owner/ngày/path, all-filtered và chỉ probe E/F đạt.
  lint/typecheck/build/compileall đạt; legacy 419 giữ baseline 4 failure/7 error.
  Không migration/config mới hoặc gửi mail thật trong kiểm thử.

- 2026-10-16 — Domain Worker cập nhật bảng ready tăng dần trong precheck, thẻ KPI
  riêng/cỡ lớn, highlight URL running; gửi có metric email/domain đang gửi và
  countdown HH:MM:SS từ next_run_at, zero chờ worker xác nhận. Giữ xác nhận gửi,
  owner lock/ledger; không đổi API/database/SMTP. FE 82/82 và regression incremental
  polling/countdown/reload/zero; lint/typecheck/build/compileall đạt.

- 2026-10-16 — UX Domain Worker chỉ tổng kết precheck, bỏ bảng/danh sách URL;
  chỉ cấu hình khi còn mục cần gửi, start chuyển sang theo dõi batch X/Y theo
  domain JobItem. Banner completed/partial, progress native, màu cyan–blue/xanh
  lá/vàng có nhãn chữ và dark mode. Không đổi backend/SMTP/database.
  FE 80/80, lint/typecheck/build và compileall đạt; kiểm thử empty outcome,
  running batch 1/10 và completed banner, không gửi mail/check URL thật.

- 2026-10-16 — Rà soát batch precheck→SMTP: chung owner lock cho create/start/resume/
  retry, workspace API phục hồi active IDs/history; chờ xác nhận giữ lượt tới send/
  explicit close. Form không thay batch đang chạy, lịch sử đọc lại được. Lọc URL
  chuẩn hóa/trùng/đã gửi đủ mọi route-account trước probe; all-filtered không tạo
  job. Retry giữ SENT/UNKNOWN/Message-ID, refresh exact plan từ evidence còn hợp lệ,
  chỉ xử lý failed/unattempted; UI có attempts/lỗi/Message-ID. Finalize merge metadata,
  recovery theo lease/intent không khóa vĩnh viễn. Không migration/cấu hình mới;
  giữ policy theo ngày hiện có, không xóa lịch sử/runtime hoặc gửi mail thật để test.
  FE 78/78; backend PostgreSQL cô lập 207 đạt/2 skip, gồm wheel 3/3;
  lint/typecheck/build/compileall/pip check đạt. Legacy 419 giữ baseline
  4 failure/7 error. Reload worker khi không có precheck/send đang chạy.

- 2026-10-16 — Khôi phục đầy đủ narrative Browser Evidence legacy cho email worker:
  projection giữ control/link DOM, navigation nguồn/đích/redirect và form signals
  tổng hợp đã redact; render qua formatter chung sau validate owned ảnh/hash.
  Plain/HTML có đủ mô tả, href, bước tái hiện và phạm vi capture trước chữ ký; không
  dùng block generic viết riêng. DOM cũ mất metadata phải precheck lại nếu cần gửi,
  không đoán dữ kiện hay sửa email đã gửi. UI chỉ Đã gửi/Hoàn tất gửi, bỏ nhãn lưu
  bản sao thành công; IMAP lưu Sent tự động như cũ, chỉ báo khi có lỗi thực tế.
  Sửa MIME composer giữ đúng HTML part cho logo CID, không gắn logo vào manifest;
  hai ảnh DOM + JSON giữ ba attachment riêng. Kiểm thử đầu-cuối đối chiếu nguyên
  khối nội dung mẫu trong plain/HTML và cấu trúc MIME, không gửi/check URL thật.
  FE 73/73, backend PostgreSQL riêng 198 đạt/2 skip, wheel 3/3; legacy 419 giữ
  baseline 4 failure/7 error. Lint/typecheck/build/compileall/pip check đạt; backend
  editable đã đồng bộ formatter mới, worker idle nạp lại, API health 200.

- 2026-10-16 — Sửa thiếu bước lưu Sent khi migrate SMTP: legacy có `_imap_save_sent`
  sau SMTP, bản web đã bỏ sót; đính chính nhận định trước. Worker gọi chung helper
  sau checkpoint SMTP accepted, status copy riêng, lỗi IMAP không retry SMTP. Helper
  sửa parse quoted/SPECIAL-USE folder, kiểm APPEND OK, timeout/TLS và dedupe Message-ID.
  IMAP dùng account owned/allowlist; phục hồi chỉ bản sao từ approved immutable plan,
  giữ Message-ID/ngày gốc, không gửi lại SMTP. UI hiện kết quả lưu Sent từng email.
  FE 73/73, backend PostgreSQL riêng 196 đạt/2 skip, wheel 3/3; legacy 419 giữ
  baseline 4 failure/7 error (4 test Sent mới đạt). Lint/typecheck/build/compileall/
  pip check đạt. Cài lại backend editable để đồng bộ helper ngoài repo cwd;
  worker nạp lại sau khi idle. Phục hồi bản sao Sent cho các lượt đang đối soát,
  không SMTP resend; status copy lưu riêng trên từng attempt.

- 2026-10-16 — Đối soát batch báo hoàn tất nhưng không thấy Sent: ledger có hai
  SMTP accepted, sender không có IMAP APPEND Sent. UI đổi nhãn thành SMTP đã chấp
  nhận/Hoàn tất xử lý batch, đếm outcome duy nhất và giải thích chưa xác minh inbox/
  bản sao Sent. Không IMAP hoặc gửi lại, không sửa ledger đã gửi. Cập nhật tài liệu.
  FE 73/73, lint/typecheck/build/compileall đạt; chỉ đổi UI, không đổi SMTP/backend.

- 2026-10-16 — Triển khai Domain Worker gửi batch normal theo yêu cầu: precheck chỉ
  hiện số đếm, hoàn tất mới có danh sách còn cần gửi/cấu hình 5 domain/5 phút mặc định.
  Account scope giữ từ precheck; Start có permission/CSRF/xác nhận, server khóa plan/
  logo/ảnh trước enqueue, không draft/preview từng dòng. Worker domain_worker_send/all
  lưu lịch nghỉ, intent trước SMTP/checkpoint từng thư; retry failed giữ sent, unknown
  không tự retry. Dedupe owner/ngày/domain/account/channel/recipient như legacy, bỏ
  capture nếu đã gửi đủ; ngày mới không dùng batch/ledger hôm qua. DB giữ metadata/
  tiến độ/sổ gửi, ảnh/body private store. Cloaking vẫn cách ly, chưa approve/send web.
  FE 72/72, backend PostgreSQL riêng 195 đạt/2 skip, wheel 3/3; lint/typecheck/build/
  compileall/pip check đạt. Chrome fixture desktop/mobile kiểm counts→batch→F5 không
  POST lại; SMTP mock kiểm retry partial/Message-ID/UNKNOWN/day reset. Legacy 415
  giữ baseline 4 failure/7 error. Worker idle đã reload; không gửi thư thật.

- 2026-10-16 — Đối chiếu lại Domain Worker legacy: chọn account trước precheck,
  giữ Job.account_scope và account nguồn trong queue; bỏ Xem draft/Preview từng dòng.
  Danh sách thường chỉ nhận ready có email + evidence; no-email loại, cloaking/
  thiếu ảnh sang page riêng. Evidence Review nhận 1–3 ảnh + thumbnail, validate rồi
  đưa các check nguồn về ready; cloaking web mới có queue đọc, chưa approve/send.
  Sửa artifact root tương đối lệch CWD API/worker; phục hồi 7 file đúng hash vào
  thư mục chung, toàn bộ 9 artifact hiện có hợp lệ. Không gửi/check URL thật.
  FE 70/70, backend PostgreSQL riêng 183 đạt/2 skip, wheel 3/3; lint/typecheck/
  build/compileall/pip check đạt. Chrome fixture desktop/mobile xác nhận account
  scope, ready-only và F5 không POST lại. Hai worker idle đã reload; API health 200.
  Legacy 415 giữ baseline 4 failure/7 error.

- 2026-10-16 — Theo yêu cầu dùng lại logic cũ, capture web gọi trực tiếp hàm chung
  capture_normal_report_evidence với mặc định HTTP/DNS, DOM rồi passive fallback;
  bỏ override http_probe/network_guard, DNS pool mới và helper DNS capture không dùng.
  Adapter chỉ kiểm output ảnh/manifest/hash và chuyển sang owned artifacts. Ghi chú
  này thay thế cơ chế DNS/NET capture riêng của các mốc ngay trước đó.
  Capture đúng URL đã thành công hai ảnh bằng defaults; worker idle đã reload.
  FE 72/72; backend PostgreSQL riêng 178 đạt/1 skip, wheel 3/3; typecheck/lint/
  build/compileall/pip check đạt. Legacy 415 giữ baseline 4 failure/7 error.

- 2026-10-16 — Sửa tiếp capture web sau khi tái hiện trên URL vừa báo lỗi: DNS
  dnspython timeout nhưng resolver Windows trả IP public. Browser egress chuyển
  mặc định sang system DNS bounded 3 giây/pool 4, vẫn kiểm mọi IPv4/IPv6 và mỗi
  redirect/subresource; thêm mã egress_dns_failed. Đã capture đúng URL thành công
  hai ảnh DOM nguồn–đích, không gửi report. Kết quả precheck cũ giữ để audit.
  FE 72/72; backend PostgreSQL riêng 181 đạt/1 skip, wheel 3/3; typecheck/lint/
  build/compileall/pip check đạt. Legacy 415 giữ baseline 4 failure/7 error.

- 2026-10-16 — Sửa capture web thất bại do backend venv thiếu Playwright: khai báo
  dependency, cài môi trường hiện tại, kiểm tra Chromium và chụp DOM nguồn–đích
  bằng fixture loopback. Worker startup kiểm browser trước claim, lỗi capture có mã
  whitelist để UI giải thích. Mở sửa quyền/tên/mô tả admin theo yêu cầu; role save
  tự materialize permission mới thiếu DB, sync giữ lựa chọn admin/user. Đã đồng bộ
  catalog runtime và restart hai worker idle; không sửa kết quả precheck cũ hoặc
  tự mở lại URL nghi vấn. FE 72/72; backend PostgreSQL riêng 179 đạt/1 skip,
  packaging wheel 3/3; typecheck/lint/build/compileall/pip check đạt. Legacy 415
  giữ baseline 4 failure/7 error. Chrome kiểm form sửa admin desktop/mobile.

- 2026-10-16 — Domain Worker web thêm exact email preview cho ready đã checkpoint:
  chọn account owned, server resolve recipient, personalize bằng lõi cũ; khóa
  From/Subject/plain/HTML, logo inline, 1–3 ảnh + manifest JSON. Default logo được
  materialize thành owned artifact, không sửa profile; kiểm check/result/manifest
  hash, account/profile/evidence versions, revision và expiry 30 phút. F5 GET
  restore, quyền riêng domain_worker.preview + CSRF; chưa có approval/send SMTP.
  FE 71/71; backend PostgreSQL sạch 174 đạt/2 skip, gồm packaging 3/3; typecheck/
  lint/build/compileall/pip check đạt. Legacy 415 giữ baseline 4 failure/7 error.
  Chrome fixture kiểm desktop/mobile/F5; không check URL, SMTP/IMAP hoặc gửi thật.

- 2026-10-16 — Logo mail mặc định bắt buộc, upload/thay PNG/JPEG <=2 MB và reset
  ngay settings/mail; profile riêng/hash/version/audit, default không cần activation.
  Settings hiển thị mail connectivity policy trước thao tác, khóa endpoint chưa cho
  phép; bật local tests cho ba endpoint mail hiện có, không bỏ public DNS/TLS guard.
  Chuyển canonical toolkit/cloaking/browser/quick actions vào backend/compat,
  root wrappers giữ legacy; wheel không lấy source ngoài BE, worker helper ở BE.
  Tài liệu giải thích bốn biến browser/mail và mục tiêu cuối chỉ BE/FE, chưa xóa
  các workflow legacy còn chờ migration; không đụng config.ini/runtime hiện có.
  FE 67/67; backend PostgreSQL riêng 164 đạt/2 skip; wheel cô lập 3/3 gồm logo
  mặc định và MIME; typecheck/lint/build/compileall/pip check đạt. Legacy 415 giữ
  baseline 4 failure/7 error. Chrome xác nhận default logo/uploader và các nút
  thử kết nối được mở đúng policy; không SMTP/IMAP login hoặc gửi thật.

- 2026-10-16 — Áp dụng logo WIN cho hồ sơ mail web bằng owned immutable artifact,
  asset nằm trong backend package, không đọc config.ini. Draft DW có logo trước
  Regards và HTML CID; MIME composer portable được đối chiếu HTML/bytes với legacy.
  Tài liệu hóa worker chạy thường trực, .bat chỉ shortcut local, topology deploy
  và điều kiện chuyển hết compatibility vào BE trước khi dọn source root.
  Đã áp dụng logo cho hồ sơ được yêu cầu qua UI; JPEG backend trùng SHA-256 ảnh
  nguồn. FE 66/66, backend PostgreSQL riêng 162 đạt/2 skip, packaging 3/3;
  lint/typecheck/build/compileall/pip check đạt. Legacy giữ 4 failure/7 error.

- 2026-10-16 — DW-01: Domain Worker web precheck batch owned, HTTP cloaking/lookup
  song song, no-email/evidence/cloaking queue, capture thường có flag và ready gate;
  giữ operation/F5, stop/resume/retry failed, unknown không tự chạy lại. Draft
  tham khảo email Registrar/Registry/Hosting đối chiếu formatter/file legacy,
  pure shared routing/hosting renderer, không SMTP/IMAP/submit thật. Còn bước
  web review/Playwright cloaking và exact account preview/send. Sửa shared clock,
  cooperative stop và flush EvidenceSet trước Artifact sau kiểm thử PostgreSQL.
  FE 65/65; backend PostgreSQL riêng 159 đạt/2 skip; packaging 3/3 gồm wheel;
  typecheck/lint/build/compileall/pip check đạt. Legacy 415 giữ baseline
  4 failure/7 error. Chrome xác nhận menu/trang mới, không chạy URL/gửi thật.

- 2026-10-16 — Bỏ override width/padding content riêng Quick Report để dùng
  cùng AppShell và breakpoint với các page khác; không đổi nội dung hay nghiệp vụ.

- 2026-10-16 — Đồng bộ UI web gọn trên layout chung, Dashboard, admin, mail
  và Check Domain: sidebar 240px/header 56px, bảng/form/dialog giảm padding.
  Thông số Check Domain chuyển sang nhãn/giá trị theo bốn nhóm responsive,
  giữ profile/raw JSON trong details. Guidance bố cục gần Quick Report.
  Không đổi pipeline, không check/gửi thật; kiểm tra FE và visual QA local.
  FE 61/61, lint/typecheck/build/compileall đạt. Chrome kiểm tra Check Domain
  desktop sáng/tối và mobile 390px, cùng Dashboard/Quick Report/admin/mail.

- 2026-10-16 — Sửa vận hành lỗi Check Domain “Không tải được mẫu báo cáo”:
  backend editable nạp bản compatibility site-packages cũ thiếu formatter email.
  Cài lại đúng backend venv, reload API và xác nhận mẫu hiện trên kết quả cũ,
  không check/gửi lại. Bổ sung regression subprocess ngoài repo cwd và wheel
  cho browser/CDN/Registrar email/Registry webform+email; tài liệu hóa reinstall.
  Backend 89 đạt/60 skip, packaging 3/3 (gồm wheel cô lập), compileall/pip check
  đạt; legacy 415 giữ baseline 4 failure/7 error. Chrome xác nhận 8 mẫu hiện lại.

- 2026-10-16 — Sửa draft Check Domain theo đúng kênh nhận: Registry dùng
  `registry_contact` đã check, webform lấy formatter webform và email lấy biến
  thể email Registry cũ; nếu có cả hai thì hiện hai mẫu riêng. Registrar giữ
  mẫu email và webform riêng theo routing cũ. Không ghi draft file/gửi thật.

- 2026-10-16 — Check Domain web bổ sung mẫu email registrar khi tìm được địa chỉ
  hợp lệ: tách chọn recipient khỏi nhánh ghi draft legacy, lọc WHOIS privacy,
  hiện To/Subject và biến thể email cũ theo domain + ngày trong bộ nhớ. Registrar
  chỉ nhận webform dùng mẫu webform riêng, không hiện mẫu email. UI hiện thông số trực
  tiếp với chữ gọn, nhóm mẫu thành cảnh báo trình duyệt, CDN, Registrar và
  Registry; nội dung dài mở khi cần copy. Không gửi SMTP/submit form. Kiểm tra
  guidance 10 đạt/1 skip, smoke formatter thật đạt, compileall đạt; legacy 415
  giữ baseline 4 failure/7 error.

- 2026-10-16 — Sửa Check Domain web theo mục đích tra cứu một domain: bỏ nút
  capture/upload gây hiểu nhầm (capture backend headless, cần worker riêng và
  flag mặc định tắt), bỏ câu coi ảnh là bắt buộc. Thêm endpoint read-only owned
  report-guidance, dùng formatter/routing legacy để hiện form và nội dung copy;
  không tạo draft file hoặc gửi thật. Test mock + formatter thật không gọi mạng;
  FE 59/59, backend 87 đạt/60 skip, typecheck/lint/build/compileall đạt.

- 2026-10-16 — CD-02 React phần kiểm tra/evidence: đọc đối chiếu
  `pages/1_Check_Domain.py`, thêm trang một URL, giữ operation theo user qua F5,
  hiện kết quả đầy đủ theo nguồn, upload 1–3 ảnh với thumbnail, capture job riêng
  và xem artifact owned. Chưa có draft/preview/send API nên chưa hiện gửi; không
  check URL hoặc gửi thật. FE 60/60, typecheck/lint/build; backend 86 đạt/60
  skip, compileall đạt. DB integration cần PostgreSQL test cô lập.

- 2026-10-16 — Sửa Check Domain web về đúng một domain/full URL mỗi thao tác:
  POST nhận `target`, response trả một `check`, job nội bộ chỉ có một item.
  Quick Report và Domain Worker vẫn là luồng nhiều URL; không check/gửi thật.

- 2026-10-16 — Browser Evidence web cho phép capture trực tiếp như legacy khi
  bật flag và chạy worker riêng; proxy egress là tùy chọn. Giữ kiểm tra public
  URL cho từng HTTP redirect và browser request. Direct mode cần cô lập mạng
  worker để giảm rủi ro DNS rebinding tại thời điểm kết nối; không mở URL thật.

- 2026-10-16 — NET-01 app boundary: HTTP probe redirect từng hop qua proxy,
  Playwright route guard mọi request, chặn private IPv4/IPv6/DNS/port; capture
  lúc đầu yêu cầu proxy egress, sau đó đã bỏ ràng buộc này; flag vẫn mặc định tắt.
  Mail connectivity test mặc định tắt,
  chỉ nhận exact host:port allowlist + public DNS khi bật. External proxy/firewall
  và legacy full-check egress còn mở; backend 83 đạt/60 skip, legacy 415 giữ
  baseline 4 failure/7 error, compileall/pip check đạt. Không gọi mạng thật.

- 2026-10-16 — Sửa lỗi backend không khởi động sau EVID-01: `backend/.venv`
  chưa được cài lại nên thiếu `python-multipart` dù dependency đã khai báo.
  Cài lại package vào đúng venv; xác nhận uvicorn `--app-dir src --reload`
  startup và `GET /api/v1/health/live` trả 200. Không đổi nghiệp vụ.

- 2026-10-16 — EVID-01: Check Domain web có upload 1–3 ảnh PNG/JPEG, owned
  evidence set/manifest/hash và download qua artifact route hiện có; capture
  Browser Evidence vào job riêng, flag mặc định tắt cho tới khi có egress policy.
  Backend 75 đạt/60 skip (PostgreSQL test cô lập chưa có); legacy 415 giữ baseline
  4 failure/7 error; compileall/pip check đạt. Không mở URL nghi vấn thật.

- 2026-10-16 — CD-01: backend web có owned Check Domain create/read API, worker
  full investigation dùng lõi `run_check`, DTO whitelist và quyền submit VT riêng.
  Web không ghi CSV/draft/manifest legacy, không gửi report; React/evidence/send
  thuộc CD-02/EVID-01. Backend 71 đạt/59 skip (chưa có PostgreSQL test cô lập),
  legacy 415 giữ baseline 4 failure/7 error; compileall/pip check. Chỉ fixture/mock,
  không probe URL thật.

- 2026-10-16 — Tách /login Telegram riêng; / thành dashboard protected theo
  permission. 401 route nội bộ về /login, đăng nhập thành công về /, logout về
  /login; pending vẫn bị chặn. Callback lỗi/expired về login với mã allowlist.
  Login card cyan–blue responsive sáng/tối, không đổi OIDC/cookie/security policy.
  FE 56/56; backend 120 đạt/2 skip; typecheck/lint/build/compileall. Visual QA login
  390px + desktop sáng/tối bằng fixture cô lập; không login Telegram thật.
  CLAUDE.md không đọc/cập nhật theo chỉ dẫn hiện hành.

- 2026-10-16 — Sửa thứ tự thẻ Quick Report: lượt hiện tại theo input order, lịch
  sử ngày ngoài lượt xếp sau; số # khớp worker tuần tự. Không đổi worker/runtime.
  Thêm regression daily response đảo thứ tự. FE 47/47,
  typecheck/lint/build và compileall đạt. CLAUDE.md không đọc/cập nhật.

- 2026-10-16 — Daily Quick Report mặc định ẩn thẻ thuộc lượt cũ (mọi state) và
  cache hit, reset lựa chọn ẩn khi tạo lượt mới. Clear cache thành công dọn
  operation/session pointer để thẻ biến mất; giữ DB history, không tự check lại.
  Test 10+10/clear/recheck20 + clear failure; FE 46/46, typecheck/lint/build và
  compileall. Không sửa backend; CLAUDE.md không đọc/cập nhật theo chỉ dẫn.

- 2026-10-16 — Daily Quick Report phân biệt pending/running lịch sử không có
  job item: DTO not_queued, UI Chưa có job và hướng dẫn nhập lại URL. Không đổi
  runtime history, tự enqueue hoặc check lại. Kiểm tra membership cùng owner và
  job_type; giữ pending cho record thực sự có job. FE 45/45 + typecheck/lint/build;
  backend 118 đạt/2 skip, compileall đạt. CLAUDE.md không đọc/cập nhật.

- 2026-10-16 — Sửa Quick Report theo danh sách tích luỹ ngày: ô nhập luôn hiện,
  GET /quick-reports/daily đọc DB owner/workflow/ngày, chọn latest mỗi URL;
  không mất kết quả cũ khi thêm batch/F5/xoá Redis cache. Mặc định hiện cũ và
  mới, checkbox ẩn là tuỳ chọn; bộ đếm cũ/mới tách khỏi provenance Redis. API
  operation cũ giữ tương thích; không migration. FE scenario 10+2/F5, backend
  A/B/day/latest/permission được kiểm thử. FE 44/44 + typecheck/lint/build,
  backend 118 đạt/2 skip trên PostgreSQL sạch; compileall đạt.
  CLAUDE.md không đọc/cập nhật.

- 2026-10-16 — Quick Report thêm cache/new counts, mặc định ẩn kết quả cache,
  toggle xem lại và all-cache guidance; nút Xoá cache hôm nay theo permission,
  dùng API owner/day hiện có. Không xoá lịch sử/provenance/delivery hoặc tự
  check/gửi lại; khoá clear khi operation hiện tại đang tải/chạy. FE 43/43,
  typecheck/lint/build; cache backend 6 đạt/1 integration skip; compileall đạt.
  CLAUDE.md không đọc/cập nhật theo chỉ dẫn hiện hành.

- 2026-10-16 — Bỏ khối Báo cáo nic.top, component export và download helper khỏi
  React Quick Report theo yêu cầu; FE 38/38, typecheck/lint/build và compileall đạt. Giữ backend export tương thích, không đổi
  check/routing/registry form. Cập nhật README/technical guide/implementation plan.
  CLAUDE.md không đọc/cập nhật theo chỉ dẫn hiện hành.

- 2026-10-16 — Quick Report ẩn nhóm form không áp dụng, chia đều cột còn lại,
  nút báo cáo xanh cyan–blue nổi bật. Bật Netcraft submit flag cục bộ theo yêu
  cầu và restart FastAPI; file mẫu giữ false, loại khai báo trùng. Không gửi
  report thật; FE regression + typecheck/lint/build, compileall/pip check.
  CLAUDE.md không đọc/cập nhật theo chỉ dẫn hiện hành.

- 2026-10-16 — Quick Report dense layout: nhóm GSB/Microsoft/Chống Lừa Đảo/
  Cốc Cốc/Netcraft toàn hàng; dưới là ba cột CDN/Registrar/Registry. Form thường
  mở URL trực tiếp, bỏ modal copy trùng. Netcraft một lần bấm tạo exact preview
  rồi gửi API, bỏ xác nhận UI theo yêu cầu; giữ server permission/flag/ledger/
  dedupe/UNKNOWN. Giảm khoảng cách và chiều cao preview. FE 38/38, typecheck/lint/build và compileall đạt. Kiểm thử chỉ mock,
  không gửi report thật; CLAUDE.md không đọc/cập nhật theo chỉ dẫn hiện hành.

- 2026-10-16 — Quick Report chia ba cột báo cáo cộng đồng/hạ tầng/registrar-registry,
  mẫu nội dung inline theo dịch vụ, copy và nhãn Mở và tự điền rõ ràng. Thông tin
  hạ tầng/contact chung hàng. GET form-drafts thêm include_providers opt-in, giữ
  owner/permission/profile/no-store và formatter legacy; không tạo task/gửi report
  khi xem. Microsoft không có draft văn bản; Netcraft giữ exact approval riêng.

- 2026-10-16 — Thu gọn Quick Report React: hạ tầng icon/tên, routing hai dòng
  email; ẩn Cloaking HTTP trên UI. Sắp nút Google/Microsoft/Chống Lừa Đảo/Cốc Cốc/
  Cloudflare/Registrar/Registry; form có adapter chỉ hiện nút extension, form khác
  mở draft đúng channel + form URL trong dialog. Giữ nguyên backend và approval
  Netcraft. FE 34/34 test, typecheck/lint/build và compileall đạt; không gửi report
  thật. CLAUDE.md không đọc/cập nhật theo chỉ dẫn hiện hành.

- 2026-10-16 — Sửa Quick Report GET 500 khi API chạy từ backend cwd: Hatch
  editable path và wheel force-include chỉ bốn module compatibility cần thiết,
  không config.ini/.env/runtime. Test subprocess ngoài repo cwd và isolated
  wheel (không editable .pth) đạt; full backend chạy từ backend cwd 119/119,
  compileall/pip check đạt. Chrome khôi phục 6/6 kết quả đã có và preview draft
  thành công, không check/gửi lại. CLAUDE.md không đọc/cập nhật theo chỉ dẫn cũ.

- 2026-10-16 — Audit registrar/registry Quick Report: bổ sung toàn bộ manual
  form/draft từ 23 registrar form rule và 76 registry entry; 48 registrar email
  rule vẫn dùng resolver legacy. Sửa XYZ family và com.co/CentralNic routing
  theo form URL; API draft owned/private và FE preview/copy. Quick Report cũ
  chỉ hiển thị email contact, không có SMTP action. Không tuyên bố toàn bộ web
  migration hoàn tất; audit ở docs/quick-report-routing-audit.md. Không submit
  hoặc kết nối mail thật; CLAUDE.md không đọc/cập nhật theo chỉ dẫn cũ.
  Backend cô lập 117/117, FE 33/33 + typecheck/lint/build; legacy 415 giữ baseline
  4 failure + 7 error; compileall/pip check/diff check đạt.

- 2026-10-16 — Sửa vận hành Quick Report không có kết quả: khai báo dependency
  requests/python-whois/dnspython/ipwhois, startup preflight trước claim và
  heartbeat trong check lâu. Thêm run_quick_report_worker.bat; FE phân biệt
  pending/queued với running. Lượt người dùng chọn được phục hồi đúng 4 item
  lỗi thiếu dependency và hoàn tất 4/4 bằng worker thật; worker giữ chạy nền,
  không SMTP/IMAP/provider submit. Backend fixture 112/112, FE 32/32 và
  typecheck/lint/build; legacy giữ baseline 415 / 4 failure / 7 error;
  compileall/pip check đạt. CLAUDE.md không đọc/cập nhật theo chỉ dẫn cũ.

- 2026-10-16 — Sửa handshake extension trả `Error: origin_not_allowed` làm FE
  mất phân loại lỗi: worker trả error.message, FE tương thích cả prefix legacy.
  Tách runtime receiving-end-not-found/channel-closed. Chrome thật xác nhận
  bản extension mới đã nhận message nhưng chưa lưu đúng origin; không tự sửa
  permission hoặc mở form. FE 31/31 + lint/build; CLAUDE.md giữ nguyên.

- 2026-10-16 — Sửa chẩn đoán Quick Report extension: tách thiếu/sai ID, thiếu
  browser runtime, origin bị chặn, timeout; trim ID public và sanitize lỗi.
  Retry có loading/toast, sequence guard không để ping cũ ghi đè kết quả mới.
  UI hiển thị ID/origin. FE 30/30 và build; kiểm tra Chrome thật xác nhận env
  hợp lệ nhưng messaging API chưa được expose, không khẳng định đã kết nối.
  Không mở form/submit; CLAUDE.md không đọc/cập nhật theo chỉ dẫn cũ.

- 2026-10-16 — Sửa lỗi thêm mail bị báo chung “account conflicts”: thiếu khóa
  mã hóa trả 503 có hướng dẫn, trùng identity theo owner trả 409 riêng, lỗi
  SMTP/IMAP precondition có thông báo cụ thể; không lộ SQL/secret. Dev xác minh
  chưa có sender/encrypted account rồi khởi tạo master key trong backend/.env,
  kiểm tra AES-GCM roundtrip và dev reload. Không lưu mail mẫu/kết nối mail thật.
  Backend PostgreSQL/Redis cô lập 110/110; legacy 415 giữ baseline 4 failure +
  7 error; compileall/pip check/diff check đạt. CLAUDE.md không đọc/cập nhật.

- 2026-10-16 — Sửa form thêm mail đơn lẻ: nhãn tùy chọn fallback SMTP username,
  validation hiện rõ thay vì khóa nút im lặng; lỗi API nằm trong modal. Port
  465/587 tự chọn Implicit TLS/STARTTLS. SMTP-only không phụ thuộc profile,
  IMAP/proxy hoặc số lượng account. FE 25/25, typecheck/lint/build; không đổi
  backend hoặc kết nối mail thật. CLAUDE.md không đọc/cập nhật theo chỉ dẫn cũ.

- 2026-10-16 — FE thêm Sonner toast theo theme, shared mutation feedback,
  xác nhận khóa user/thu hồi phiên/xóa role/tắt mail; pending buttons, read-error
  retry, offline banner, render error boundary và tiến độ Quick Report. Toast
  không chứa raw API data; polling giữ im lặng. Logout chỉ kết thúc UI khi API
  thành công và lỗi mạng /me có retry. Chỉ thay FE, không gọi provider/mail thật.
  FE 24/24, typecheck/lint/build, npm audit và compileall/pip check đạt; Chrome
  automation không kết nối nên chưa visual verify trên tab hiện tại. CLAUDE.md
  không đọc/cập nhật theo chỉ dẫn phiên trước.

- 2026-10-16 — Quick Report bắt buộc hồ sơ theo owner có brand_name,
  contact_name và contact_email hợp lệ. API readiness chỉ trả missing/ready;
  dependency chặn check mới, task form, Netcraft preview/submit và export bằng
  409 khi chưa đủ cấu hình. React hiện checklist/link cấu hình trước khi mount
  workflow; kiểm tra lại sau lưu. Không bắt SMTP/IMAP cho Quick Report.

- 2026-10-16 — Quick Report bổ sung exact Netcraft preview/approval với ledger
  owner/ngày/full URL, durable intent, FAILED retry explicit và UNKNOWN không
  tự gửi lại; migration 0004. Export nic.top XLSX giữ full URL và literal cells.
  Kết nối tám provider form theo routing server, public contact payload và
  extension fill-only. Gửi Netcraft mặc định tắt bằng backend feature flag;
  kiểm thử fixture/mock, không submit provider thật. CLAUDE.md không đọc/cập
  nhật theo chỉ dẫn phiên trước.

- 2026-10-15 — UI-04: làm mới React theo visual system indigo–violet, gradient
  primary/active nav, header/card có chiều sâu nhẹ, avatar và icon tiles; nhóm
  nhập URL Quick Report rõ hơn, action bar responsive. Giữ light/dark và toàn bộ
  permission/workflow. FE 16/16 + typecheck/lint/build đạt; Chrome desktop/mobile
  390×844 hai mode không tràn ngang. Chỉ lọc URL fixture cục bộ, không chạy check
  hay gửi report. CLAUDE.md không đọc/cập nhật theo chỉ dẫn phiên trước.

- 2026-10-15 — UI-03: React có light/dark, lưu preference trình duyệt và khởi tạo
  trước render; Tailwind dark variant theo `.dark`, form/bảng/cảnh báo dùng chung
  palette. Cài Radix Themes 3.3.0, shared Button/AppDialog/ThemeToggle; dialog hỗ
  trợ focus trap/Esc/return focus, trang tải theo route. FE 16/16 test cùng
  typecheck/lint/build đạt; đã xem Chrome hiện tại ở desktop và mobile 390×844.
  Chỉ đổi frontend, không chạy nghiệp vụ hay gửi thật. CLAUDE.md không đọc/cập nhật
  theo chỉ dẫn phiên trước.

- 2026-10-15 — Theo yêu cầu mới, role `user` seed toàn quyền nghiệp vụ trừ
  `users.*` và `roles.*` (51/56). Sync thường giữ lựa chọn admin; CLI thêm
  `sync-rbac --reset-user-permissions` áp dụng bộ mặc định cho DB hiện có và
  thu hồi phiên thành viên. Admin vẫn toàn quyền, owner isolation không đổi.
  Backend DB/Redis test riêng 92/92; legacy 415 test giữ 4 failure + 7 error
  baseline; compileall/pip check đạt. Đã áp dụng và xác minh DB dev user 51 quyền.
  CLAUDE.md không đọc/cập nhật theo chỉ dẫn phiên trước.

- 2026-10-15 — Hoàn tất RBAC-03: admin có toàn catalog (56 quyền); user ban đầu
  rỗng, permission do admin chọn và sync không ghi đè. Migration 0003 bỏ quyền
  user seed cũ; bootstrap chỉ gán admin; UI cho sửa permission user, khóa admin.
  Gán role user cũng chặn vượt quyền. Backend PostgreSQL/Redis riêng 92/92;
  frontend 13/13 + typecheck/lint/build; migration downgrade/upgrade/check,
  compileall và pip check đạt. Legacy 415 test giữ 4 failure + 7 error baseline.
  Không gửi SMTP/IMAP/provider thật. CLAUDE.md không đọc/cập nhật theo chỉ dẫn cũ.

- 2026-10-15 — Chuẩn hóa initialization RBAC: thêm CLI idempotent
  `domaincheck-admin sync-rbac` chạy sau Alembic để tạo permission catalog và
  system role trước khi có admin. Bootstrap admin đầu tiên gán cả `admin` và
  `user`, nên có quyền quản trị lẫn các workflow an toàn mặc định; user ngoài
  vẫn `pending` cho tới khi admin kích hoạt.

- 2026-10-15 — Sửa dependency cài đặt backend cho SQLAlchemy 2.1 async:
  dùng `sqlalchemy[asyncio]` để clean virtualenv luôn có `greenlet` trước khi
  Alembic import async engine. Lỗi thiếu `greenlet` không còn yêu cầu người dùng
  cài tay một dependency ngoài khai báo dự án.

- 2026-10-15 — Tách cấu hình web đúng package boundary: FastAPI và PostgreSQL
  Compose dùng `backend/.env` tạo từ `backend/.env.example`; frontend chỉ nhận
  biến public trong `frontend/.env` khi cần. Root không còn `.env.example` và
  backend không đọc root `.env`; `compose.yaml` vẫn ở root với vai trò điều phối.

- 2026-10-15 — Sửa Telegram web login: token exchange chuyển sang HTTP Basic theo
  OIDC Telegram chính thức; Vite dev callback/cookie thống nhất ở
  `127.0.0.1:5173`, backend đọc `backend/.env`, có status API không lộ secret và callback
  error allowlist. FE đổi sang dark investigation console với login readiness,
  sidebar/topbar cyan–indigo responsive; thiếu cấu hình thì khóa login và chỉ dẫn
  callback. Backend PostgreSQL 17 + Redis 7 đạt 90/90; frontend 12/12 +
  typecheck/lint/build; visual Playwright đạt ở 1440×1000 và 390×844. Legacy giữ
  baseline 415 test / 4 failure / 7 error. Mock tests không gọi Telegram thật.
  `CLAUDE.md` không đọc/cập nhật.

- 2026-10-15 — Hoàn thành EXT-01: FastAPI tạo capability task Quick Report owned,
  one-time exchange/status token và device registration; React Axios mở Google/
  Microsoft qua Web Form Assistant v3, poll trạng thái và fallback link thủ công.
  Extension kiểm exact app/provider origin, chỉ `fill_only`, giữ payload tạm trong
  `chrome.storage.session`; token không vào provider URL/DB/Redis/localStorage.
  PostgreSQL 17 + Redis 7 backend 87/87, test form-task/extension 15/15, frontend
  11/11 + typecheck/lint/build và JS syntax đạt. Chỉ fixture/mock, không mở form hay
  submit thật. Legacy giữ baseline 415 test / 4 failure / 7 error. Sáu adapter còn
  lại thuộc EXT-02; `CLAUDE.md` không đọc/cập nhật.

- 2026-10-15 — Chuẩn hóa frontend sang feature-based architecture: `app` giữ
  provider/router/layout; `features` tách api/hooks/components/pages/schemas/utils;
  `components/shared` và `lib/http|query` dùng chung. Thay toàn bộ HTTP runtime bằng
  Axios 1.20.0 cùng cookie, CSRF interceptor và error normalization. Triển khai phần
  check thật của QR-02: lọc explicit, permission gate, POST operation, polling, result
  cards và phục hồi F5 theo user. Provider autofill/Netcraft/nic.top vẫn khóa vì chưa
  có API capability/idempotency, nên QR-02 còn `in_progress`, không dựng success giả.
  FE typecheck/lint/10 test/build đạt; không gọi network/provider/SMTP/IMAP/capture.
  `CLAUDE.md` không đọc/cập nhật theo chỉ dẫn người dùng.

- 2026-10-15 — Hoàn thành backend `QR-01`: API owned tạo/đọc Quick Report operation,
  ghi target/check/job/item trước probe và trả `202`; worker process riêng gọi đúng
  compatibility `run_cdn_check()`, checkpoint từng URL, giữ full URL/order/duplicate.
  Redis cache tách owner/ngày/engine và DB ghi provenance; sanitizer loại raw HTML,
  credential/local path. Test chỉ dùng fake adapter, không network/provider thật;
  backend PostgreSQL + Redis 82/82;
  Alembic check/compileall/pip check/diff check đạt;
  legacy giữ baseline 415 test / 4 failure / 7 error. React page/form task/export còn
  QR-02/EXT-01. `CLAUDE.md` không đọc/cập nhật.

- 2026-10-15 — Hoàn thành `DELIVERY-01`: delivery được materialize từ exact preview
  đã duyệt bằng business identity phía server, không dùng preview/hash để né dedupe;
  Message-ID ổn định, row claim + optimistic version và attempt intent được ghi trước
  side effect. Chỉ `FAILED` retry explicit; lease/mất checkpoint sau intent phục hồi
  thành `UNKNOWN`. Sửa `OwnedRepository` refresh row sau khi chờ `FOR UPDATE`, chặn race
  hai session cùng claim. Preview mới đã duyệt chỉ rebind delivery còn pending/failed,
  không mở lại SENT/SENDING/UNKNOWN. Backend PostgreSQL + Redis 76/76; Alembic check, compileall,
  pip check và diff check đạt. Legacy giữ đúng baseline 415 test / 4 failure / 7 error;
  chưa có route gửi workflow hoặc SMTP/IMAP/provider/capture thật. `CLAUDE.md` không
  được đọc/cập nhật theo chỉ dẫn.

- 2026-10-15 — Hoàn thành `PREVIEW-01`: exact subject/plain/HTML body và delivery
  plan được lưu trong immutable private JSON artifact; PostgreSQL giữ revision,
  fingerprint, expiry, account/profile version, recipients và evidence references.
  Advisory lock cấp revision; approve/send-boundary validation chặn revision cũ,
  hết hạn, account/profile/evidence thay đổi, artifact mutation và cross-owner ID.
  Backend PostgreSQL + Redis 69/69, test tập trung 11/11; Alembic check xác nhận không
  cần migration mới, compileall/pip check/diff check đạt. Legacy giữ baseline 415 test,
  4 failure + 7 error. Không gửi SMTP/IMAP/provider/capture thật.

- 2026-10-15 — Hoàn thành `JOB-01` cho backend web: service tạo job/item bền vững
  giữ input order và account scope; worker claim/heartbeat/start/checkpoint bằng lease
  token + optimistic version; stop/resume cooperative và retry đúng failed subset.
  Lease chết giữa item hoặc trong lúc stop được phục hồi thành `UNKNOWN`, không tự retry.
  PostgreSQL + Redis backend 64/64; 12/12 test job; Alembic check, compileall và pip
  check đạt. Legacy giữ đúng baseline 415 test / 4 failure / 7 error. Chỉ dùng fixture,
  không SMTP/IMAP/provider/capture thật.

- 2026-10-15 — Hoàn thành DB-02 và ISO-01 foundation: thêm `OwnedRepository` cố định
  owner cho get/list/count/update/delete; Target, Artifact và Mail Settings dùng boundary
  chung. Job claim đồng thời bằng `SKIP LOCKED`, reclaim lease hết hạn và heartbeat
  yêu cầu token + optimistic version; stop theo owner. Test PostgreSQL xác minh A/B cùng
  URL/account, direct/nested ID, HTTP 404, composite FK và cùng pooled connection không
  rò dữ liệu. PostgreSQL 17 + Redis 7 fixture đạt 57/57; Alembic check, compileall và
  pip check sạch. Không gọi network, SMTP/IMAP, capture hoặc runtime thật.

- 2026-10-15 — Hoàn thành ART-01 cho web multiuser: thêm private artifact port và
  filesystem adapter với key UUID scope owner/evidence, staging/rollback batch,
  validation PNG/JPEG MIME/signature/byte/dimension/SHA-256 và integrity check khi
  đọc. `GET /api/v1/artifacts/{id}` kiểm owner + permission feature, không lộ local
  path/tên nguồn. Test tập trung 17/17; PostgreSQL 17 tạm chạy 48 test (47 đạt,
  1 Redis integration skip), Alembic check sạch; legacy giữ baseline 415 test /
  4 failure / 7 error. Không capture URL, gửi SMTP, đọc IMAP hay dùng runtime thật.

- 2026-10-15 — Hoàn thành CACHE-01 cho web multiuser: Redis key có
  owner/feature/business-day/bucket/version và chỉ giữ hash của input; TTL tới đúng
  nửa đêm timezone IANA, gồm DST. Get/set fail-open khi Redis mất; clear theo
  permission chỉ SCAN/UNLINK namespace owner và không chạm ledger/evidence/DB.
  PostgreSQL 17 + Redis 7 tạm đạt 39/39, Alembic check sạch; flush/restart Redis
  chỉ tạo cache miss. Không đọc `CLAUDE.md` theo chỉ dẫn hiện hành.

- 2026-10-15 — Web MAIL-02 thêm action kiểm tra SMTP/IMAP theo owner và quyền
  `mail_accounts.test_own`: SMTP chỉ TLS/login, IMAP chỉ TLS/login/logout, không
  gửi thư hoặc đọc mailbox. Secret chỉ giải mã trong runtime, lỗi/audit sanitize;
  React chỉ hiện nút khi có quyền. PostgreSQL test tạm 31/31, FE 6 test cùng
  typecheck/lint/build đạt; không kết nối mail thật. NET-01 vẫn là release gate
  trước khi cho phép egress production.

- 2026-10-15 — Hoàn thành MAIL-01 cho web multiuser: migration `0002` bảo đảm một
  sender mặc định mỗi owner; API + React quản lý profile, SMTP, IMAP folder Unicode,
  proxy và chữ ký. Secret AES-GCM bind owner/account, GET chỉ trả cờ và PATCH bỏ
  field secret giữ ciphertext; xóa account là soft-disable. PostgreSQL 24/24 và
  Alembic upgrade/check/downgrade/upgrade đạt; không mở SMTP/IMAP hoặc gửi mail.

- 2026-10-15 — Hoàn thành RBAC/admin foundation của web multiuser: API + React
  quản lý user/role, kích hoạt/khóa, gán role, revoke session, audit allowlist,
  bootstrap admin explicit theo Telegram OIDC subject và transaction bảo vệ admin
  hoạt động cuối cùng. Lỗi API có request ID và DTO allowlist. PostgreSQL test tạm
  đạt 21/21 gồm concurrent last-admin; FE typecheck/lint/4 test/build đạt. Full
  legacy giữ đúng baseline 415 test, 4 failure + 7 error. Không gọi Telegram,
  SMTP/IMAP hoặc provider thật; chưa mở workflow điều tra/gửi.

- 2026-10-15 — Bắt đầu triển khai migration web theo plan M0/M1. Thêm
  `docs/implementation-plan.md`, parity/storage/ADR; backend FastAPI, metadata và
  Alembic 24 bảng PostgreSQL, owner composite constraints, ExecutionContext,
  permission catalog, AES-GCM secret envelope, OIDC PKCE/state/nonce primitives
  và repository isolation; auth DB tạo pending user, opaque session, `/me`, CSRF
  logout và authz-version invalidation. Thêm React/TypeScript/Vite/Tailwind foundation gọi
  health API thật; chưa mở workflow nghiệp vụ hoặc gửi. PostgreSQL test upgrade,
  `alembic check`, downgrade và A/B isolation đạt; backend 16 test khi không DB
  (5 integration skip), DB run 15/15 trước test route bổ sung, FE
  typecheck/lint/2 test/build đạt. Full legacy vẫn
  đúng baseline 415 test, 4 failure + 7 error; compileall và pip check đạt. Không
  đọc config thật, import runtime, mở URL, gửi SMTP hay gọi live Telegram.

- 2026-10-15 — Hoàn thiện prompt FE/BE cho nhiều user: mục 6.5 quy định owner,
  actor, sender account, DB/cache/job/evidence isolation; mục 18.1–18.3 bắt buộc
  lập implementation plan có task/dependency/acceptance và cập nhật tiến độ.
  Bổ sung kiểm thử A/B, concurrent user và release gate; README dẫn tới bản mới.
  Chỉ đổi tài liệu, chưa thực hiện plan, migration hoặc thay đổi ứng dụng.
  Compileall, diff check và kiểm tra cấu trúc prompt đạt; không chạy unittest
  vì không thay mã nguồn hoặc hành vi hiện tại.

- 2026-10-15 — Chốt PostgreSQL + Redis theo lựa chọn chủ dự án trong prompt
  FE/BE; thêm `targets`, phân biệt URL ổn định với từng lần check, thời điểm ghi
  DB và 24 bảng nền tảng. MongoDB chỉ còn phần so sánh; chưa tạo database hoặc
  migration thật. Chỉ cập nhật prompt, README và ghi chú này.

- 2026-10-15 — Thêm `PROMPT_TACH_FE_BE_REACT.md`: khảo sát chức năng và đặc tả
  chuyển FE React/Vite/Tailwind, BE Python API, Telegram login, RBAC, mail config
  theo user; đề xuất PostgreSQL + Redis và phương án MongoDB thay thế. Tài liệu
  quy định parity, migration, worker/evidence/extension và bốn menu cần bỏ khi
  triển khai. README dẫn tới prompt; chưa đổi code, dependency hoặc runtime.
  Compileall, kiểm tra cấu trúc Markdown và diff check đạt; không chạy unittest
  vì chỉ đổi tài liệu. Số 415 test trong prompt là đếm tĩnh AST, không phải kết
  quả chạy test.

- 2026-10-06 — Lead Triage module version 23 định dạng report theo các mục
  Brand Impersonation, Observed Redirect Chain, Related Backend Findings và
  Evidence and Request; giữ nội dung chứng cứ từ Domain Worker, thêm registrar
  khi ghi chú có `reg=`, và liệt kê toàn bộ backend đã cung cấp kèm trạng thái
  liên hệ chưa xác minh. Không tự khẳng định đánh cắp thông tin, tiền hoặc OTP
  nếu evidence không cho thấy. 45/45 test Lead Triage và compileall đạt. Không
  gửi SMTP hoặc mở URL thật.

- 2026-10-06 — Lead Triage module version 22 giữ mọi đầu mối backend người dùng
  cung cấp trong report; mỗi dòng nêu IP cung cấp, DNS A hiện tại, RDAP và ghi
  rõ khi IP RDAP không xuất hiện trong DNS A hiện tại; đồng thời nêu
  có/không có giao điểm DNS/URL. NCC được yêu cầu xác minh trước khi liên kết;
  không khẳng định backend chưa liên kết đang phục vụ target. 45/45 test Lead
  Triage và compileall đạt. Full suite 412 test còn 3 failure + 6 error nền
  Windows ngoài thay đổi này. Không gửi SMTP hoặc mở URL thật.

- 2026-10-06 — Lead Triage module version 21 rút report có DOM evidence:
  không lặp thời điểm/DNS target không liên quan, lời yêu cầu điều tra, URL và
  hướng dẫn ở phần review focus. Giữ các bước tái hiện/redirect trong evidence
  chung, chỉ nêu điểm cần đánh giá và backend có liên hệ; bản Việt tóm tắt.
  44/44 test Lead Triage và compileall đạt. Full suite 411 test còn baseline
  Windows 3 failure + 6 error ngoài thay đổi; không gửi SMTP hay mở URL thật.

- 2026-10-06 — Lead Triage chỉ hiển thị report của domain vừa bấm Check; Check
  batch hiển thị nhóm vừa chọn. Vẫn giữ cache các domain khác để quay lại xem,
  xóa observation English dùng chung khi chuyển domain đơn để tránh lẫn nội dung.
  44/44 AppTest/unit test Lead Triage và compileall đạt.

- 2026-10-06 — Sửa nội dung gửi Lead Triage theo case 8cewd.buzz: formatter
  registry dùng “our brand identity”; draft cũ được sửa ngữ pháp; chỉ ghép
  backend có liên hệ qua DNS A hoặc hostname trong Browser Evidence hợp lệ.
  Giữ URL/redirect chứng cứ, bỏ yêu cầu điều tra lặp khi đã có DOM evidence và
  câu so sánh với dịch vụ chính thức chưa được cung cấp. Module version 20;
  test Lead Triage cô lập sent log để không đọc trạng thái gửi runtime.
  44/44 test Lead Triage và compileall đạt; full suite 411 test còn đúng baseline
  Windows 3 failure + 6 error ngoài thay đổi. Không gửi SMTP hay mở URL thật.

- 2026-10-06 — Lead Triage lưu snapshot review v1 tại
  `data/lead_triage_cache.json` bằng ghi atomic: ghi chú, parsed targets,
  precheck/draft, đường dẫn DOM evidence và widget review được khôi phục qua
  chuyển menu/F5, không tự check hay gửi. Nút **Xóa cache** xóa snapshot và
  state UI, giữ sent log/evidence audit. Cache backend khôi phục buộc kiểm tra
  lại khi operator bấm Check. 42/42 test Lead Triage/AppTest (cache dùng thư
  mục tạm), compileall và diff check đạt. Full suite 409 test còn baseline
  Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage gộp logo và thông tin tổ chức từ config chung vào
  bản sao tài khoản SMTP nếu account thiếu trường chữ ký; dùng cùng bản sao
  cho preview và SMTP để logo không chỉ xuất hiện ở UI. Giữ email gửi theo
  account đã chọn. 37/37 test Lead Triage, compileall và diff check đạt; full
  suite 404 test còn baseline Windows 3 failure + 6 error. Không gửi SMTP thật.

- 2026-10-06 — Rà lại nội dung report Lead Triage: addendum ghi thời điểm check
  UTC, phân biệt DNS A quan sát với IP backend do người vận hành cung cấp, chỉ
  gắn tổ chức RDAP khi IP khớp DNS, yêu cầu NCC xác minh mối liên hệ và xử lý
  khi xác nhận vi phạm. Sửa ngữ pháp sau khi loại AU88/AU888, subject Việt nêu
  nghi phishing; giữ nguyên phần report gốc Domain Worker. Bản Việt dịch thêm
  các mẫu registrar cố định gồm mô tả, yêu cầu, câu kết, ngày phát hiện và
  VirusTotal. 38/38 test Lead Triage, compileall và diff check đạt; full suite
  405 test còn baseline Windows 3 failure + 6 error. Không gửi SMTP thật.

- 2026-10-06 — Preview Lead Triage hiển thị logo ngay trước Regards bằng native
  Streamlit, dùng lại formatter chữ ký và resolver logo của email gửi. Trước
  khi chọn account hiện profile tổ chức chung; sau khi chọn hiện đúng account,
  vẫn có plain text exact trong expander. 36/36 test Lead Triage, compileall và
  diff check đạt; full suite 403 test còn baseline Windows 3 failure + 6 error.
  Không gửi SMTP thật.

- 2026-10-06 — Lead Triage thêm nút kiểm tra lại email nhận theo đúng resolver
  Domain Worker tại từng draft. Kết quả cập nhật địa chỉ mặc định và kênh;
  lỗi xóa precheck recipient cũ và khóa gửi cho đến khi tra lại thành công.
  Module version 18.
  35/35 test/AppTest liên quan, compileall và diff check đạt. Full suite
  402 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage thêm bước **Phân tích ghi chú** explicit trước khi
  hiện danh sách; dán/sửa văn bản chỉ cập nhật ô nhập, ẩn danh sách cũ và không
  chạy precheck/network. Thu gọn ô nhập, hiển thị số lượng target/backend và
  hướng dẫn thao tác ngay trên page. AppTest kiểm tra không tự check khi dán/sửa,
  kể cả khi nhập lại ghi chú cũ. 33/33 test Lead Triage và compileall đạt; full
  suite 400 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage chọn email nhận từ recipient precheck Domain Worker
  theo channel của draft, tách nhiều email và lọc địa chỉ bị chặn/sai định dạng;
  fallback email của draft Worker, không lấy contact IP/backend hoặc ghi chú.
  Widget email cập nhật theo địa chỉ mặc định mới. Module version 17.
  32/32 test/AppTest liên quan, compileall và diff check đạt. Full suite
  399 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage khôi phục target Đã gửi từ metadata `sent_log.csv`
  (`send_mode=lead_triage`, `status=sent`, đúng `target_url`) để ẩn và khóa Check
  sau khi mở lại trang. Session vẫn giữ trạng thái nếu SMTP thành công nhưng ghi
  log lỗi. Module version 16; có AppTest phiên mới và log gửi lỗi/kênh khác.
  30/30 test/AppTest tập trung, compileall/diff check đạt; full suite 397 test
  còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage giữ nguyên Browser Evidence formatter chung rồi
  thêm hướng dẫn NCC xác minh nguồn–đích và dấu hiệu giả mạo/luồng đăng nhập
  từ manifest; phần Việt có bước tái hiện và câu hỏi hạ tầng backend. Không suy
  diễn một DOM href thành click hoặc chứng cứ origin. Module version 15.
  30/30 test/AppTest liên quan, compileall và diff check đạt. Full suite còn
  baseline Windows 3 failure + 6 error ngoài thay đổi này; không mở URL thật
  hoặc gửi SMTP trong kiểm thử.

- 2026-10-06 — Lead Triage khóa Check cho target đã gửi: cột ButtonColumn
  không có nút ở dòng Đã gửi, multiselect bỏ target này, callback và batch
  lọc lại để chặn sự kiện cũ. Trạng thái gửi vẫn hiển thị trong bảng.
  28/28 test/AppTest tập trung, compileall và diff check đạt. Full suite
  395 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage thêm cột Trạng thái gửi theo đúng target. SMTP
  thành công hoặc `already_sent` ghi trạng thái trong session và rerun bảng;
  gửi lỗi vẫn Chưa gửi. Giữ trạng thái khi nội dung ghi chú thay đổi trong phiên.
  27/27 test/AppTest tập trung, compileall và diff check đạt. Full suite
  394 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage hoàn thiện bản đối chiếu tiếng Việt cho các biến thể
  report registry Domain Worker: lời chào, lý do liên hệ, yêu cầu xử lý và nhãn
  chứng cứ. Bản tiếng Anh gửi NCC giữ nguyên. Module version 14.
  26/26 test/AppTest liên quan, compileall và diff check đạt; full suite
  393 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage giữ nguyên câu tố phishing từ report Domain Worker
  khi loại tên AU88/AU888, thay tên bằng “our brand” thay vì xóa cả câu.
  Backend bỏ cụm “trong ghi chú”, thêm nhãn IP được cung cấp, DNS mới và RDAP;
  tiếp tục yêu cầu NCC xác minh mối liên hệ. Module version 13.
  24/24 test/AppTest tập trung, compileall và diff check đạt. Full suite
  391 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage chỉ mở Gửi report khi chụp DOM nguồn–đích thành
  công, đủ hai ảnh, manifest/hash hợp lệ và đúng URL; kiểm tra lại tại ranh giới
  SMTP và trong delivery lock. Bỏ uploader thủ công ở menu này; ảnh thụ động
  fallback chỉ dùng xem trước, không mở gửi. Module version 12.
  23/23 test/AppTest liên quan, compileall và diff check đạt. Full suite
  390 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này;
  không chụp URL thật hoặc gửi SMTP trong kiểm thử.

- 2026-10-06 — Lead Triage đổi nhãn nhập và nguồn recipient sang “Thông tin
  đầu mối”/“ghi chú đầu mối”; module version 11 nạp lại nhãn helper cũ.
  21/21 test/AppTest liên quan và compileall/diff check đạt; chỉ đổi câu chữ.

- 2026-10-06 — Lead Triage ghim cột Check/Domain bên trái, đặt thứ tự cột
  explicit và reset key bảng để bỏ layout cũ. Batch tối đa sáu target song song;
  backend DNS/RDAP trong cùng ghi chú tái dùng tối đa 300 giây, target check mới.
  Module version 10. Test/AppTest tập trung 21/21 đạt, gồm barrier sáu target
  và cache backend hết hạn. Không gửi SMTP hoặc mở URL thật trong kiểm thử.
  Compileall/diff check đạt; full suite 388 test còn đúng baseline Windows
  3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Sửa phạm vi Lead Triage theo cấu trúc ghi chú: domain trước mục
  backend là report target (ví dụ sáu domain), domain/IP sau marker chỉ làm đầu
  mối hạ tầng bổ sung (ví dụ hai domain), không tạo report/gửi riêng. Check
  backend bằng DNS/RDAP một lần, song song với precheck target; draft từng
  target thêm IP ghi chú, DNS mới và org RDAP với nhãn mối liên hệ cần xác minh,
  giữ recipient của report target. Module version 9. Test/AppTest tập trung
  20/20, compileall và diff check đạt; full suite 387 test còn đúng baseline
  Windows 3 failure + 6 error ngoài phạm vi thay đổi. Không gửi email hay mở
  domain thật trong kiểm thử.

- 2026-10-06 — Lead Triage loại dòng nhắc AU88/AU888 khỏi subject/body cũ
  và observation bổ sung trước preview Anh–Việt; giữ URL chứng cứ và snapshot
  gốc, subject fallback theo domain. Có regression test draft cũ + observation.
  Regression test này đạt; compileall và diff check đạt. Full suite tại thời
  điểm kiểm tra: 384 test, 4 failure + 9 error, gồm 1 failure + 3 error ở
  AppTest Lead Triage đang thay đổi và baseline Windows/Provider Replies.

- 2026-10-06 — Xử lý đầu mối thêm nút Check trên từng dòng bảng, giữ các kết quả
  đã check trong cùng ghi chú. Batch check tối đa bốn đầu mối song song và RDAP
  các IP của một đầu mối chạy cùng precheck; report `run_check()` được tạo theo
  yêu cầu ở từng mục sau precheck để giảm thời gian chờ ban đầu. Test/AppTest
  tập trung 16/16 và compileall đạt. Full suite 383 test còn đúng baseline
  Windows: 3 failure + 6 error không thuộc thay đổi này; không gọi URL hoặc
  gửi SMTP thật trong test.

- 2026-10-06 — Làm sạch thư NCC của Xử lý đầu mối: bỏ note provenance subdomain
  và lời giải thích ghi chú nội bộ; backend external chỉ chứa DNS/IP/RDAP khớp.
  Yêu cầu đình chỉ dịch vụ vi phạm, bảo toàn hồ sơ và xác nhận biện pháp xử lý.
  Hai panel dùng cùng chữ ký tổ chức theo tài khoản; logo preview/SMTP dùng helper
  Domain Worker, giữ ảnh DOM + manifest đã duyệt. 14/14 test tập trung,
  compileall và diff check đạt. Full suite 381 test còn đúng baseline Windows
  3 failure + 6 error; không gửi SMTP hay capture URL thật trong kiểm thử.

- 2026-10-06 — Xử lý đầu mối dùng nguyên subject/body report của Domain Worker
  qua `run_check()`; đầu mối IP chưa có hosting draft dùng cùng formatter hosting.
  Snapshot parsed draft theo full URL, chèn backend context và Browser Evidence
  trước chữ ký. Nút DOM gọi đúng `provider_replies.capture_dom_link_evidence()`;
  giữ preview Anh–Việt và SMTP exact kèm attachment/hash. Dùng predicate cloaking
  chung cho precheck/pipeline và log delivery theo report/channel gốc.
  13/13 test tập trung (parity formatter hosting thật, AppTest DOM/SMTP mock),
  compileall và diff check đạt. Full suite 380 test còn đúng baseline Windows
  3 failure + 6 error; không capture URL hay gửi SMTP thật khi kiểm thử.

- 2026-10-06 — Menu Xử lý đầu mối hiển thị draft trong hai khung Anh–Việt có viền,
  đặt cạnh nhau, cùng nút Gửi nổi bật ngay dưới draft của từng domain. Nút này
  chỉ mở sau khi chọn tài khoản SMTP, xác minh
  người nhận và tích xác nhận gắn với nội dung hiện tại. Chỉ body tiếng Anh đã
  preview được gửi; fingerprint + lock và `sent_log.csv` chống gửi trùng, log
  chỉ chứa metadata; session cũng khóa lại nút sau SMTP thành công. Không gửi thư
  thật trong kiểm thử. 13/13 test tập trung (gồm AppTest/SMTP mock), compileall và
  diff check đạt; full suite 380 test còn đúng baseline Windows 3 failure + 6 error.

- 2026-10-06 — Sửa draft Xử lý đầu mối theo quan sát thực tế; dùng nút capture
  Browser Evidence chung helper Domain Worker, có fallback upload 1–3 ảnh.
  Preview ảnh, xác thực manifest/hash/URL và gắn fingerprint của attachment
  vào bản đã duyệt; chỉ gửi tiếng Anh kèm ảnh + manifest khi evidence hợp lệ.
  Case cloaking cần duyệt riêng bị khóa gửi ở menu này. 13/13 test tập trung
  gồm AppTest capture và SMTP mock, compileall/diff check đạt. Full suite chạy
  380 test, còn đúng baseline Windows 3 failure + 6 error.

- 2026-10-06 — Thêm menu Xử lý đầu mối: dán ghi chú domain/IP, mặc định chọn
  nhóm backend, so DNS A với IP được cung cấp, tra RDAP contact cho IP đã nêu,
  dùng chung HTTP và recipient/cloaking precheck của Domain Worker. Kết quả
  chỉ giữ trong phiên UI, không tạo job hoặc gửi report; domain trần được
  check ở trang chủ HTTPS và vẫn cần full URL/evidence trước takedown.
  Bổ sung draft yêu cầu điều tra tiếng Anh theo từng domain từ HTTP/precheck
  và mô tả AU888/backend của sếp có caveat; IP/RDAP của sếp vẫn hiện khi DNS
  timeout nhưng được gắn nhãn chưa xác minh. Có ô observation bổ sung, email
  nhận chỉnh được và tải draft `.txt`; không dùng hosting formatter legacy
  vì nó khẳng định origin/credential theft quá mức chứng cứ hiện có.
  DNS dùng system resolver fallback có timeout khi dnspython timeout.
  Đã đạt 7/7 test menu gồm AppTest hai URL cùng domain, 10/10 test tập trung
  gồm navigation và compileall. Full suite chạy 374 test, còn đúng baseline
  Windows đã biết:
  3 failure + 6 error ở mock macOS, Domain Worker stop và Provider Replies cũ.
  Sửa lỗi `AttributeError: module lead_triage has no attribute
  build_investigation_draft` khi app đang chạy giữ module cũ: page tự reload
  helper theo `MODULE_VERSION` và giữ kết quả phiên hiện tại. AppTest tái hiện
  stale module đạt trong `.venv`; full suite 375 test còn cùng baseline 3
  failure + 6 error, compileall đạt.

- 2026-10-04 — Email SMTP bổ sung chữ ký tổ chức gồm vai trò Brand Protection,
  pháp nhân và mã đăng ký; logo OKWIN chuyển vào `assets/email/` và được nhúng
  inline 64 px bằng CID ngay trước Regards. Mail giữ plain-text fallback, HTML
  alternative và evidence attachment độc lập. Provider Replies preview cùng chữ
  ký và dùng chung MIME helper; PyInstaller bundle `assets/`.
  Đã đạt 11/11 test tập trung, AppTest Provider Replies và compileall. Full suite
  chạy 365 test, còn đúng baseline Windows: 3 failure + 6 error ở mock macOS,
  Domain Worker stop và Provider Replies cũ, không thuộc thay đổi chữ ký.
  Sửa tiếp thứ tự nội dung: `Reported URL`, Browser Evidence và cloaking evidence
  đều dùng helper chung để chèn trước Regards, không còn lọt giữa email liên hệ
  và hai dòng tổ chức trong chữ ký.
  Test thứ tự chữ ký và Browser Evidence đạt; full suite chạy 366 test, vẫn còn
  đúng baseline 3 failure + 6 error không liên quan nêu trên.
  Draft legacy đã chứa Reported URL sai vị trí được chuẩn hóa idempotent: xóa
  đúng marker trùng rồi chèn lại trước chữ ký, không yêu cầu xóa file thủ công.
  Full suite sau migration chạy 367 test, vẫn đúng baseline 3 failure + 6 error.

- 2026-10-02 — Chuẩn hóa 5+5 mẫu webform Registrar/Registry theo góc nhìn người
  đại diện thương hiệu/tổ chức, dùng `We`. Registry khi chưa có delivery Registrar
  chỉ nói tập thể phát hiện URL nghi phishing và submit để review, không còn câu
  meta máy móc phủ định lịch sử report.
  Đã đạt 15/15 test formatter và compileall. Full suite chạy 362 test, còn đúng
  baseline Windows: 3 failure + 6 error ở mock macOS/Domain Worker stop/Provider
  Replies, không thuộc thay đổi nội dung này.

- 2026-10-02 — Webform Registrar/Registry có hai pool riêng, mỗi pool 5 mẫu
  tiếng Anh thận trọng và được chọn ổn định theo domain/ngày. Formatter nhận
  observations đã xác minh theo kiểu opt-in, chỉ ghi số attachment và không lộ
  path; Registry chỉ nhắc lần báo Registrar khi caller xác nhận trạng thái.
  Quick Report giữ nguyên flow, không thêm probe/capture/network step.
  Đã đạt 14/14 test formatter, 17/17 test Quick Report và compileall. Full suite
  chạy 361 test, còn đúng baseline Windows đã biết: 3 failure + 6 error ở mock
  macOS/Domain Worker stop/Provider Replies, không thuộc thay đổi này.

- 2026-09-29 — Bảng registry tĩnh bổ sung `.org` và `.ong` của Public Interest
  Registry (PIR), định tuyến Quick Report tới `https://pir.org/report-abuse/`.
  Luồng chỉ mở form thủ công và cung cấp draft copy-ready, không tự submit.
  Đã đạt 3/3 test tập trung và compileall. Full suite chạy 357 test, còn 3
  failure + 6 error ở baseline Windows/Provider Replies ngoài thay đổi này.

- 2026-09-27 — Provider Replies đồng bộ thêm folder `3 - Cần bằng chứng`
  bên cạnh Inbox, Junk/Spam và `6 - Tự động/Cloudflare`; folder này có thể
  override bằng `imap_evidence_mailbox`, được hiển thị riêng và không gộp vào
  Inbox/Cloudflare.

- 2026-09-27 — Sửa lỗi IMAP `ascii codec can't encode` khi SELECT folder lồng
  `6 - Tự động/Cloudflare`: tên mailbox Unicode được mã hóa Modified UTF-7 ở
  ranh giới lệnh IMAP, còn tên hiển thị/cache vẫn giữ nguyên. Inbox/Junk và
  folder Cloudflare tiếp tục được cô lập lỗi riêng.

- 2026-09-27 — Provider Replies mặc định đọc phản hồi Cloudflare từ folder IMAP
  lồng `6 - Tự động/Cloudflare` theo cấu trúc mailbox thực tế; vẫn hỗ trợ
  override bằng `imap_cloudflare_mailbox` cho từng account. Inbox và Junk/Spam
  tiếp tục được quét độc lập, lỗi folder Cloudflare không làm mất dữ liệu các
  folder còn lại. Đã cập nhật test và tài liệu; không kết nối IMAP thật.

- 2026-09-25 — Cloudflare Form Worker phục hồi membership của job cũ sau
  reload/chuyển trang: snapshot RAM hoặc status thiếu `record_ids` được recover
  theo timestamp + tổng item và checkpoint `job_id` lên ledger. Mọi URL đã vào
  job tiếp tục nằm trong **Tiến trình gửi**; **Bảng loại** chỉ chứa kết quả bị
  loại sau precheck, mặc định ẩn sau toggle để ưu tiên theo dõi job. Có AppTest
  cho worker singleton mới với job lưu trên đĩa và toggle bảng loại.
  Precheck và tiến trình gửi dùng chung một dataframe **Kết quả URL**; không tách
  thêm bảng “Danh sách thực hiện” khi job đã tồn tại.
  Page mở dần theo trạng thái: trước precheck chỉ có input + nút kiểm tra; sau
  precheck mới hiện cấu hình Cookie rồi bảng kết quả bên dưới. Job `COMPLETED`
  hiển thị thông báo hoàn thành với tổng URL thành công/lỗi.
  Page dùng native Streamlit `layout="wide"` giống Domain Worker để tận dụng
  toàn bộ vùng nội dung desktop, không chèn CSS phụ thuộc DOM.
  Atomic replace của ledger/job/input retry có giới hạn khi Windows trả
  `PermissionError` tạm thời; lỗi ghi kéo dài hiển thị trong page thay vì làm
  crash Streamlit. Có test mô phỏng hai sharing violation rồi thành công.
  Bảng **Kết quả URL** cố định thứ tự theo input/precheck trong toàn bộ vòng đời
  job; `current_id` chỉ cập nhật trạng thái/callout, không được dùng để reorder.
  Dashboard batch không dùng `api_token`; `account_id` ưu tiên config và fallback
  từ cookie `curr-account`, được xác thực trước khi tạo job và không persist Cookie.
  Fragment tiến trình tự rerun toàn page khi batch kết thúc để input và action
  được mở khóa ngay, không yêu cầu người vận hành bấm **Dừng**; AppTest page đạt 7/7.
  Parser input nhận dòng có annotation/redirect chain, chỉ trích từng URL thật,
  bỏ nhãn như `(top2)` và mũi tên trước precheck/dedupe; 11/11 test tập trung đạt.
  Đã đạt compileall; full suite 356 test còn đúng baseline
  Windows đã biết gồm 1 failure + 6 error đa nền tảng, không thuộc thay đổi này.

- 2026-09-24 — Cloudflare Worker bổ sung batch theo Cookie phiên Dashboard
  dựa trên đặc tả `cloudflare-abuse-batch-spec (1).md`: nhập nhiều URL, preview
  report đã sinh, giãn cách 0–300 giây và các action gửi/tiếp tục/dừng/retry lỗi.
  Sau phép thử thực tế, adapter chỉ yêu cầu `Cookie`, không yêu cầu `x-atok`, và
  giữ Origin/Referer/x-cross-site-security. Adapter POST đúng host Dashboard và
  nhận response thực tế `result=success` khi có `abuse_rand` không rỗng (vẫn
  tương thích `success=true`); response dedupe hiển thị `msg`, đánh dấu đã gửi
  gần đây và không retry; auth lỗi
  chờ phiên mới, rate limit/challenge tạm dừng, mất response thành `UNKNOWN`
  không tự retry. Page 14 được rút gọn còn Cookie Dashboard, bỏ fallback API và
  extension khỏi UI; input/job status cache theo ngày, quay lại page vẫn theo dõi
  thread đang chạy. UI tách **Danh sách thực hiện**, **Bảng loại** và **Tiến trình
  gửi**; tiến trình chỉ dùng `record_ids` của active job, không suy từ ledger. Lỗi xác định của một
  URL thành `FAILED` rồi tiếp tục; retry chỉ lấy URL lỗi. Nút xóa cache giữ nguyên
  lịch sử `SUBMITTED`/`ALREADY_SUBMITTED`/`UNKNOWN`. Phiên chỉ ở RAM; ledger schema
  v4 và job status sanitize không chứa Cookie. Các bảng chỉ hiển thị scope URL
  trong input hiện tại. Bảng loại chốt sau precheck cho URL đã xử lý từ trước/không
  Cloudflare; record đã vào job giữ nguyên trong tiến trình kể cả terminal và retry
  merge `record_ids` cũ. URL đang gửi được sort lên đầu với icon `▶`. Record cũ ngoài scope không hiện nhưng vẫn chống gửi trùng. Extension
  Quick Report vẫn độc lập.
  Đã đạt 16/16 test tập trung gồm AppTest giữ terminal trong tiến trình và merge
  job khi retry; compileall và diff check đạt. Full suite chạy 347 test, còn đúng baseline Windows đã biết: 1 failure + 6
  error ở mock macOS `PosixPath` và stop process dùng `fcntl`/`SIGKILL`/`killpg`.
  Test chỉ dùng mock, không mở form hay gửi report thật.

- 2026-09-22 — Nâng nội dung webform Registrar/Registry để mô tả rõ suspected
  phishing/brand impersonation, luồng registration/sign-in có thể dẫn tới nội
  dung lừa đảo, nguy cơ cung cấp credential/thông tin cá nhân và hậu quả account
  compromise, fraud hoặc financial loss. Narrative vẫn dùng `appears`/`may`/`if
  confirmed`, không tự khẳng định thu thập OTP/payment; Registrar yêu cầu điều
  tra toàn flow và bảo toàn record, Registry yêu cầu phối hợp sponsoring
  registrar và áp dụng biện pháp tương xứng. Có test giữ full URL, câu rủi ro,
  ngôn ngữ điều kiện và chặn claim/scan không có bằng chứng. Đã đạt 26/26 test
  tập trung gồm AppTest Quick Report. Full suite chạy 331 test, còn 1 failure +
  6 error Windows baseline ở mock macOS `PosixPath` và stop process dùng
  `fcntl`/`SIGKILL`/`killpg`, không thuộc formatter webform. Không gửi report thật.

- 2026-09-21 — Web Form Assistant v2.8.0 bổ sung adapter XYZ.COM LLC v1.0.0
  cho ticket Anti-Abuse `gen.xyz/account/submitticket.php?step=2&deptid=6`.
  Quick Report và bảng Registry của `.xyz` cùng họ TLD XYZ dùng endpoint mới;
  extension điền reporter, loại Phishing, domain và draft nhưng không sửa CSRF,
  upload attachment, xử lý CAPTCHA hoặc submit. Host permission chỉ thêm
  `gen.xyz`; có test contract adapter, allowlist, URL mapping và UI source.
  Đã đạt 36/36 test tập trung, syntax check toàn bộ JavaScript, compileall và
  diff check. Full suite chạy 331 test, còn 1 failure + 6 error Windows baseline
  ở mock macOS `PosixPath` và stop process dùng `fcntl`/`SIGKILL`/`killpg`, không
  thuộc adapter XYZ. Không mở form bằng task thật hoặc submit report.

- 2026-09-21 — Web Form Assistant v2.7.1 sửa adapter Chống Lừa Đảo v1.1.0 theo
  form SolidJS mới của provider: bỏ selector placeholder tiếng Anh đã mất, scope
  email/URL/select/textarea trong form và chọn loại bằng value `2:PHISHING` thay
  vì text `Phishing`. Giữ `fill_only`, không upload/CAPTCHA/submit; kiểm tra DOM
  công khai chỉ đọc, không dùng task token và không gửi form thật. Đã đạt 9/9
  test extension, syntax check toàn bộ JavaScript, compileall và diff check.

- 2026-09-21 — Quick Report hiển thị caption email tố cáo Registrar/Registry
  ngay trên khối cloaking. Lookup nhẹ dùng WHOIS sẵn có, RDAP khi registrar chưa
  có email và bảng registry tĩnh; không chạy precheck Domain Worker, không tìm
  origin hosting, mở website hoặc capture evidence. Recipient privacy/Cloudflare
  không được hiển thị, registrar có web form giữ route form. Bump runtime cache
  để loại result cũ thiếu field; 14/14 test Quick Report và compileall đạt. Full
  suite chạy 327 test còn 7 lỗi đa nền tảng có sẵn ở Chrome/Domain Worker trên
  Windows (`PosixPath`, `fcntl`, `SIGKILL`/`killpg`), không thuộc thay đổi này.

- 2026-09-21 — Phản hồi NCC đồng bộ thêm folder IMAP `2-Cloudflare` bên cạnh
  Inbox và Junk/Spam; lỗi folder phụ được cô lập, thống kê và bảng email hiển
  thị Cloudflare thành nhóm riêng, Seen all tiếp tục thao tác theo mailbox nguồn.
  Page dùng thống kê của chính lượt fetch theo ngày địa phương thay vì mở lượt
  đếm Inbox/Junk riêng. Thay đổi dùng chung cũng đưa folder này vào nguồn phản
  hồi của Thống kê tổng quát. File chính: `provider_replies.py`,
  `pages/9_Provider_Replies.py`, `general_statistics.py`, test và tài liệu; chỉ
  kiểm thử bằng mock/AppTest, không kết nối IMAP hoặc gửi email thật.

- 2026-09-17 — Điều tra retry bằng log thực: job chọn 13 URL nhưng hai lượt đầu
  tiếp tục timeout SMTP, rồi nghỉ batch theo cấu hình. Sửa bảng ưu tiên trạng thái
  đang/chờ retry thay vì che bởi kết quả lỗi cũ; hiển thị scope và tiến độ theo
  lần chạy. UI/worker dùng chung completion predicate, submit lưu retry_targets
  cụ thể, không coi success của pipeline là SMTP success. Thêm test đi qua lõi
  draft/send với SMTP mock cho 12 failed + 1 pending và retry lỗi lần nữa, cùng
  AppTest trạng thái bảng. Đã đạt 66 test Domain Worker, 322/322 full unittest,
  compileall và diff check. Không sửa runtime data hoặc gửi thật.

- 2026-09-17 — Domain Worker sửa retry nhiều lần: kết quả mới thay dòng URL cũ,
  bộ đếm không cộng dồn attempt, và lượt bị ngắt giữa chừng vẫn có thể chạy tiếp.
  Thêm khóa vòng đời process, ghi PID ngay khi launch và xác minh worker nhả khóa
  trước khi báo dừng. UI có Chạy lại sau stopped/failed và Chạy lại precheck khi
  chưa hoàn tất; không tự restart. Kiểm thử mock SMTP/process và AppTest cho
  stop → chạy lại → retry lỗi lặp lại. Người dùng cho phép tiếp tục khi skill
  developing-with-streamlit bị thiếu trong checkout. Đã đạt 320/320 unittest
  (gồm AppTest), compileall và diff check; hai test bridge localhost được chạy
  lại ngoài sandbox sau lỗi quyền bind socket. Không gửi email/report thật.

- 2026-09-15 — Sửa Quick Report hiển thị `DeltaGenerator` và docstring dài
  sau khi mở form Google Safe Browsing/Microsoft SmartScreen: thay bốn
  conditional expression gọi `st.success`/`st.error` bằng `if/else` statement,
  gồm cả hai nhánh Playwright legacy. Không đổi adapter, payload hoặc
  hành vi submit. File chính: `pages/7_Quick_Report.py`, test UI; tài liệu:
  README, CLAUDE và file này. Đã kiểm tra 11/11 test Quick Report,
  AppTest không có exception, compileall và diff check; không đổi nghiệp vụ
  nên không chạy lại full suite.

- 2026-09-13 — Giảm lag Quick Report: dừng timer kết quả sau khi batch hoàn tất,
  tách mỗi domain thành fragment để click form không render lại toàn danh sách.
  AppTest kiểm tra bấm Google/Microsoft lặp lại bằng mock, không mở form thật.

- 2026-09-13 — Sửa Quick Report mất kết quả khi F5: version nằm trong cache
  dùng chung thay vì session, giữ pending futures và khôi phục danh sách sau
  F5/chuyển menu. Xóa cache dùng callback dọn kết quả/cache lọc/ô nhập. Kết quả
  chỉ giữ trong vòng đời server; có AppTest cho session mới, pending và clear.

- 2026-09-13 — Sửa các nút tự điền Quick Report báo không tìm thấy Chrome trên
  macOS: helper mở Chrome bổ sung app hệ thống/người dùng trên macOS và PATH
  trên Linux, giữ tìm kiếm Windows; lỗi khởi chạy được cô lập và thử ứng viên
  tiếp theo. Kiểm thử bằng mock, không mở form hoặc gửi report thật.

- 2026-09-15 — Web Form Assistant v2.7.0 thêm adapter Registry Co phishing
  v1.0.0 cho Quick Report: chỉ match form chính thức
  `registry.co/report-abuse/form`, điền reporter, loại phishing, brand, domain,
  full URL/path và draft registry. Upload evidence, checkbox thiện chí,
  reCAPTCHA và submit vẫn thủ công; host permission chỉ thêm origin
  Registry Co. File chính: adapter JS, manifest, bridge/worker, Quick Report và
  test extension/UI; tài liệu: README, CLAUDE, Technical Guide, file này và
  skill dự án. Đã kiểm tra syntax toàn bộ JavaScript, 28 test tập
  trung, 308/308 full unittest, AppTest Quick Report, compileall, pip check và
  diff check.

- 2026-09-13 — Microsoft SmartScreen adapter v1.0.5/extension v2.6.5 rollback
  toàn bộ tự chọn ngôn ngữ theo yêu cầu: Quick Report không truyền `language`,
  adapter chỉ điền URL và không tìm/mở/thao tác MWF language combo. Trường ngôn
  ngữ giữ nguyên mặc định của Microsoft; popup recheck chỉ xác minh URL.

- 2026-09-13 — Microsoft SmartScreen adapter v1.0.4/extension v2.6.4 sửa trạng
  thái dropdown mở nhưng không commit: option Vietnamese được focus, gắn class
  `active` theo MWF, phát pointer/mouse; nếu chưa đổi `comboLanguage` thì mở lại
  và phát Enter. Chỉ báo `FILLED` sau khi widget cập nhật thật; không submit hay
  thao tác CAPTCHA.

- 2026-09-13 — Microsoft SmartScreen adapter v1.0.3/extension v2.6.3 sửa MWF
  Combo lần hai: component chỉ cập nhật state gửi đi qua `onSelectionChanged`,
  nên adapter phát chuỗi pointer/mouse event thật sự tới nút và option Vietnamese,
  chờ input `comboLanguage` đổi rồi mới báo `FILLED`. Không gán giả input, submit
  hoặc thao tác CAPTCHA.

- 2026-09-13 — Sửa Microsoft SmartScreen adapter v1.0.2/extension v2.6.2:
  form thật dùng MWF custom combo (`comboLanguage`, `LanguageListButton`,
  `comboLanguageList`) chứ không phải select chuẩn. Adapter click option
  **Vietnamese** rồi xác minh input readonly đã đổi giá trị; không còn kiểm tra
  nhầm text của nút khiến badge luôn `!`. Không submit hoặc thao tác CAPTCHA.

- 2026-09-13 — Web Form Assistant v2.6.1 cập nhật Microsoft SmartScreen adapter
  v1.0.1: Quick Report vẫn truyền `language=Vietnamese`; adapter tìm trường theo
  thuộc tính hoặc caption “What is the main language used on the site?”, chọn
  **Vietnamese** và xác minh selected option trước khi báo `FILLED`. Recheck cũng
  chặn trạng thái hoàn tất nếu ngôn ngữ bị mất sau rerender. Không thay đổi
  submit/CAPTCHA.

- 2026-09-13 — Web Form Assistant rollback panel nổi trên trang theo phản hồi
  trải nghiệm; checklist, **Điền lại** và **Kiểm tra lại** chỉ còn trong popup.
  Badge ghim cũng được khôi phục về bộ ký hiệu `…`, `✓`, `C`, `!`, `×` của
  v2.6.0. File chính: extension `content.js`,
  `background.js`, manifest và test; tài liệu: README, CLAUDE, Technical Guide,
  AGENTS và skill dự án. Không thay đổi hành vi submit/CAPTCHA.

- 2026-09-13 — Web Form Assistant v2.6.0 thêm badge trạng thái theo từng tab:
  `…` đang xử lý, `✓` đã điền/submit, `C` chờ CAPTCHA thủ công, `!` cần thao tác
  và `×` khi lỗi. Service worker chỉ dùng state/message đã sanitize, đổi màu và
  tooltip tương ứng, đồng thời xóa badge/status cũ khi tab navigation. Không đọc
  dữ liệu form và không thay đổi hành vi submit/CAPTCHA. File chính:
  `chrome_extension/cloudflare-profile-worker/{manifest.json,background.js}`,
  test extension; tài liệu: `README.md`, `CLAUDE.md`, `03_Technical_Guide.md`,
  file này và skill dự án. Đã kiểm tra syntax toàn bộ JavaScript, 25 test tập
  trung, 304/304 full unittest, compileall, pip check và diff check.

- 2026-09-13 — Web Form Assistant v2.5.0 nâng popup lên EXT-UI 2: checklist chỉ
  hiển thị tên/trạng thái field, CAPTCHA và submit; thêm **Điền lại**, **Kiểm tra
  lại** và **Sao chép chẩn đoán** trên đúng tab. Content script giữ task/adapter
  trong RAM để refill hoặc validate, không truyền giá trị field; mọi diagnostic
  được sanitize và không action nào submit/click CAPTCHA. File chính:
  `chrome_extension/cloudflare-profile-worker/{manifest.json,background.js,content.js,popup.html,popup.css,popup.js}`,
  adapter Cloudflare, test extension; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này và skill dự án. Đã kiểm tra syntax toàn bộ
  JavaScript, 25 test tập trung, 304/304 full unittest, compileall, pip check và
  diff check.

- 2026-09-13 — Web Form Assistant v2.4.0 bổ sung popup mini khi bấm icon
  extension. Popup chỉ đọc và hiển thị version, hostname, adapter/version,
  trạng thái cùng thông báo gần nhất của đúng tab; content script publish
  metadata đã sanitize, service worker giữ fallback theo tab và popup ưu tiên
  hỏi trực tiếp content script. Không hiển thị task token, email, draft, cookie,
  CAPTCHA và không thêm submit. File chính:
  `chrome_extension/cloudflare-profile-worker/{manifest.json,background.js,content.js,popup.html,popup.css,popup.js}`,
  test extension; tài liệu: `README.md`, `CLAUDE.md`, `03_Technical_Guide.md`,
  file này và skill dự án. Đã kiểm tra syntax toàn bộ JavaScript, 25 test tập
  trung, 304/304 full unittest, compileall, pip check và diff check.

- 2026-09-12 — Tăng chiều cao mặc định riêng của bảng **Kết quả domain** lên
  tối thiểu 300 px để dễ theo dõi batch khi mới có ít dòng; các bảng phụ vẫn co
  theo nội dung. File chính: `pages/6_Domain_Worker.py`, test UI; tài liệu:
  `README.md`, `CLAUDE.md` và file này. Đã kiểm tra 8/8 test UI Domain Worker,
  compileall và diff check; không đổi nghiệp vụ worker nên không chạy lại full suite.

- 2026-09-12 — Sắp xếp lại Domain Worker theo thứ tự **tiến độ → cấu hình gửi
  worker → kết quả domain**. Tách bảng và tiến độ thành hai fragment cùng poll
  mỗi 3 giây để bảng vẫn realtime nhưng không chen lên trên form cấu hình. File
  chính: `pages/6_Domain_Worker.py`, test UI; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này và skill dự án. Đã kiểm tra 58 test tập
  trung, 304/304 full unittest, compileall, pip check và diff check.

- 2026-09-12 — Bảng **Kết quả domain** của Domain Worker chuyển vào fragment
  polling nên cập nhật cùng tiến độ mỗi 3 giây. Bảng dùng đúng một cột **Trạng
  thái tài khoản** để liệt kê mọi account trong job với **Đã gửi / Một phần /
  Lỗi / Chưa gửi** và recipient tương ứng; thêm account không làm rộng bảng, và
  đã bỏ bản sao bảng tĩnh từng chỉ đổi sau full refresh. File
  chính: `pages/6_Domain_Worker.py`, test UI; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này và skill dự án. Đã kiểm tra 58 test tập
  trung, 304/304 full unittest, compileall, pip check và diff check.

- 2026-09-12 — Sửa Domain Worker không tự bật real-time ngay sau nút **Khởi
  chạy worker**: cả precheck, launch gửi và retry đều ghi session poll marker,
  rerun page ngay để fragment bật chu kỳ 3 giây; thêm grace period cho khoảng
  trễ process con tạo/cập nhật status và tự dọn marker khi terminal. Không còn
  phải bấm **Làm mới trạng thái** để kích hoạt polling. File chính:
  `pages/6_Domain_Worker.py`, test UI; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này và skill dự án. Đã kiểm tra 58 test tập
  trung, 304/304 full unittest, compileall, pip check và diff check.

- 2026-09-12 — Bảng **Kết quả domain** của Domain Worker bổ sung cột **Đã gửi
  đến**, hợp nhất và loại trùng địa chỉ nhận từ kết quả job cùng sent ledger
  thành công của ngày hiện tại. Chỉ delivery `sent`/`already_sent` thành công
  được hiển thị; recipient gửi lỗi không bị ghi nhận nhầm là đã gửi. File chính:
  `pages/6_Domain_Worker.py`, test UI; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này và skill dự án. Đã kiểm tra 58 test tập
  trung, 304/304 full unittest, compileall, pip check và diff check.

- 2026-09-12 — Domain Worker DW-2 bổ sung theo dõi active job tự động mỗi 3 giây
  bằng fragment độc lập, ghi/hiển thị `current_stage` và full-rerun đúng một lần
  khi job kết thúc để dừng polling. Form gửi có preview route dự kiến theo
  URL/account/recipient/channel và sent ledger, không gọi đây là exact draft;
  delivery result giữ stage/error code cho lỗi draft validation, SMTP auth,
  connection, send hoặc recipient rejected. Worker vẫn tự sinh/validate draft và
  gửi sau xác nhận, không có SMTP trong preview. File chính: `domain_worker.py`,
  `pages/6_Domain_Worker.py`, test core/UI; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này và skill dự án. Đã kiểm tra 58 test tập
  trung, 304/304 full unittest, compileall, pip check và diff check.
  Sau phản hồi UX, fragment trở thành nguồn duy nhất hiển thị trạng thái/tiến độ/
  batch/countdown; đã bỏ khối metric tĩnh trùng lặp và các bảng ready, cloaking,
  delivery route ít dùng. Page chỉ giữ bảng kết quả domain, tổng delivery và các
  expander lỗi/email cần thiết.

- 2026-09-12 — Domain Worker DW-1 tối ưu UI mà không đổi pipeline gửi tự động:
  gộp lọc nội dung thô và danh sách worker thành một ô nhập, parser bỏ qua ghi
  chú/token lỗi nhưng vẫn chạy URL hợp lệ, tự lọc cache gửi/no-email trước khi
  tạo job; gom link ngoại lệ vào khu `Cần bạn xử lý`, thu gọn KPI/bảng theo dõi,
  bỏ CSS `.stale`, đặt xác nhận gửi thật mặc định tắt và chỉ tự phục hồi job của
  ngày địa phương hiện tại. Job cũ vẫn giữ để audit. File chính:
  `pages/6_Domain_Worker.py`, test UI; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này và skill dự án. Đã kiểm tra 58 test tập
  trung, 302/302 full unittest, compileall, pip check và diff check.

- 2026-09-12 — Sửa Browser Evidence trên bản Windows chia sẻ bị Playwright báo
  thiếu `chromium-*/chrome-win64/chrome.exe`: runtime frozen không còn để Node
  tự suy ra vị trí từ `PLAYWRIGHT_BROWSERS_PATH=0`, mà trỏ tuyệt đối từ
  `sys._MEIPASS` tới `playwright/driver/package.local-browsers`. `build_app.bat`
  kiểm tra executable Chromium và Headless Shell sau COLLECT và dừng build nếu
  artifact thiếu. File chính: `browser_evidence.py`, `build_app.bat`, test
  packaging; tài liệu: `README.md`, `CLAUDE.md`, `03_Technical_Guide.md`, file
  này và skill dự án. Đã build thật với `--clean`, xác minh đủ hai executable
  trong `dist` và không có browser tree trùng; 31 test tập trung, 300/300 full
  unittest, compileall, pip check và diff check đều đạt.

- 2026-09-12 — Thêm adapter extension **GoDaddy Phishing** v2.3.0 cho Quick
  Report: chỉ hiện action tự điền khi registrar khớp GoDaddy, mở đúng
  `legalportal.godaddy.com/abuse/phishing` trên Chrome profile hiện tại và điền
  email, brand, full URL/path, draft registrar. Adapter không tự tích cam kết
  good-faith, không CAPTCHA/submit; host permission chỉ thêm origin GoDaddy.
  File chính: adapter JS, manifest, `cloudflare_profile_bridge.py`,
  `cloudflare_form_worker.py`, `pages/7_Quick_Report.py` và test; không mở hay
  submit form thật trong test. Đã kiểm tra JSON manifest, syntax toàn bộ
  JavaScript, 24 test tập trung, 300/300 full unittest, compileall, pip check
  và diff check.

- 2026-09-12 — Sửa Cloudflare Abuse Reports API HTTP 405: route submit phishing
  dùng đúng `abuse-reports/abuse_phishing` thay vì nhãn UI `phishing`. Payload
  đổi `host_notification` và `owner_notification` từ `send-anon` sang `send`
  để nhất quán với workflow báo cáo bằng danh tính doanh nghiệp.
  Đã kiểm tra 15 test tập trung, 297/297 full unittest, compileall, pip
  check và diff check; test chỉ mock API, không gửi report thật.

- 2026-09-12 — Nâng Cloudflare Form Worker thành **Cloudflare Worker API-first**:
  menu được bật lại; dùng cùng lõi Quick Report để lọc nameserver Cloudflare,
  preview draft/payload và gửi tuần tự các URL được chọn qua Cloudflare Abuse
  Reports API sau xác nhận. Token/Account ID đọc từ `[cloudflare]` trong
  `config.ini` hoặc environment, không ghi ledger/UI/log; nút kiểm tra chỉ
  verify + GET reports. Ledger schema v2 checkpoint kênh, HTTP status và Report
  ID từng URL, chống gửi trùng trong ngày và khóa hai phiên local; extension
  Chrome còn là fallback. File chính: `cloudflare_abuse_api.py`,
  `cloudflare_form_worker.py`, `pages/14_Cloudflare_Form_Worker.py`,
  `phishing_toolkit.py`, navigation, spec, config mẫu và test; tài liệu:
  `README.md`, `CLAUDE.md`, `03_Technical_Guide.md`, file này và skill;
  đã kiểm tra 297/297 unittest, AppTest, compileall, pip check, diff check và
  build PyInstaller; chỉ mock POST, không phát sinh report thật.

- 2026-09-11 — Thêm bộ icon lá chắn cho Web Form Assistant: manifest extension
  v2.2.2 khai báo icon PNG 16/32/48/128 và icon action; source chất lượng cao
  lưu tại `assets/phishingtool-extension-icon-source.png`, còn bản runtime ở
  `chrome_extension/cloudflare-profile-worker/icons/`. Extension đã nạp cần
  Reload trong trang quản lý extension của Chrome; thêm test bảo đảm manifest
  chỉ tới các file tồn tại. Tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md` và file này.

- 2026-09-11 — Sửa build Windows bị phình do Playwright Chromium đóng gói
  hai đường dẫn: ngoài filter `collect_data_files`, spec lọc lại
  `Analysis.datas` và `Analysis.binaries` sau khi PyInstaller hook chèn rồi
  phân loại lại package data, chỉ giữ
  `driver/package.local-browsers` mà runtime frozen sử dụng. Bản trùng
  `driver/package/.local-browsers` khoảng 701 MB không còn làm ZIP tăng kích
  thước; Chromium và Headless Shell vẫn được bundle để Browser Evidence chạy
  offline trên máy nhận. File chính: `PhishingTool.spec`, test packaging;
  tài liệu: `README.md`, `CLAUDE.md`, `03_Technical_Guide.md`, file này và
  skill dự án.

- 2026-09-11 — Thêm **Cloudflare Form Worker** độc lập Domain Worker email:
  dùng phép phát hiện Cloudflare và draft chung với Quick Report, preview URL đã
  chọn rồi mở Chrome hiển thị để chỉ điền hoặc submit sau xác nhận. Ledger JSON
  dedupe ngày địa phương + full URL. Form được điền bởi extension MV3 cục bộ trên
  đúng Chrome profile đang mở qua task localhost one-time; không dùng profile ảo,
  remote debugging hoặc đọc cookie. CAPTCHA/form không xác minh được chuyển
  manual/failed, không bypass. File chính: `cloudflare_form_worker.py`,
  `pages/14_Cloudflare_Form_Worker.py`, navigation, spec và test core/UI; tài liệu:
  `README.md`, `03_Technical_Guide.md`, `CLAUDE.md`, file này và skill dự án.
  Extension v1.0.1 xử lý tải trắng bằng một lần reload, điền confirm email và
  company từ brand, đặt draft đúng trường public evidence thay vì Comments;
  auto-submit fail closed nếu CAPTCHA/required field/success chưa xác minh được.
  Extension v1.0.2 chuyển localhost bridge sang MV3 service worker để không bị
  CSP/CORS của trang chặn; record không nhận callback sau 45 giây chuyển `FAILED`
  với hướng dẫn reload extension thay vì treo ở `OPENING_PROFILE`.
  Extension v1.0.3 hiển thị badge chẩn đoán trên form và worker phải chờ callback
  từng URL trước khi mở URL tiếp theo để tránh challenge/trang trắng do mở dồn tab.
  Extension v1.0.4 không giả định caption Cloudflare là thẻ label: chọn field theo
  vùng DOM gần nhất, loại Comments và hiển thị input/textarea count khi chờ.
  Extension v1.0.5 phải điền/kiểm tra lại cả Email và Confirm email sau React
  rerender. Không tích hợp dịch vụ hay logic tự giải/vượt Cloudflare Turnstile;
  chỉ chờ operator hoàn tất rồi mới tiếp tục submit đã được xác nhận.
  Extension v1.0.6 ánh xạ Confirm email từ caption DOM bất kể caption có phải
  `<label>` hay không, fallback theo input kế tiếp và badge phải báo `2/2`.
  Quick Report dùng cùng profile bridge cho nút Cloudflare duy nhất nhưng luôn
  `fill_only`; không giữ song song link mở form cũ và không cho submit tại page này.
  Phase adapter 1 hoàn tất ở extension v2.0.0: `content.js` chỉ điều phối task;
  `adapters/cloudflare.js` sở hữu selector/fill/validate/CAPTCHA/submit/success
  theo contract chung để thêm provider mới mà không sửa logic Cloudflare.
  Phase adapter 2 hoàn tất ở extension v2.1.0: Quick Report dùng cùng profile
  bridge để tự điền Google Safe Browsing (URL, draft, taxonomy) và Microsoft
  SmartScreen (URL); cả hai luôn `fill_only`, không tự giải CAPTCHA/submit.
  Manifest chỉ cấp quyền ba origin form chính thức và localhost; task bridge
  whitelist provider/field, không nhận dữ liệu browser nhạy cảm. Đã kiểm tra
  syntax toàn bộ JavaScript adapter/coordinator, 288/288 unittest, compileall,
  pip check và diff check; không mở form hoặc submit báo cáo thật trong test.
  Phase adapter 2.1 hoàn tất ở extension v2.2.0: Quick Report thay hai link cộng
  đồng bằng nút tự điền Chống Lừa Đảo và Cốc Cốc Safe; truyền full URL, email,
  draft community trung lập và loại phishing tương ứng. Hai adapter luôn
  `fill_only`, không giải reCAPTCHA/submit; Check Domain vẫn giữ link thủ công.
  Đã đối chiếu DOM read-only của hai form chính thức, chạy syntax check toàn bộ
  adapter, 291/291 unittest, Quick Report AppTest, compileall, pip check và diff
  check; không nhập dữ liệu hoặc submit form thật trong kiểm thử.
  Extension v2.2.1 sửa adapter Cốc Cốc: Material UI Select phải mở bằng
  `mousedown`, chọn option phishing `data-value="1"`, rồi xác minh hidden input
  `name="type"` thực sự là `1` mới được báo `FILLED`. Đã kiểm tra 22 test tập
  trung và 292/292 toàn bộ unittest.

- 2026-09-11 — Sửa build Browser Evidence cho bản Windows share: `build_app.bat`
  cài rõ Chromium + Chromium Headless Shell với `PLAYWRIGHT_BROWSERS_PATH=0`
  trước PyInstaller; spec copy chúng vào `package.local-browsers`, kiểm tra đủ
  browser runtime theo layout hiện tại (`chrome-win64/chrome.exe` và
  `chrome-headless-shell-win64/chrome-headless-shell.exe`), còn bản frozen dùng
  browser đã bundle. Kết quả generic `collect_data_files("playwright")` phải lọc
  `.local-browsers` để tránh đóng gói trùng cây browser nguồn khoảng 701 MB.
  PyInstaller phải chạy với `--clean` để không tái sử dụng Analysis/TOC cũ.
  Phải phân phối toàn bộ
  `dist/PhishingTool/`, không chỉ
  `.exe`; máy nhận không cần cài Playwright/Python. File chính:
  `build_app.bat`, `PhishingTool.spec`, `browser_evidence.py`; tài liệu:
  `README.md`, `CLAUDE.md`, `03_Technical_Guide.md`, file này và skill dự án.

- 2026-09-09 — Chặn `abuse@cloudflare.com` ở precheck, draft và ranh giới SMTP:
  địa chỉ này không được Cloudflare theo dõi và IP/ASN Cloudflare chỉ là proxy/CDN,
  không phải origin hosting đã xác minh. Domain Worker nhận diện contact/ASN/tổ
  chức Cloudflare, không tạo hosting draft/email và loại case chỉ có recipient
  này khỏi Worker; cache precheck trong ngày được làm sạch. Guard SMTP cũng chặn
  draft/job legacy. File chính: `phishing_toolkit.py`,
  `domain_worker.py`, `pages/6_Domain_Worker.py`, test worker; tài liệu:
  `README.md`, `03_Technical_Guide.md`, file này.

- 2026-09-09 — Quick Report dùng hai link chính thức **Mở Google Safe
  Browsing** và **Mở Microsoft SmartScreen** trong Browser Blocking để người
  vận hành kiểm tra/bổ sung nội dung và xác nhận trước khi submit; link mang
  query `url` của đúng URL đang báo cáo. Hai action tự mở/tự điền form bằng
  Playwright được giữ lại trong lõi nhưng tạm ẩn qua feature flag UI; không ảnh
  hưởng nút Playwright thụ động dùng riêng để xác minh cloaking. File chính:
  `pages/7_Quick_Report.py`, test Quick Report; tài liệu:
  `README.md`, `03_Technical_Guide.md`, file này.

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

- Kiểm chứng đợt UI ba cột: FE 34/34, typecheck/lint/build; backend 118 đạt,
  1 skip theo môi trường trên PostgreSQL test sạch; compileall đạt. Browser sáng/
  tối và viewport 390px không tràn ngang. CLAUDE.md không đọc/cập nhật theo chỉ dẫn.

- 2026-10-16: Fixed false SMTP/IMAP egress denial on Windows using system DNS with a 3-second deadline; preserved exact endpoint/public IP checks and separated DNS errors. Backend: 100 passed/67 skipped, focused mail/network: 14 passed. No live mail login/send/read.- 2026-10-17 - Provider Replies removes the browser window checkbox; UI/API
  default to headless. Result images and safe errors remain; show_browser API
  stays compatible. No dependency changes or live mail/probes.

- 2026-10-17 — Phản hồi NCC giữ dòng sau gửi, cập nhật cache trạng thái tại chỗ
  rồi đối chiếu backend; filter needs_reply không loại sent/replied_in_sent.
  Email gốc đóng mặc định. Capture có show_browser mặc định true riêng page này,
  mở Chromium cô lập trên desktop backend; worker/review khác giữ headless cũ.
  Child capture giữ reason code an toàn để hiện timeout/thiếu browser/lỗi điều
  hướng thay vì lỗi chung; giới hạn 180 giây, không lộ stderr hay dùng profile cá nhân.
  FE workflow 7/7 và adapter/mail diagnostics 12/12 đạt, typecheck/lint/build/
  compileall đạt; smoke browser headed about:blank và UI desktop/mobile fixture đạt.
  Không migration/dependency mới; không mail/URL nghi vấn thật.
  Full backend PostgreSQL sạch 254 đạt/2 skip (256 test); legacy 420 giữ baseline
  4 failure/7 error sau chạy lại. Không có lỗi mới trong các test liên quan.

- 2026-10-17 — Phản hồi NCC: tiến độ IMAP thực theo thư mục/số thư qua owned
  snapshot và GET sync-progress, phục hồi qua reload; account lock vẫn giữ,
  run ID cũ không ghi đè mới, completed sau commit, stale báo gián đoạn.
  Mặc định lọc Cloudflare/cần phản hồi; NCC khác chỉ xem. Bỏ gate phải có
  delivery lịch sử cho Cloudflare hợp lệ ở capture/preview/send; giữ thread,
  source account, evidence/hash, explicit approval và UNKNOWN ledger.
  Nút DOM capture hiện kèm lý do thiếu URL/quyền/flag, preview ảnh/control URL.
  Backend PostgreSQL sạch 252 đạt/2 skip; riêng workflow 15/15; legacy 420 giữ
  baseline 4 failure/7 error. Visual QA desktop/mobile bằng fixture, không
  SMTP/IMAP/probe thật; không migration/dependency mới.
  Frontend 99/99 đạt khi chạy tuần tự sau timeout do tải song song; workflow
  7/7, typecheck/lint/build/compileall đạt.

- 2026-10-17 — Phản hồi NCC: giới hạn chiều cao bảng, sticky header, mở case tự
  đến phần xử lý và có nút về danh sách giữ draft; polling không cuộn lại.
  Regression danh sách 30 case kiểm tra focus và giữ draft; không đổi backend/mail.

- 2026-10-17 — Provider Replies web: owned IMAP sync theo account/ngày và folder,
  pure parser/draft legacy canonical chuyển vào backend/compat (root wrapper giữ),
  source-match/thread, seen UIDVALIDITY, original body private artifact, existing
  ReviewCase/Snapshot metadata. Exact preview + upload/capture + legal gate, threaded
  shared MIME/SMTP/Sent/ledger, retry failed và UNKNOWN riêng; reconcile header Sent
  không suy diễn delivery. React menu/trang giữ case, compact layout và partial errors.
  Không migration/dependency/permission mới, không SMTP/IMAP/probe thật.
    Backend PostgreSQL sạch 250 đạt/2 skip (252 test), Provider Replies 13/13,
    FE 96/96; lint/typecheck/build/compileall/pip check và wheel 3/3 đạt.
    Visual QA desktop/mobile bằng fixture; API health 200, route protected 401.
    Legacy 420 giữ baseline 4 failure/7 error. Chạy full backend phải dùng DB
    test sạch: dùng lại DB còn fixture gây sai test admin/queue toàn cục.

- 2026-10-17 — Rà soát tổng luồng bằng DB cô lập/mock: thêm journey test ghép
  precheck, batch thường, hai Review, hai account, partial retry và lọc input lại.
  Phát hiện/sửa route discovery bỏ qua Check cloaking_review/evidence_review dù
  ledger đã sent; nay lọc theo đủ route/account chung, giữ phần thiếu và scope ngày.
  Báo cáo tại docs/testing/flow-audit-2026-10-17.md; không mail/probe/runtime thật.
  Backend DB sạch 237 đạt/2 skip; journey + send 28/28; FE 92/92; wheel 3/3;
  lint/typecheck/build/compileall/pip check đạt. Legacy 420 giữ 4 failure/7 error
  khi chạy lại; lần đầu thêm lỗi lock CSV chập chờn trên Windows, đã ghi báo cáo.

- 2026-10-16 — Điều tra trực tiếp slotcatalog.co: 4 profile browser HTTP 200,
  text similarity 0.997, LIKELY từ IOC đơn profile khiến selector bỏ hết ảnh.
  Canonical selector nay giữ cặp comparison_only từ profile khỏe/nonterminal,
  không tự khẳng định khác biệt; Review có ghi chú và clear lỗi capture cũ.
  Đã nhập hai ảnh chụp thực tế vào đúng owned case, không tạo draft/gửi email.
  FE 92/92, backend PostgreSQL 236 đạt/2 skip, wheel cô lập 3/3;
  lint/typecheck/build/compileall/pip check đạt. Legacy 420 giữ baseline
  4 failure/7 error; regression selector mới đạt. Reinstall backend và reload
  worker lúc idle; không sửa config.ini hoặc thay DNS/proxy.

- 2026-10-16 — Sửa thiếu capture cloaking trong precheck web: worker tự chụp
  candidate bằng detector passive cũ sau HTTP/contact, kể cả LIKELY; no-email
  không chụp. Giữ cặp profile khác biệt mạnh nhất + ảnh khác nội bộ, Review hiện
  sẵn ảnh/tên profile và nút chụp lại; không auto approve/send. Capture ngoài DB
  transaction, lỗi giữ queue và thông báo, không mất evidence đã duyệt.
  Backend PostgreSQL sạch 236 đạt/2 skip; cloaking 15/15; FE 92/92 (2 workers);
  wheel 3/3, lint/typecheck/build/compileall đạt. Legacy 419 giữ 4 failure/7 error.
  Worker all/precheck đã reload lúc idle; không probe URL/gửi mail thật.

- 2026-10-16 — Cloaking Review web triển khai current-day owned detail/images,
  ba quyết định, upload atomic hai ảnh đối chiếu hoặc 1–3 ảnh report thường riêng,
  exact preview + approve/send sync, skip, passive capture process riêng với ảnh
  candidate chờ duyệt. Tái dùng preview/MIME/SMTP/Sent/ledger Domain Worker;
  giữ sent/unknown, case lock, partial retry và original detector observations.
  Không migration, dependency hoặc permission mới; không gửi/probe thật.
  FE 91/91; backend PostgreSQL sạch 231 đạt/2 skip; Cloaking Review 12/12 gồm
  regression nguồn HTTP mới, capture subprocess và ảnh nội bộ không attach.
  Wheel cô lập 3/3, typecheck/lint/build/compileall/pip check đạt. Browser fixture
  desktop 1440px/mobile 390px không lỗi/tràn ngang. Legacy 419 giữ 4 failure/7 error.

- 2026-10-16 — Sửa Evidence Review legacy ẩn tài khoản sau preview đầu: source
  jobs đều không ghi scope thì tiếp tục hiện mọi owned enabled account, preview
  explicit bổ sung union scope, giữ ledger; source job có scope vẫn giới hạn cũ.
  Backend PostgreSQL sạch 221 đạt/2 skip, Evidence Review 10/10; compileall đạt.
  Legacy 419 giữ baseline 4 failure/7 error. Không migration hoặc SMTP thật.

- 2026-10-16 — Evidence Review sửa lỗi case legacy thiếu required_account_ids:
  cho mở case, chọn explicit tài khoản enabled thuộc owner và bind tại preview
  dưới case lock. Không chạy precheck lại, không suy đoán default account; scope
  đã lưu tiếp tục bắt buộc, giữ exact preview/hash và ledger chống gửi trùng.
  Xác nhận case thực tế mở được, có 2 tài khoản owned để chọn; không gửi thật.
  Backend 220 đạt/2 skip, FE 88/88, lint/typecheck/build/compileall đạt;
  legacy 419 giữ baseline 4 failure/7 error. Không migration/cấu hình mới.

- 2026-10-16 — Domain Evidence Review web đối chiếu legacy: case ngày/full URL,
  1–3 ảnh thumbnail/atomic, tài khoản nguồn, exact preview rồi gửi SMTP sync không
  job. Shared dispatcher/ledger/Message-ID/Sent, partial retry giữ sent/unknown;
  scoped advisory lock, owned image route, reset confirmation khi sửa ảnh/account,
  reload preview/images/deliveries. Sent đủ nguồn mới ẩn case; source ready không
  treo batch nhờ workspace đối chiếu ledger, không sửa source check/version khi đọc
  trạng thái để giữ binding của exact preview/Sent. Không migration/dependency/config
  mới, không email/probe thật.
  FE 88/88; backend PostgreSQL cô lập 219 đạt/2 skip, wheel cô lập mới 3/3;
  lint/typecheck/build/compileall/pip check đạt. Legacy 419 giữ baseline
  4 failure/7 error. Browser fixture desktop/mobile 390px không tràn ngang trang;
  reload worker lúc idle, API health 200.

- 2026-10-16 — Thu gọn account cell: chỉ email một lần, badge/sent counts cùng
  dòng, ẩn zero errors/unknown và bỏ Chi tiết gửi/khung card. Giữ nguyên metadata
  delivery/API/ledger và trạng thái từng account, không đổi nghiệp vụ SMTP.
  FE 85/85 với regression label=email không lặp, per-account partial/retry/reload;
  lint/typecheck/build/compileall đạt, không gửi mail thật.

- 2026-10-16 — Sửa send-summary 409 sau pause: chuyển control-mode guard khỏi
  summary sang _start. Pause/cancel đọc tổng kết/ledger bình thường, giữ chặn
  start sai trạng thái. Regression đọc summary không đổi mode/counts và kiểm tra
  read-only source lỗi thực tế đạt; không gửi mail hoặc sửa runtime/job hiện tại.
  Backend PostgreSQL cô lập 211 đạt/2 skip; focused pause/cancel summary + chặn
  start đạt. Legacy 419 giữ baseline 4 failure/7 error; compileall và API health
  đạt. Không migration/config mới.

- 2026-10-16 — Thu gọn Theo dõi gửi email thành flex-wrap strip chung batch,
  domain progress, email counters và countdown compact; giảm heading/padding,
  bỏ lịch batch tiếp theo lặp dòng. Không đổi nghiệp vụ/job/timer lifecycle.
  FE 85/85, focused 22/22 với countdown compact; lint/typecheck/build/compileall
  đạt. Không thay backend/database hoặc gửi mail thật.

- 2026-10-16 — Gộp UI precheck/send trong một vùng và một bảng domain; khi gửi
  thu KPI precheck vào details, chỉ nhóm thống kê email/batch. Chi tiết lỗi, số lần
  thử và Message-ID nằm trong account cell; bỏ bảng SMTP thứ hai. Observer cache
  send-summary giúp đồng bộ ID trước workspace polling, không thêm request/API.
  FE 85/85; regression một table/ẩn KPI precheck khi gửi, per-account retry,
  details lỗi/Message-ID, pause/reload và lịch sử đạt. lint/typecheck/build/
  compileall đạt. Không sửa backend/database/SMTP trong thay đổi UI này.

- 2026-10-16 — Gửi batch tách Tạm dừng/Tiếp tục/Ngừng hẳn qua control_mode JSON,
  reuse cooperative stop checkpoint, pause giữ owner workspace và khôi phục F5;
  cancel chặn resume/retry/start source đó, giữ ledger/pending/history. /pause alias
  /stop, /cancel cùng permission stop+CSRF; không schema/config mới, không gửi thật.
  FE 85/85; backend PostgreSQL cô lập 211 đạt/2 skip, gồm pause/resume/checkpoint
  in-flight cancel và chặn restart. lint/typecheck/build/compileall đạt; legacy
  419 giữ baseline 4 failure/7 error. API health 200.

- 2026-10-16 — Bảng ready cập nhật delivery theo job query dùng chung; trạng thái
  domain + khối kết quả từng account (tên/email, đã gửi/tổng, lỗi/chưa rõ), KPI sent
  chỉ tăng khi mọi account/recipient/channel SENT. Start/retry/reload giữ nguồn DB;
  missing delivery không được suy ra sent. Chi tiết delivery có Gửi từ rõ ràng.
  Không đổi API/database/SMTP. FE 84/84, gồm hai account/hai recipient, partial→retry
  complete/KPI/reload; lint/typecheck/build/compileall đạt, không gửi mail thật.

- 2026-10-16 — Lọc no-email từ Check DB trước precheck theo owner/ngày/domain;
  no_email_count/reason riêng, metadata cũ mặc định 0. A/B no-email + C/D sent
  được loại trước probe, chỉ E/F tạo job. Không cache lỗi/chưa rõ/mâu thuẫn,
  không bỏ phần route gửi còn thiếu; all-filtered không tạo job, giữ ngày hiện có.
  FE 83/83, backend PostgreSQL cô lập 209 đạt/2 skip; regression latest terminal
  theo updated_at, khác owner/ngày/path, all-filtered và chỉ probe E/F đạt.
  lint/typecheck/build/compileall đạt; legacy 419 giữ baseline 4 failure/7 error.
  Không migration/config mới hoặc gửi mail thật trong kiểm thử.

- 2026-10-16 — Domain Worker cập nhật bảng ready tăng dần trong precheck, thẻ KPI
  riêng/cỡ lớn, highlight URL running; gửi có metric email/domain đang gửi và
  countdown HH:MM:SS từ next_run_at, zero chờ worker xác nhận. Giữ xác nhận gửi,
  owner lock/ledger; không đổi API/database/SMTP. FE 82/82 và regression incremental
  polling/countdown/reload/zero; lint/typecheck/build/compileall đạt.

- 2026-10-16 — UX Domain Worker chỉ tổng kết precheck, bỏ bảng/danh sách URL;
  chỉ cấu hình khi còn mục cần gửi, start chuyển sang theo dõi batch X/Y theo
  domain JobItem. Banner completed/partial, progress native, màu cyan–blue/xanh
  lá/vàng có nhãn chữ và dark mode. Không đổi backend/SMTP/database.
  FE 80/80, lint/typecheck/build và compileall đạt; kiểm thử empty outcome,
  running batch 1/10 và completed banner, không gửi mail/check URL thật.

- 2026-10-16 — Rà soát batch precheck→SMTP: chung owner lock cho create/start/resume/
  retry, workspace API phục hồi active IDs/history; chờ xác nhận giữ lượt tới send/
  explicit close. Form không thay batch đang chạy, lịch sử đọc lại được. Lọc URL
  chuẩn hóa/trùng/đã gửi đủ mọi route-account trước probe; all-filtered không tạo
  job. Retry giữ SENT/UNKNOWN/Message-ID, refresh exact plan từ evidence còn hợp lệ,
  chỉ xử lý failed/unattempted; UI có attempts/lỗi/Message-ID. Finalize merge metadata,
  recovery theo lease/intent không khóa vĩnh viễn. Không migration/cấu hình mới;
  giữ policy theo ngày hiện có, không xóa lịch sử/runtime hoặc gửi mail thật để test.
  FE 78/78; backend PostgreSQL cô lập 207 đạt/2 skip, gồm wheel 3/3;
  lint/typecheck/build/compileall/pip check đạt. Legacy 419 giữ baseline
  4 failure/7 error. Reload worker khi không có precheck/send đang chạy.

- 2026-10-16 — Khôi phục đầy đủ narrative Browser Evidence legacy cho email worker:
  projection giữ control/link DOM, navigation nguồn/đích/redirect và form signals
  tổng hợp đã redact; render qua formatter chung sau validate owned ảnh/hash.
  Plain/HTML có đủ mô tả, href, bước tái hiện và phạm vi capture trước chữ ký; không
  dùng block generic viết riêng. DOM cũ mất metadata phải precheck lại nếu cần gửi,
  không đoán dữ kiện hay sửa email đã gửi. UI chỉ Đã gửi/Hoàn tất gửi, bỏ nhãn lưu
  bản sao thành công; IMAP lưu Sent tự động như cũ, chỉ báo khi có lỗi thực tế.
  Sửa MIME composer giữ đúng HTML part cho logo CID, không gắn logo vào manifest;
  hai ảnh DOM + JSON giữ ba attachment riêng. Kiểm thử đầu-cuối đối chiếu nguyên
  khối nội dung mẫu trong plain/HTML và cấu trúc MIME, không gửi/check URL thật.
  FE 73/73, backend PostgreSQL riêng 198 đạt/2 skip, wheel 3/3; legacy 419 giữ
  baseline 4 failure/7 error. Lint/typecheck/build/compileall/pip check đạt; backend
  editable đã đồng bộ formatter mới, worker idle nạp lại, API health 200.

- 2026-10-16 — Sửa thiếu bước lưu Sent khi migrate SMTP: legacy có `_imap_save_sent`
  sau SMTP, bản web đã bỏ sót; đính chính nhận định trước. Worker gọi chung helper
  sau checkpoint SMTP accepted, status copy riêng, lỗi IMAP không retry SMTP. Helper
  sửa parse quoted/SPECIAL-USE folder, kiểm APPEND OK, timeout/TLS và dedupe Message-ID.
  IMAP dùng account owned/allowlist; phục hồi chỉ bản sao từ approved immutable plan,
  giữ Message-ID/ngày gốc, không gửi lại SMTP. UI hiện kết quả lưu Sent từng email.
  FE 73/73, backend PostgreSQL riêng 196 đạt/2 skip, wheel 3/3; legacy 419 giữ
  baseline 4 failure/7 error (4 test Sent mới đạt). Lint/typecheck/build/compileall/
  pip check đạt. Cài lại backend editable để đồng bộ helper ngoài repo cwd;
  worker nạp lại sau khi idle. Phục hồi bản sao Sent cho các lượt đang đối soát,
  không SMTP resend; status copy lưu riêng trên từng attempt.

- 2026-10-16 — Đối soát batch báo hoàn tất nhưng không thấy Sent: ledger có hai
  SMTP accepted, sender không có IMAP APPEND Sent. UI đổi nhãn thành SMTP đã chấp
  nhận/Hoàn tất xử lý batch, đếm outcome duy nhất và giải thích chưa xác minh inbox/
  bản sao Sent. Không IMAP hoặc gửi lại, không sửa ledger đã gửi. Cập nhật tài liệu.
  FE 73/73, lint/typecheck/build/compileall đạt; chỉ đổi UI, không đổi SMTP/backend.

- 2026-10-16 — Triển khai Domain Worker gửi batch normal theo yêu cầu: precheck chỉ
  hiện số đếm, hoàn tất mới có danh sách còn cần gửi/cấu hình 5 domain/5 phút mặc định.
  Account scope giữ từ precheck; Start có permission/CSRF/xác nhận, server khóa plan/
  logo/ảnh trước enqueue, không draft/preview từng dòng. Worker domain_worker_send/all
  lưu lịch nghỉ, intent trước SMTP/checkpoint từng thư; retry failed giữ sent, unknown
  không tự retry. Dedupe owner/ngày/domain/account/channel/recipient như legacy, bỏ
  capture nếu đã gửi đủ; ngày mới không dùng batch/ledger hôm qua. DB giữ metadata/
  tiến độ/sổ gửi, ảnh/body private store. Cloaking vẫn cách ly, chưa approve/send web.
  FE 72/72, backend PostgreSQL riêng 195 đạt/2 skip, wheel 3/3; lint/typecheck/build/
  compileall/pip check đạt. Chrome fixture desktop/mobile kiểm counts→batch→F5 không
  POST lại; SMTP mock kiểm retry partial/Message-ID/UNKNOWN/day reset. Legacy 415
  giữ baseline 4 failure/7 error. Worker idle đã reload; không gửi thư thật.

- 2026-10-16 — Đối chiếu lại Domain Worker legacy: chọn account trước precheck,
  giữ Job.account_scope và account nguồn trong queue; bỏ Xem draft/Preview từng dòng.
  Danh sách thường chỉ nhận ready có email + evidence; no-email loại, cloaking/
  thiếu ảnh sang page riêng. Evidence Review nhận 1–3 ảnh + thumbnail, validate rồi
  đưa các check nguồn về ready; cloaking web mới có queue đọc, chưa approve/send.
  Sửa artifact root tương đối lệch CWD API/worker; phục hồi 7 file đúng hash vào
  thư mục chung, toàn bộ 9 artifact hiện có hợp lệ. Không gửi/check URL thật.
  FE 70/70, backend PostgreSQL riêng 183 đạt/2 skip, wheel 3/3; lint/typecheck/
  build/compileall/pip check đạt. Chrome fixture desktop/mobile xác nhận account
  scope, ready-only và F5 không POST lại. Hai worker idle đã reload; API health 200.
  Legacy 415 giữ baseline 4 failure/7 error.

- 2026-10-16 — Theo yêu cầu dùng lại logic cũ, capture web gọi trực tiếp hàm chung
  capture_normal_report_evidence với mặc định HTTP/DNS, DOM rồi passive fallback;
  bỏ override http_probe/network_guard, DNS pool mới và helper DNS capture không dùng.
  Adapter chỉ kiểm output ảnh/manifest/hash và chuyển sang owned artifacts. Ghi chú
  này thay thế cơ chế DNS/NET capture riêng của các mốc ngay trước đó.
  Capture đúng URL đã thành công hai ảnh bằng defaults; worker idle đã reload.
  FE 72/72; backend PostgreSQL riêng 178 đạt/1 skip, wheel 3/3; typecheck/lint/
  build/compileall/pip check đạt. Legacy 415 giữ baseline 4 failure/7 error.

- 2026-10-16 — Sửa tiếp capture web sau khi tái hiện trên URL vừa báo lỗi: DNS
  dnspython timeout nhưng resolver Windows trả IP public. Browser egress chuyển
  mặc định sang system DNS bounded 3 giây/pool 4, vẫn kiểm mọi IPv4/IPv6 và mỗi
  redirect/subresource; thêm mã egress_dns_failed. Đã capture đúng URL thành công
  hai ảnh DOM nguồn–đích, không gửi report. Kết quả precheck cũ giữ để audit.
  FE 72/72; backend PostgreSQL riêng 181 đạt/1 skip, wheel 3/3; typecheck/lint/
  build/compileall/pip check đạt. Legacy 415 giữ baseline 4 failure/7 error.

- 2026-10-16 — Sửa capture web thất bại do backend venv thiếu Playwright: khai báo
  dependency, cài môi trường hiện tại, kiểm tra Chromium và chụp DOM nguồn–đích
  bằng fixture loopback. Worker startup kiểm browser trước claim, lỗi capture có mã
  whitelist để UI giải thích. Mở sửa quyền/tên/mô tả admin theo yêu cầu; role save
  tự materialize permission mới thiếu DB, sync giữ lựa chọn admin/user. Đã đồng bộ
  catalog runtime và restart hai worker idle; không sửa kết quả precheck cũ hoặc
  tự mở lại URL nghi vấn. FE 72/72; backend PostgreSQL riêng 179 đạt/1 skip,
  packaging wheel 3/3; typecheck/lint/build/compileall/pip check đạt. Legacy 415
  giữ baseline 4 failure/7 error. Chrome kiểm form sửa admin desktop/mobile.

- 2026-10-16 — Domain Worker web thêm exact email preview cho ready đã checkpoint:
  chọn account owned, server resolve recipient, personalize bằng lõi cũ; khóa
  From/Subject/plain/HTML, logo inline, 1–3 ảnh + manifest JSON. Default logo được
  materialize thành owned artifact, không sửa profile; kiểm check/result/manifest
  hash, account/profile/evidence versions, revision và expiry 30 phút. F5 GET
  restore, quyền riêng domain_worker.preview + CSRF; chưa có approval/send SMTP.
  FE 71/71; backend PostgreSQL sạch 174 đạt/2 skip, gồm packaging 3/3; typecheck/
  lint/build/compileall/pip check đạt. Legacy 415 giữ baseline 4 failure/7 error.
  Chrome fixture kiểm desktop/mobile/F5; không check URL, SMTP/IMAP hoặc gửi thật.

- 2026-10-16 — Logo mail mặc định bắt buộc, upload/thay PNG/JPEG <=2 MB và reset
  ngay settings/mail; profile riêng/hash/version/audit, default không cần activation.
  Settings hiển thị mail connectivity policy trước thao tác, khóa endpoint chưa cho
  phép; bật local tests cho ba endpoint mail hiện có, không bỏ public DNS/TLS guard.
  Chuyển canonical toolkit/cloaking/browser/quick actions vào backend/compat,
  root wrappers giữ legacy; wheel không lấy source ngoài BE, worker helper ở BE.
  Tài liệu giải thích bốn biến browser/mail và mục tiêu cuối chỉ BE/FE, chưa xóa
  các workflow legacy còn chờ migration; không đụng config.ini/runtime hiện có.
  FE 67/67; backend PostgreSQL riêng 164 đạt/2 skip; wheel cô lập 3/3 gồm logo
  mặc định và MIME; typecheck/lint/build/compileall/pip check đạt. Legacy 415 giữ
  baseline 4 failure/7 error. Chrome xác nhận default logo/uploader và các nút
  thử kết nối được mở đúng policy; không SMTP/IMAP login hoặc gửi thật.

- 2026-10-16 — Áp dụng logo WIN cho hồ sơ mail web bằng owned immutable artifact,
  asset nằm trong backend package, không đọc config.ini. Draft DW có logo trước
  Regards và HTML CID; MIME composer portable được đối chiếu HTML/bytes với legacy.
  Tài liệu hóa worker chạy thường trực, .bat chỉ shortcut local, topology deploy
  và điều kiện chuyển hết compatibility vào BE trước khi dọn source root.
  Đã áp dụng logo cho hồ sơ được yêu cầu qua UI; JPEG backend trùng SHA-256 ảnh
  nguồn. FE 66/66, backend PostgreSQL riêng 162 đạt/2 skip, packaging 3/3;
  lint/typecheck/build/compileall/pip check đạt. Legacy giữ 4 failure/7 error.

- 2026-10-16 — DW-01: Domain Worker web precheck batch owned, HTTP cloaking/lookup
  song song, no-email/evidence/cloaking queue, capture thường có flag và ready gate;
  giữ operation/F5, stop/resume/retry failed, unknown không tự chạy lại. Draft
  tham khảo email Registrar/Registry/Hosting đối chiếu formatter/file legacy,
  pure shared routing/hosting renderer, không SMTP/IMAP/submit thật. Còn bước
  web review/Playwright cloaking và exact account preview/send. Sửa shared clock,
  cooperative stop và flush EvidenceSet trước Artifact sau kiểm thử PostgreSQL.
  FE 65/65; backend PostgreSQL riêng 159 đạt/2 skip; packaging 3/3 gồm wheel;
  typecheck/lint/build/compileall/pip check đạt. Legacy 415 giữ baseline
  4 failure/7 error. Chrome xác nhận menu/trang mới, không chạy URL/gửi thật.

- 2026-10-16 — Bỏ override width/padding content riêng Quick Report để dùng
  cùng AppShell và breakpoint với các page khác; không đổi nội dung hay nghiệp vụ.

- 2026-10-16 — Đồng bộ UI web gọn trên layout chung, Dashboard, admin, mail
  và Check Domain: sidebar 240px/header 56px, bảng/form/dialog giảm padding.
  Thông số Check Domain chuyển sang nhãn/giá trị theo bốn nhóm responsive,
  giữ profile/raw JSON trong details. Guidance bố cục gần Quick Report.
  Không đổi pipeline, không check/gửi thật; kiểm tra FE và visual QA local.
  FE 61/61, lint/typecheck/build/compileall đạt. Chrome kiểm tra Check Domain
  desktop sáng/tối và mobile 390px, cùng Dashboard/Quick Report/admin/mail.

- 2026-10-16 — Sửa vận hành lỗi Check Domain “Không tải được mẫu báo cáo”:
  backend editable nạp bản compatibility site-packages cũ thiếu formatter email.
  Cài lại đúng backend venv, reload API và xác nhận mẫu hiện trên kết quả cũ,
  không check/gửi lại. Bổ sung regression subprocess ngoài repo cwd và wheel
  cho browser/CDN/Registrar email/Registry webform+email; tài liệu hóa reinstall.
  Backend 89 đạt/60 skip, packaging 3/3 (gồm wheel cô lập), compileall/pip check
  đạt; legacy 415 giữ baseline 4 failure/7 error. Chrome xác nhận 8 mẫu hiện lại.

- 2026-10-16 — Sửa draft Check Domain theo đúng kênh nhận: Registry dùng
  `registry_contact` đã check, webform lấy formatter webform và email lấy biến
  thể email Registry cũ; nếu có cả hai thì hiện hai mẫu riêng. Registrar giữ
  mẫu email và webform riêng theo routing cũ. Không ghi draft file/gửi thật.

- 2026-10-16 — Check Domain web bổ sung mẫu email registrar khi tìm được địa chỉ
  hợp lệ: tách chọn recipient khỏi nhánh ghi draft legacy, lọc WHOIS privacy,
  hiện To/Subject và biến thể email cũ theo domain + ngày trong bộ nhớ. Registrar
  chỉ nhận webform dùng mẫu webform riêng, không hiện mẫu email. UI hiện thông số trực
  tiếp với chữ gọn, nhóm mẫu thành cảnh báo trình duyệt, CDN, Registrar và
  Registry; nội dung dài mở khi cần copy. Không gửi SMTP/submit form. Kiểm tra
  guidance 10 đạt/1 skip, smoke formatter thật đạt, compileall đạt; legacy 415
  giữ baseline 4 failure/7 error.

- 2026-10-16 — Sửa Check Domain web theo mục đích tra cứu một domain: bỏ nút
  capture/upload gây hiểu nhầm (capture backend headless, cần worker riêng và
  flag mặc định tắt), bỏ câu coi ảnh là bắt buộc. Thêm endpoint read-only owned
  report-guidance, dùng formatter/routing legacy để hiện form và nội dung copy;
  không tạo draft file hoặc gửi thật. Test mock + formatter thật không gọi mạng;
  FE 59/59, backend 87 đạt/60 skip, typecheck/lint/build/compileall đạt.

- 2026-10-16 — CD-02 React phần kiểm tra/evidence: đọc đối chiếu
  `pages/1_Check_Domain.py`, thêm trang một URL, giữ operation theo user qua F5,
  hiện kết quả đầy đủ theo nguồn, upload 1–3 ảnh với thumbnail, capture job riêng
  và xem artifact owned. Chưa có draft/preview/send API nên chưa hiện gửi; không
  check URL hoặc gửi thật. FE 60/60, typecheck/lint/build; backend 86 đạt/60
  skip, compileall đạt. DB integration cần PostgreSQL test cô lập.

- 2026-10-16 — Sửa Check Domain web về đúng một domain/full URL mỗi thao tác:
  POST nhận `target`, response trả một `check`, job nội bộ chỉ có một item.
  Quick Report và Domain Worker vẫn là luồng nhiều URL; không check/gửi thật.

- 2026-10-16 — Browser Evidence web cho phép capture trực tiếp như legacy khi
  bật flag và chạy worker riêng; proxy egress là tùy chọn. Giữ kiểm tra public
  URL cho từng HTTP redirect và browser request. Direct mode cần cô lập mạng
  worker để giảm rủi ro DNS rebinding tại thời điểm kết nối; không mở URL thật.

- 2026-10-16 — NET-01 app boundary: HTTP probe redirect từng hop qua proxy,
  Playwright route guard mọi request, chặn private IPv4/IPv6/DNS/port; capture
  lúc đầu yêu cầu proxy egress, sau đó đã bỏ ràng buộc này; flag vẫn mặc định tắt.
  Mail connectivity test mặc định tắt,
  chỉ nhận exact host:port allowlist + public DNS khi bật. External proxy/firewall
  và legacy full-check egress còn mở; backend 83 đạt/60 skip, legacy 415 giữ
  baseline 4 failure/7 error, compileall/pip check đạt. Không gọi mạng thật.

- 2026-10-16 — Sửa lỗi backend không khởi động sau EVID-01: `backend/.venv`
  chưa được cài lại nên thiếu `python-multipart` dù dependency đã khai báo.
  Cài lại package vào đúng venv; xác nhận uvicorn `--app-dir src --reload`
  startup và `GET /api/v1/health/live` trả 200. Không đổi nghiệp vụ.

- 2026-10-16 — EVID-01: Check Domain web có upload 1–3 ảnh PNG/JPEG, owned
  evidence set/manifest/hash và download qua artifact route hiện có; capture
  Browser Evidence vào job riêng, flag mặc định tắt cho tới khi có egress policy.
  Backend 75 đạt/60 skip (PostgreSQL test cô lập chưa có); legacy 415 giữ baseline
  4 failure/7 error; compileall/pip check đạt. Không mở URL nghi vấn thật.

- 2026-10-16 — CD-01: backend web có owned Check Domain create/read API, worker
  full investigation dùng lõi `run_check`, DTO whitelist và quyền submit VT riêng.
  Web không ghi CSV/draft/manifest legacy, không gửi report; React/evidence/send
  thuộc CD-02/EVID-01. Backend 71 đạt/59 skip (chưa có PostgreSQL test cô lập),
  legacy 415 giữ baseline 4 failure/7 error; compileall/pip check. Chỉ fixture/mock,
  không probe URL thật.

- 2026-10-16 — Tách /login Telegram riêng; / thành dashboard protected theo
  permission. 401 route nội bộ về /login, đăng nhập thành công về /, logout về
  /login; pending vẫn bị chặn. Callback lỗi/expired về login với mã allowlist.
  Login card cyan–blue responsive sáng/tối, không đổi OIDC/cookie/security policy.
  FE 56/56; backend 120 đạt/2 skip; typecheck/lint/build/compileall. Visual QA login
  390px + desktop sáng/tối bằng fixture cô lập; không login Telegram thật.
  CLAUDE.md không đọc/cập nhật theo chỉ dẫn hiện hành.

- 2026-10-16 — Sửa thứ tự thẻ Quick Report: lượt hiện tại theo input order, lịch
  sử ngày ngoài lượt xếp sau; số # khớp worker tuần tự. Không đổi worker/runtime.
  Thêm regression daily response đảo thứ tự. FE 47/47,
  typecheck/lint/build và compileall đạt. CLAUDE.md không đọc/cập nhật.

- 2026-10-16 — Daily Quick Report mặc định ẩn thẻ thuộc lượt cũ (mọi state) và
  cache hit, reset lựa chọn ẩn khi tạo lượt mới. Clear cache thành công dọn
  operation/session pointer để thẻ biến mất; giữ DB history, không tự check lại.
  Test 10+10/clear/recheck20 + clear failure; FE 46/46, typecheck/lint/build và
  compileall. Không sửa backend; CLAUDE.md không đọc/cập nhật theo chỉ dẫn.

- 2026-10-16 — Daily Quick Report phân biệt pending/running lịch sử không có
  job item: DTO not_queued, UI Chưa có job và hướng dẫn nhập lại URL. Không đổi
  runtime history, tự enqueue hoặc check lại. Kiểm tra membership cùng owner và
  job_type; giữ pending cho record thực sự có job. FE 45/45 + typecheck/lint/build;
  backend 118 đạt/2 skip, compileall đạt. CLAUDE.md không đọc/cập nhật.

- 2026-10-16 — Sửa Quick Report theo danh sách tích luỹ ngày: ô nhập luôn hiện,
  GET /quick-reports/daily đọc DB owner/workflow/ngày, chọn latest mỗi URL;
  không mất kết quả cũ khi thêm batch/F5/xoá Redis cache. Mặc định hiện cũ và
  mới, checkbox ẩn là tuỳ chọn; bộ đếm cũ/mới tách khỏi provenance Redis. API
  operation cũ giữ tương thích; không migration. FE scenario 10+2/F5, backend
  A/B/day/latest/permission được kiểm thử. FE 44/44 + typecheck/lint/build,
  backend 118 đạt/2 skip trên PostgreSQL sạch; compileall đạt.
  CLAUDE.md không đọc/cập nhật.

- 2026-10-16 — Quick Report thêm cache/new counts, mặc định ẩn kết quả cache,
  toggle xem lại và all-cache guidance; nút Xoá cache hôm nay theo permission,
  dùng API owner/day hiện có. Không xoá lịch sử/provenance/delivery hoặc tự
  check/gửi lại; khoá clear khi operation hiện tại đang tải/chạy. FE 43/43,
  typecheck/lint/build; cache backend 6 đạt/1 integration skip; compileall đạt.
  CLAUDE.md không đọc/cập nhật theo chỉ dẫn hiện hành.

- 2026-10-16 — Bỏ khối Báo cáo nic.top, component export và download helper khỏi
  React Quick Report theo yêu cầu; FE 38/38, typecheck/lint/build và compileall đạt. Giữ backend export tương thích, không đổi
  check/routing/registry form. Cập nhật README/technical guide/implementation plan.
  CLAUDE.md không đọc/cập nhật theo chỉ dẫn hiện hành.

- 2026-10-16 — Quick Report ẩn nhóm form không áp dụng, chia đều cột còn lại,
  nút báo cáo xanh cyan–blue nổi bật. Bật Netcraft submit flag cục bộ theo yêu
  cầu và restart FastAPI; file mẫu giữ false, loại khai báo trùng. Không gửi
  report thật; FE regression + typecheck/lint/build, compileall/pip check.
  CLAUDE.md không đọc/cập nhật theo chỉ dẫn hiện hành.

- 2026-10-16 — Quick Report dense layout: nhóm GSB/Microsoft/Chống Lừa Đảo/
  Cốc Cốc/Netcraft toàn hàng; dưới là ba cột CDN/Registrar/Registry. Form thường
  mở URL trực tiếp, bỏ modal copy trùng. Netcraft một lần bấm tạo exact preview
  rồi gửi API, bỏ xác nhận UI theo yêu cầu; giữ server permission/flag/ledger/
  dedupe/UNKNOWN. Giảm khoảng cách và chiều cao preview. FE 38/38, typecheck/lint/build và compileall đạt. Kiểm thử chỉ mock,
  không gửi report thật; CLAUDE.md không đọc/cập nhật theo chỉ dẫn hiện hành.

- 2026-10-16 — Quick Report chia ba cột báo cáo cộng đồng/hạ tầng/registrar-registry,
  mẫu nội dung inline theo dịch vụ, copy và nhãn Mở và tự điền rõ ràng. Thông tin
  hạ tầng/contact chung hàng. GET form-drafts thêm include_providers opt-in, giữ
  owner/permission/profile/no-store và formatter legacy; không tạo task/gửi report
  khi xem. Microsoft không có draft văn bản; Netcraft giữ exact approval riêng.

- 2026-10-16 — Thu gọn Quick Report React: hạ tầng icon/tên, routing hai dòng
  email; ẩn Cloaking HTTP trên UI. Sắp nút Google/Microsoft/Chống Lừa Đảo/Cốc Cốc/
  Cloudflare/Registrar/Registry; form có adapter chỉ hiện nút extension, form khác
  mở draft đúng channel + form URL trong dialog. Giữ nguyên backend và approval
  Netcraft. FE 34/34 test, typecheck/lint/build và compileall đạt; không gửi report
  thật. CLAUDE.md không đọc/cập nhật theo chỉ dẫn hiện hành.

- 2026-10-16 — Sửa Quick Report GET 500 khi API chạy từ backend cwd: Hatch
  editable path và wheel force-include chỉ bốn module compatibility cần thiết,
  không config.ini/.env/runtime. Test subprocess ngoài repo cwd và isolated
  wheel (không editable .pth) đạt; full backend chạy từ backend cwd 119/119,
  compileall/pip check đạt. Chrome khôi phục 6/6 kết quả đã có và preview draft
  thành công, không check/gửi lại. CLAUDE.md không đọc/cập nhật theo chỉ dẫn cũ.

- 2026-10-16 — Audit registrar/registry Quick Report: bổ sung toàn bộ manual
  form/draft từ 23 registrar form rule và 76 registry entry; 48 registrar email
  rule vẫn dùng resolver legacy. Sửa XYZ family và com.co/CentralNic routing
  theo form URL; API draft owned/private và FE preview/copy. Quick Report cũ
  chỉ hiển thị email contact, không có SMTP action. Không tuyên bố toàn bộ web
  migration hoàn tất; audit ở docs/quick-report-routing-audit.md. Không submit
  hoặc kết nối mail thật; CLAUDE.md không đọc/cập nhật theo chỉ dẫn cũ.
  Backend cô lập 117/117, FE 33/33 + typecheck/lint/build; legacy 415 giữ baseline
  4 failure + 7 error; compileall/pip check/diff check đạt.

- 2026-10-16 — Sửa vận hành Quick Report không có kết quả: khai báo dependency
  requests/python-whois/dnspython/ipwhois, startup preflight trước claim và
  heartbeat trong check lâu. Thêm run_quick_report_worker.bat; FE phân biệt
  pending/queued với running. Lượt người dùng chọn được phục hồi đúng 4 item
  lỗi thiếu dependency và hoàn tất 4/4 bằng worker thật; worker giữ chạy nền,
  không SMTP/IMAP/provider submit. Backend fixture 112/112, FE 32/32 và
  typecheck/lint/build; legacy giữ baseline 415 / 4 failure / 7 error;
  compileall/pip check đạt. CLAUDE.md không đọc/cập nhật theo chỉ dẫn cũ.

- 2026-10-16 — Sửa handshake extension trả `Error: origin_not_allowed` làm FE
  mất phân loại lỗi: worker trả error.message, FE tương thích cả prefix legacy.
  Tách runtime receiving-end-not-found/channel-closed. Chrome thật xác nhận
  bản extension mới đã nhận message nhưng chưa lưu đúng origin; không tự sửa
  permission hoặc mở form. FE 31/31 + lint/build; CLAUDE.md giữ nguyên.

- 2026-10-16 — Sửa chẩn đoán Quick Report extension: tách thiếu/sai ID, thiếu
  browser runtime, origin bị chặn, timeout; trim ID public và sanitize lỗi.
  Retry có loading/toast, sequence guard không để ping cũ ghi đè kết quả mới.
  UI hiển thị ID/origin. FE 30/30 và build; kiểm tra Chrome thật xác nhận env
  hợp lệ nhưng messaging API chưa được expose, không khẳng định đã kết nối.
  Không mở form/submit; CLAUDE.md không đọc/cập nhật theo chỉ dẫn cũ.

- 2026-10-16 — Sửa lỗi thêm mail bị báo chung “account conflicts”: thiếu khóa
  mã hóa trả 503 có hướng dẫn, trùng identity theo owner trả 409 riêng, lỗi
  SMTP/IMAP precondition có thông báo cụ thể; không lộ SQL/secret. Dev xác minh
  chưa có sender/encrypted account rồi khởi tạo master key trong backend/.env,
  kiểm tra AES-GCM roundtrip và dev reload. Không lưu mail mẫu/kết nối mail thật.
  Backend PostgreSQL/Redis cô lập 110/110; legacy 415 giữ baseline 4 failure +
  7 error; compileall/pip check/diff check đạt. CLAUDE.md không đọc/cập nhật.

- 2026-10-16 — Sửa form thêm mail đơn lẻ: nhãn tùy chọn fallback SMTP username,
  validation hiện rõ thay vì khóa nút im lặng; lỗi API nằm trong modal. Port
  465/587 tự chọn Implicit TLS/STARTTLS. SMTP-only không phụ thuộc profile,
  IMAP/proxy hoặc số lượng account. FE 25/25, typecheck/lint/build; không đổi
  backend hoặc kết nối mail thật. CLAUDE.md không đọc/cập nhật theo chỉ dẫn cũ.

- 2026-10-16 — FE thêm Sonner toast theo theme, shared mutation feedback,
  xác nhận khóa user/thu hồi phiên/xóa role/tắt mail; pending buttons, read-error
  retry, offline banner, render error boundary và tiến độ Quick Report. Toast
  không chứa raw API data; polling giữ im lặng. Logout chỉ kết thúc UI khi API
  thành công và lỗi mạng /me có retry. Chỉ thay FE, không gọi provider/mail thật.
  FE 24/24, typecheck/lint/build, npm audit và compileall/pip check đạt; Chrome
  automation không kết nối nên chưa visual verify trên tab hiện tại. CLAUDE.md
  không đọc/cập nhật theo chỉ dẫn phiên trước.

- 2026-10-16 — Quick Report bắt buộc hồ sơ theo owner có brand_name,
  contact_name và contact_email hợp lệ. API readiness chỉ trả missing/ready;
  dependency chặn check mới, task form, Netcraft preview/submit và export bằng
  409 khi chưa đủ cấu hình. React hiện checklist/link cấu hình trước khi mount
  workflow; kiểm tra lại sau lưu. Không bắt SMTP/IMAP cho Quick Report.

- 2026-10-16 — Quick Report bổ sung exact Netcraft preview/approval với ledger
  owner/ngày/full URL, durable intent, FAILED retry explicit và UNKNOWN không
  tự gửi lại; migration 0004. Export nic.top XLSX giữ full URL và literal cells.
  Kết nối tám provider form theo routing server, public contact payload và
  extension fill-only. Gửi Netcraft mặc định tắt bằng backend feature flag;
  kiểm thử fixture/mock, không submit provider thật. CLAUDE.md không đọc/cập
  nhật theo chỉ dẫn phiên trước.

- 2026-10-15 — UI-04: làm mới React theo visual system indigo–violet, gradient
  primary/active nav, header/card có chiều sâu nhẹ, avatar và icon tiles; nhóm
  nhập URL Quick Report rõ hơn, action bar responsive. Giữ light/dark và toàn bộ
  permission/workflow. FE 16/16 + typecheck/lint/build đạt; Chrome desktop/mobile
  390×844 hai mode không tràn ngang. Chỉ lọc URL fixture cục bộ, không chạy check
  hay gửi report. CLAUDE.md không đọc/cập nhật theo chỉ dẫn phiên trước.

- 2026-10-15 — UI-03: React có light/dark, lưu preference trình duyệt và khởi tạo
  trước render; Tailwind dark variant theo `.dark`, form/bảng/cảnh báo dùng chung
  palette. Cài Radix Themes 3.3.0, shared Button/AppDialog/ThemeToggle; dialog hỗ
  trợ focus trap/Esc/return focus, trang tải theo route. FE 16/16 test cùng
  typecheck/lint/build đạt; đã xem Chrome hiện tại ở desktop và mobile 390×844.
  Chỉ đổi frontend, không chạy nghiệp vụ hay gửi thật. CLAUDE.md không đọc/cập nhật
  theo chỉ dẫn phiên trước.

- 2026-10-15 — Theo yêu cầu mới, role `user` seed toàn quyền nghiệp vụ trừ
  `users.*` và `roles.*` (51/56). Sync thường giữ lựa chọn admin; CLI thêm
  `sync-rbac --reset-user-permissions` áp dụng bộ mặc định cho DB hiện có và
  thu hồi phiên thành viên. Admin vẫn toàn quyền, owner isolation không đổi.
  Backend DB/Redis test riêng 92/92; legacy 415 test giữ 4 failure + 7 error
  baseline; compileall/pip check đạt. Đã áp dụng và xác minh DB dev user 51 quyền.
  CLAUDE.md không đọc/cập nhật theo chỉ dẫn phiên trước.

- 2026-10-15 — Hoàn tất RBAC-03: admin có toàn catalog (56 quyền); user ban đầu
  rỗng, permission do admin chọn và sync không ghi đè. Migration 0003 bỏ quyền
  user seed cũ; bootstrap chỉ gán admin; UI cho sửa permission user, khóa admin.
  Gán role user cũng chặn vượt quyền. Backend PostgreSQL/Redis riêng 92/92;
  frontend 13/13 + typecheck/lint/build; migration downgrade/upgrade/check,
  compileall và pip check đạt. Legacy 415 test giữ 4 failure + 7 error baseline.
  Không gửi SMTP/IMAP/provider thật. CLAUDE.md không đọc/cập nhật theo chỉ dẫn cũ.

- 2026-10-15 — Chuẩn hóa initialization RBAC: thêm CLI idempotent
  `domaincheck-admin sync-rbac` chạy sau Alembic để tạo permission catalog và
  system role trước khi có admin. Bootstrap admin đầu tiên gán cả `admin` và
  `user`, nên có quyền quản trị lẫn các workflow an toàn mặc định; user ngoài
  vẫn `pending` cho tới khi admin kích hoạt.

- 2026-10-15 — Sửa dependency cài đặt backend cho SQLAlchemy 2.1 async:
  dùng `sqlalchemy[asyncio]` để clean virtualenv luôn có `greenlet` trước khi
  Alembic import async engine. Lỗi thiếu `greenlet` không còn yêu cầu người dùng
  cài tay một dependency ngoài khai báo dự án.

- 2026-10-15 — Tách cấu hình web đúng package boundary: FastAPI và PostgreSQL
  Compose dùng `backend/.env` tạo từ `backend/.env.example`; frontend chỉ nhận
  biến public trong `frontend/.env` khi cần. Root không còn `.env.example` và
  backend không đọc root `.env`; `compose.yaml` vẫn ở root với vai trò điều phối.

- 2026-10-15 — Sửa Telegram web login: token exchange chuyển sang HTTP Basic theo
  OIDC Telegram chính thức; Vite dev callback/cookie thống nhất ở
  `127.0.0.1:5173`, backend đọc `backend/.env`, có status API không lộ secret và callback
  error allowlist. FE đổi sang dark investigation console với login readiness,
  sidebar/topbar cyan–indigo responsive; thiếu cấu hình thì khóa login và chỉ dẫn
  callback. Backend PostgreSQL 17 + Redis 7 đạt 90/90; frontend 12/12 +
  typecheck/lint/build; visual Playwright đạt ở 1440×1000 và 390×844. Legacy giữ
  baseline 415 test / 4 failure / 7 error. Mock tests không gọi Telegram thật.
  `CLAUDE.md` không đọc/cập nhật.

- 2026-10-15 — Hoàn thành EXT-01: FastAPI tạo capability task Quick Report owned,
  one-time exchange/status token và device registration; React Axios mở Google/
  Microsoft qua Web Form Assistant v3, poll trạng thái và fallback link thủ công.
  Extension kiểm exact app/provider origin, chỉ `fill_only`, giữ payload tạm trong
  `chrome.storage.session`; token không vào provider URL/DB/Redis/localStorage.
  PostgreSQL 17 + Redis 7 backend 87/87, test form-task/extension 15/15, frontend
  11/11 + typecheck/lint/build và JS syntax đạt. Chỉ fixture/mock, không mở form hay
  submit thật. Legacy giữ baseline 415 test / 4 failure / 7 error. Sáu adapter còn
  lại thuộc EXT-02; `CLAUDE.md` không đọc/cập nhật.

- 2026-10-15 — Chuẩn hóa frontend sang feature-based architecture: `app` giữ
  provider/router/layout; `features` tách api/hooks/components/pages/schemas/utils;
  `components/shared` và `lib/http|query` dùng chung. Thay toàn bộ HTTP runtime bằng
  Axios 1.20.0 cùng cookie, CSRF interceptor và error normalization. Triển khai phần
  check thật của QR-02: lọc explicit, permission gate, POST operation, polling, result
  cards và phục hồi F5 theo user. Provider autofill/Netcraft/nic.top vẫn khóa vì chưa
  có API capability/idempotency, nên QR-02 còn `in_progress`, không dựng success giả.
  FE typecheck/lint/10 test/build đạt; không gọi network/provider/SMTP/IMAP/capture.
  `CLAUDE.md` không đọc/cập nhật theo chỉ dẫn người dùng.

- 2026-10-15 — Hoàn thành backend `QR-01`: API owned tạo/đọc Quick Report operation,
  ghi target/check/job/item trước probe và trả `202`; worker process riêng gọi đúng
  compatibility `run_cdn_check()`, checkpoint từng URL, giữ full URL/order/duplicate.
  Redis cache tách owner/ngày/engine và DB ghi provenance; sanitizer loại raw HTML,
  credential/local path. Test chỉ dùng fake adapter, không network/provider thật;
  backend PostgreSQL + Redis 82/82;
  Alembic check/compileall/pip check/diff check đạt;
  legacy giữ baseline 415 test / 4 failure / 7 error. React page/form task/export còn
  QR-02/EXT-01. `CLAUDE.md` không đọc/cập nhật.

- 2026-10-15 — Hoàn thành `DELIVERY-01`: delivery được materialize từ exact preview
  đã duyệt bằng business identity phía server, không dùng preview/hash để né dedupe;
  Message-ID ổn định, row claim + optimistic version và attempt intent được ghi trước
  side effect. Chỉ `FAILED` retry explicit; lease/mất checkpoint sau intent phục hồi
  thành `UNKNOWN`. Sửa `OwnedRepository` refresh row sau khi chờ `FOR UPDATE`, chặn race
  hai session cùng claim. Preview mới đã duyệt chỉ rebind delivery còn pending/failed,
  không mở lại SENT/SENDING/UNKNOWN. Backend PostgreSQL + Redis 76/76; Alembic check, compileall,
  pip check và diff check đạt. Legacy giữ đúng baseline 415 test / 4 failure / 7 error;
  chưa có route gửi workflow hoặc SMTP/IMAP/provider/capture thật. `CLAUDE.md` không
  được đọc/cập nhật theo chỉ dẫn.

- 2026-10-15 — Hoàn thành `PREVIEW-01`: exact subject/plain/HTML body và delivery
  plan được lưu trong immutable private JSON artifact; PostgreSQL giữ revision,
  fingerprint, expiry, account/profile version, recipients và evidence references.
  Advisory lock cấp revision; approve/send-boundary validation chặn revision cũ,
  hết hạn, account/profile/evidence thay đổi, artifact mutation và cross-owner ID.
  Backend PostgreSQL + Redis 69/69, test tập trung 11/11; Alembic check xác nhận không
  cần migration mới, compileall/pip check/diff check đạt. Legacy giữ baseline 415 test,
  4 failure + 7 error. Không gửi SMTP/IMAP/provider/capture thật.

- 2026-10-15 — Hoàn thành `JOB-01` cho backend web: service tạo job/item bền vững
  giữ input order và account scope; worker claim/heartbeat/start/checkpoint bằng lease
  token + optimistic version; stop/resume cooperative và retry đúng failed subset.
  Lease chết giữa item hoặc trong lúc stop được phục hồi thành `UNKNOWN`, không tự retry.
  PostgreSQL + Redis backend 64/64; 12/12 test job; Alembic check, compileall và pip
  check đạt. Legacy giữ đúng baseline 415 test / 4 failure / 7 error. Chỉ dùng fixture,
  không SMTP/IMAP/provider/capture thật.

- 2026-10-15 — Hoàn thành DB-02 và ISO-01 foundation: thêm `OwnedRepository` cố định
  owner cho get/list/count/update/delete; Target, Artifact và Mail Settings dùng boundary
  chung. Job claim đồng thời bằng `SKIP LOCKED`, reclaim lease hết hạn và heartbeat
  yêu cầu token + optimistic version; stop theo owner. Test PostgreSQL xác minh A/B cùng
  URL/account, direct/nested ID, HTTP 404, composite FK và cùng pooled connection không
  rò dữ liệu. PostgreSQL 17 + Redis 7 fixture đạt 57/57; Alembic check, compileall và
  pip check sạch. Không gọi network, SMTP/IMAP, capture hoặc runtime thật.

- 2026-10-15 — Hoàn thành ART-01 cho web multiuser: thêm private artifact port và
  filesystem adapter với key UUID scope owner/evidence, staging/rollback batch,
  validation PNG/JPEG MIME/signature/byte/dimension/SHA-256 và integrity check khi
  đọc. `GET /api/v1/artifacts/{id}` kiểm owner + permission feature, không lộ local
  path/tên nguồn. Test tập trung 17/17; PostgreSQL 17 tạm chạy 48 test (47 đạt,
  1 Redis integration skip), Alembic check sạch; legacy giữ baseline 415 test /
  4 failure / 7 error. Không capture URL, gửi SMTP, đọc IMAP hay dùng runtime thật.

- 2026-10-15 — Hoàn thành CACHE-01 cho web multiuser: Redis key có
  owner/feature/business-day/bucket/version và chỉ giữ hash của input; TTL tới đúng
  nửa đêm timezone IANA, gồm DST. Get/set fail-open khi Redis mất; clear theo
  permission chỉ SCAN/UNLINK namespace owner và không chạm ledger/evidence/DB.
  PostgreSQL 17 + Redis 7 tạm đạt 39/39, Alembic check sạch; flush/restart Redis
  chỉ tạo cache miss. Không đọc `CLAUDE.md` theo chỉ dẫn hiện hành.

- 2026-10-15 — Web MAIL-02 thêm action kiểm tra SMTP/IMAP theo owner và quyền
  `mail_accounts.test_own`: SMTP chỉ TLS/login, IMAP chỉ TLS/login/logout, không
  gửi thư hoặc đọc mailbox. Secret chỉ giải mã trong runtime, lỗi/audit sanitize;
  React chỉ hiện nút khi có quyền. PostgreSQL test tạm 31/31, FE 6 test cùng
  typecheck/lint/build đạt; không kết nối mail thật. NET-01 vẫn là release gate
  trước khi cho phép egress production.

- 2026-10-15 — Hoàn thành MAIL-01 cho web multiuser: migration `0002` bảo đảm một
  sender mặc định mỗi owner; API + React quản lý profile, SMTP, IMAP folder Unicode,
  proxy và chữ ký. Secret AES-GCM bind owner/account, GET chỉ trả cờ và PATCH bỏ
  field secret giữ ciphertext; xóa account là soft-disable. PostgreSQL 24/24 và
  Alembic upgrade/check/downgrade/upgrade đạt; không mở SMTP/IMAP hoặc gửi mail.

- 2026-10-15 — Hoàn thành RBAC/admin foundation của web multiuser: API + React
  quản lý user/role, kích hoạt/khóa, gán role, revoke session, audit allowlist,
  bootstrap admin explicit theo Telegram OIDC subject và transaction bảo vệ admin
  hoạt động cuối cùng. Lỗi API có request ID và DTO allowlist. PostgreSQL test tạm
  đạt 21/21 gồm concurrent last-admin; FE typecheck/lint/4 test/build đạt. Full
  legacy giữ đúng baseline 415 test, 4 failure + 7 error. Không gọi Telegram,
  SMTP/IMAP hoặc provider thật; chưa mở workflow điều tra/gửi.

- 2026-10-15 — Bắt đầu triển khai migration web theo plan M0/M1. Thêm
  `docs/implementation-plan.md`, parity/storage/ADR; backend FastAPI, metadata và
  Alembic 24 bảng PostgreSQL, owner composite constraints, ExecutionContext,
  permission catalog, AES-GCM secret envelope, OIDC PKCE/state/nonce primitives
  và repository isolation; auth DB tạo pending user, opaque session, `/me`, CSRF
  logout và authz-version invalidation. Thêm React/TypeScript/Vite/Tailwind foundation gọi
  health API thật; chưa mở workflow nghiệp vụ hoặc gửi. PostgreSQL test upgrade,
  `alembic check`, downgrade và A/B isolation đạt; backend 16 test khi không DB
  (5 integration skip), DB run 15/15 trước test route bổ sung, FE
  typecheck/lint/2 test/build đạt. Full legacy vẫn
  đúng baseline 415 test, 4 failure + 7 error; compileall và pip check đạt. Không
  đọc config thật, import runtime, mở URL, gửi SMTP hay gọi live Telegram.

- 2026-10-15 — Hoàn thiện prompt FE/BE cho nhiều user: mục 6.5 quy định owner,
  actor, sender account, DB/cache/job/evidence isolation; mục 18.1–18.3 bắt buộc
  lập implementation plan có task/dependency/acceptance và cập nhật tiến độ.
  Bổ sung kiểm thử A/B, concurrent user và release gate; README dẫn tới bản mới.
  Chỉ đổi tài liệu, chưa thực hiện plan, migration hoặc thay đổi ứng dụng.
  Compileall, diff check và kiểm tra cấu trúc prompt đạt; không chạy unittest
  vì không thay mã nguồn hoặc hành vi hiện tại.

- 2026-10-15 — Chốt PostgreSQL + Redis theo lựa chọn chủ dự án trong prompt
  FE/BE; thêm `targets`, phân biệt URL ổn định với từng lần check, thời điểm ghi
  DB và 24 bảng nền tảng. MongoDB chỉ còn phần so sánh; chưa tạo database hoặc
  migration thật. Chỉ cập nhật prompt, README và ghi chú này.

- 2026-10-15 — Thêm `PROMPT_TACH_FE_BE_REACT.md`: khảo sát chức năng và đặc tả
  chuyển FE React/Vite/Tailwind, BE Python API, Telegram login, RBAC, mail config
  theo user; đề xuất PostgreSQL + Redis và phương án MongoDB thay thế. Tài liệu
  quy định parity, migration, worker/evidence/extension và bốn menu cần bỏ khi
  triển khai. README dẫn tới prompt; chưa đổi code, dependency hoặc runtime.
  Compileall, kiểm tra cấu trúc Markdown và diff check đạt; không chạy unittest
  vì chỉ đổi tài liệu. Số 415 test trong prompt là đếm tĩnh AST, không phải kết
  quả chạy test.

- 2026-10-06 — Lead Triage module version 23 định dạng report theo các mục
  Brand Impersonation, Observed Redirect Chain, Related Backend Findings và
  Evidence and Request; giữ nội dung chứng cứ từ Domain Worker, thêm registrar
  khi ghi chú có `reg=`, và liệt kê toàn bộ backend đã cung cấp kèm trạng thái
  liên hệ chưa xác minh. Không tự khẳng định đánh cắp thông tin, tiền hoặc OTP
  nếu evidence không cho thấy. 45/45 test Lead Triage và compileall đạt. Không
  gửi SMTP hoặc mở URL thật.

- 2026-10-06 — Lead Triage module version 22 giữ mọi đầu mối backend người dùng
  cung cấp trong report; mỗi dòng nêu IP cung cấp, DNS A hiện tại, RDAP và ghi
  rõ khi IP RDAP không xuất hiện trong DNS A hiện tại; đồng thời nêu
  có/không có giao điểm DNS/URL. NCC được yêu cầu xác minh trước khi liên kết;
  không khẳng định backend chưa liên kết đang phục vụ target. 45/45 test Lead
  Triage và compileall đạt. Full suite 412 test còn 3 failure + 6 error nền
  Windows ngoài thay đổi này. Không gửi SMTP hoặc mở URL thật.

- 2026-10-06 — Lead Triage module version 21 rút report có DOM evidence:
  không lặp thời điểm/DNS target không liên quan, lời yêu cầu điều tra, URL và
  hướng dẫn ở phần review focus. Giữ các bước tái hiện/redirect trong evidence
  chung, chỉ nêu điểm cần đánh giá và backend có liên hệ; bản Việt tóm tắt.
  44/44 test Lead Triage và compileall đạt. Full suite 411 test còn baseline
  Windows 3 failure + 6 error ngoài thay đổi; không gửi SMTP hay mở URL thật.

- 2026-10-06 — Lead Triage chỉ hiển thị report của domain vừa bấm Check; Check
  batch hiển thị nhóm vừa chọn. Vẫn giữ cache các domain khác để quay lại xem,
  xóa observation English dùng chung khi chuyển domain đơn để tránh lẫn nội dung.
  44/44 AppTest/unit test Lead Triage và compileall đạt.

- 2026-10-06 — Sửa nội dung gửi Lead Triage theo case 8cewd.buzz: formatter
  registry dùng “our brand identity”; draft cũ được sửa ngữ pháp; chỉ ghép
  backend có liên hệ qua DNS A hoặc hostname trong Browser Evidence hợp lệ.
  Giữ URL/redirect chứng cứ, bỏ yêu cầu điều tra lặp khi đã có DOM evidence và
  câu so sánh với dịch vụ chính thức chưa được cung cấp. Module version 20;
  test Lead Triage cô lập sent log để không đọc trạng thái gửi runtime.
  44/44 test Lead Triage và compileall đạt; full suite 411 test còn đúng baseline
  Windows 3 failure + 6 error ngoài thay đổi. Không gửi SMTP hay mở URL thật.

- 2026-10-06 — Lead Triage lưu snapshot review v1 tại
  `data/lead_triage_cache.json` bằng ghi atomic: ghi chú, parsed targets,
  precheck/draft, đường dẫn DOM evidence và widget review được khôi phục qua
  chuyển menu/F5, không tự check hay gửi. Nút **Xóa cache** xóa snapshot và
  state UI, giữ sent log/evidence audit. Cache backend khôi phục buộc kiểm tra
  lại khi operator bấm Check. 42/42 test Lead Triage/AppTest (cache dùng thư
  mục tạm), compileall và diff check đạt. Full suite 409 test còn baseline
  Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage gộp logo và thông tin tổ chức từ config chung vào
  bản sao tài khoản SMTP nếu account thiếu trường chữ ký; dùng cùng bản sao
  cho preview và SMTP để logo không chỉ xuất hiện ở UI. Giữ email gửi theo
  account đã chọn. 37/37 test Lead Triage, compileall và diff check đạt; full
  suite 404 test còn baseline Windows 3 failure + 6 error. Không gửi SMTP thật.

- 2026-10-06 — Rà lại nội dung report Lead Triage: addendum ghi thời điểm check
  UTC, phân biệt DNS A quan sát với IP backend do người vận hành cung cấp, chỉ
  gắn tổ chức RDAP khi IP khớp DNS, yêu cầu NCC xác minh mối liên hệ và xử lý
  khi xác nhận vi phạm. Sửa ngữ pháp sau khi loại AU88/AU888, subject Việt nêu
  nghi phishing; giữ nguyên phần report gốc Domain Worker. Bản Việt dịch thêm
  các mẫu registrar cố định gồm mô tả, yêu cầu, câu kết, ngày phát hiện và
  VirusTotal. 38/38 test Lead Triage, compileall và diff check đạt; full suite
  405 test còn baseline Windows 3 failure + 6 error. Không gửi SMTP thật.

- 2026-10-06 — Preview Lead Triage hiển thị logo ngay trước Regards bằng native
  Streamlit, dùng lại formatter chữ ký và resolver logo của email gửi. Trước
  khi chọn account hiện profile tổ chức chung; sau khi chọn hiện đúng account,
  vẫn có plain text exact trong expander. 36/36 test Lead Triage, compileall và
  diff check đạt; full suite 403 test còn baseline Windows 3 failure + 6 error.
  Không gửi SMTP thật.

- 2026-10-06 — Lead Triage thêm nút kiểm tra lại email nhận theo đúng resolver
  Domain Worker tại từng draft. Kết quả cập nhật địa chỉ mặc định và kênh;
  lỗi xóa precheck recipient cũ và khóa gửi cho đến khi tra lại thành công.
  Module version 18.
  35/35 test/AppTest liên quan, compileall và diff check đạt. Full suite
  402 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage thêm bước **Phân tích ghi chú** explicit trước khi
  hiện danh sách; dán/sửa văn bản chỉ cập nhật ô nhập, ẩn danh sách cũ và không
  chạy precheck/network. Thu gọn ô nhập, hiển thị số lượng target/backend và
  hướng dẫn thao tác ngay trên page. AppTest kiểm tra không tự check khi dán/sửa,
  kể cả khi nhập lại ghi chú cũ. 33/33 test Lead Triage và compileall đạt; full
  suite 400 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage chọn email nhận từ recipient precheck Domain Worker
  theo channel của draft, tách nhiều email và lọc địa chỉ bị chặn/sai định dạng;
  fallback email của draft Worker, không lấy contact IP/backend hoặc ghi chú.
  Widget email cập nhật theo địa chỉ mặc định mới. Module version 17.
  32/32 test/AppTest liên quan, compileall và diff check đạt. Full suite
  399 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage khôi phục target Đã gửi từ metadata `sent_log.csv`
  (`send_mode=lead_triage`, `status=sent`, đúng `target_url`) để ẩn và khóa Check
  sau khi mở lại trang. Session vẫn giữ trạng thái nếu SMTP thành công nhưng ghi
  log lỗi. Module version 16; có AppTest phiên mới và log gửi lỗi/kênh khác.
  30/30 test/AppTest tập trung, compileall/diff check đạt; full suite 397 test
  còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage giữ nguyên Browser Evidence formatter chung rồi
  thêm hướng dẫn NCC xác minh nguồn–đích và dấu hiệu giả mạo/luồng đăng nhập
  từ manifest; phần Việt có bước tái hiện và câu hỏi hạ tầng backend. Không suy
  diễn một DOM href thành click hoặc chứng cứ origin. Module version 15.
  30/30 test/AppTest liên quan, compileall và diff check đạt. Full suite còn
  baseline Windows 3 failure + 6 error ngoài thay đổi này; không mở URL thật
  hoặc gửi SMTP trong kiểm thử.

- 2026-10-06 — Lead Triage khóa Check cho target đã gửi: cột ButtonColumn
  không có nút ở dòng Đã gửi, multiselect bỏ target này, callback và batch
  lọc lại để chặn sự kiện cũ. Trạng thái gửi vẫn hiển thị trong bảng.
  28/28 test/AppTest tập trung, compileall và diff check đạt. Full suite
  395 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage thêm cột Trạng thái gửi theo đúng target. SMTP
  thành công hoặc `already_sent` ghi trạng thái trong session và rerun bảng;
  gửi lỗi vẫn Chưa gửi. Giữ trạng thái khi nội dung ghi chú thay đổi trong phiên.
  27/27 test/AppTest tập trung, compileall và diff check đạt. Full suite
  394 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage hoàn thiện bản đối chiếu tiếng Việt cho các biến thể
  report registry Domain Worker: lời chào, lý do liên hệ, yêu cầu xử lý và nhãn
  chứng cứ. Bản tiếng Anh gửi NCC giữ nguyên. Module version 14.
  26/26 test/AppTest liên quan, compileall và diff check đạt; full suite
  393 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage giữ nguyên câu tố phishing từ report Domain Worker
  khi loại tên AU88/AU888, thay tên bằng “our brand” thay vì xóa cả câu.
  Backend bỏ cụm “trong ghi chú”, thêm nhãn IP được cung cấp, DNS mới và RDAP;
  tiếp tục yêu cầu NCC xác minh mối liên hệ. Module version 13.
  24/24 test/AppTest tập trung, compileall và diff check đạt. Full suite
  391 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Lead Triage chỉ mở Gửi report khi chụp DOM nguồn–đích thành
  công, đủ hai ảnh, manifest/hash hợp lệ và đúng URL; kiểm tra lại tại ranh giới
  SMTP và trong delivery lock. Bỏ uploader thủ công ở menu này; ảnh thụ động
  fallback chỉ dùng xem trước, không mở gửi. Module version 12.
  23/23 test/AppTest liên quan, compileall và diff check đạt. Full suite
  390 test còn baseline Windows 3 failure + 6 error ngoài thay đổi này;
  không chụp URL thật hoặc gửi SMTP trong kiểm thử.

- 2026-10-06 — Lead Triage đổi nhãn nhập và nguồn recipient sang “Thông tin
  đầu mối”/“ghi chú đầu mối”; module version 11 nạp lại nhãn helper cũ.
  21/21 test/AppTest liên quan và compileall/diff check đạt; chỉ đổi câu chữ.

- 2026-10-06 — Lead Triage ghim cột Check/Domain bên trái, đặt thứ tự cột
  explicit và reset key bảng để bỏ layout cũ. Batch tối đa sáu target song song;
  backend DNS/RDAP trong cùng ghi chú tái dùng tối đa 300 giây, target check mới.
  Module version 10. Test/AppTest tập trung 21/21 đạt, gồm barrier sáu target
  và cache backend hết hạn. Không gửi SMTP hoặc mở URL thật trong kiểm thử.
  Compileall/diff check đạt; full suite 388 test còn đúng baseline Windows
  3 failure + 6 error ngoài thay đổi này.

- 2026-10-06 — Sửa phạm vi Lead Triage theo cấu trúc ghi chú: domain trước mục
  backend là report target (ví dụ sáu domain), domain/IP sau marker chỉ làm đầu
  mối hạ tầng bổ sung (ví dụ hai domain), không tạo report/gửi riêng. Check
  backend bằng DNS/RDAP một lần, song song với precheck target; draft từng
  target thêm IP ghi chú, DNS mới và org RDAP với nhãn mối liên hệ cần xác minh,
  giữ recipient của report target. Module version 9. Test/AppTest tập trung
  20/20, compileall và diff check đạt; full suite 387 test còn đúng baseline
  Windows 3 failure + 6 error ngoài phạm vi thay đổi. Không gửi email hay mở
  domain thật trong kiểm thử.

- 2026-10-06 — Lead Triage loại dòng nhắc AU88/AU888 khỏi subject/body cũ
  và observation bổ sung trước preview Anh–Việt; giữ URL chứng cứ và snapshot
  gốc, subject fallback theo domain. Có regression test draft cũ + observation.
  Regression test này đạt; compileall và diff check đạt. Full suite tại thời
  điểm kiểm tra: 384 test, 4 failure + 9 error, gồm 1 failure + 3 error ở
  AppTest Lead Triage đang thay đổi và baseline Windows/Provider Replies.

- 2026-10-06 — Xử lý đầu mối thêm nút Check trên từng dòng bảng, giữ các kết quả
  đã check trong cùng ghi chú. Batch check tối đa bốn đầu mối song song và RDAP
  các IP của một đầu mối chạy cùng precheck; report `run_check()` được tạo theo
  yêu cầu ở từng mục sau precheck để giảm thời gian chờ ban đầu. Test/AppTest
  tập trung 16/16 và compileall đạt. Full suite 383 test còn đúng baseline
  Windows: 3 failure + 6 error không thuộc thay đổi này; không gọi URL hoặc
  gửi SMTP thật trong test.

- 2026-10-06 — Làm sạch thư NCC của Xử lý đầu mối: bỏ note provenance subdomain
  và lời giải thích ghi chú nội bộ; backend external chỉ chứa DNS/IP/RDAP khớp.
  Yêu cầu đình chỉ dịch vụ vi phạm, bảo toàn hồ sơ và xác nhận biện pháp xử lý.
  Hai panel dùng cùng chữ ký tổ chức theo tài khoản; logo preview/SMTP dùng helper
  Domain Worker, giữ ảnh DOM + manifest đã duyệt. 14/14 test tập trung,
  compileall và diff check đạt. Full suite 381 test còn đúng baseline Windows
  3 failure + 6 error; không gửi SMTP hay capture URL thật trong kiểm thử.

- 2026-10-06 — Xử lý đầu mối dùng nguyên subject/body report của Domain Worker
  qua `run_check()`; đầu mối IP chưa có hosting draft dùng cùng formatter hosting.
  Snapshot parsed draft theo full URL, chèn backend context và Browser Evidence
  trước chữ ký. Nút DOM gọi đúng `provider_replies.capture_dom_link_evidence()`;
  giữ preview Anh–Việt và SMTP exact kèm attachment/hash. Dùng predicate cloaking
  chung cho precheck/pipeline và log delivery theo report/channel gốc.
  13/13 test tập trung (parity formatter hosting thật, AppTest DOM/SMTP mock),
  compileall và diff check đạt. Full suite 380 test còn đúng baseline Windows
  3 failure + 6 error; không capture URL hay gửi SMTP thật khi kiểm thử.

- 2026-10-06 — Menu Xử lý đầu mối hiển thị draft trong hai khung Anh–Việt có viền,
  đặt cạnh nhau, cùng nút Gửi nổi bật ngay dưới draft của từng domain. Nút này
  chỉ mở sau khi chọn tài khoản SMTP, xác minh
  người nhận và tích xác nhận gắn với nội dung hiện tại. Chỉ body tiếng Anh đã
  preview được gửi; fingerprint + lock và `sent_log.csv` chống gửi trùng, log
  chỉ chứa metadata; session cũng khóa lại nút sau SMTP thành công. Không gửi thư
  thật trong kiểm thử. 13/13 test tập trung (gồm AppTest/SMTP mock), compileall và
  diff check đạt; full suite 380 test còn đúng baseline Windows 3 failure + 6 error.

- 2026-10-06 — Sửa draft Xử lý đầu mối theo quan sát thực tế; dùng nút capture
  Browser Evidence chung helper Domain Worker, có fallback upload 1–3 ảnh.
  Preview ảnh, xác thực manifest/hash/URL và gắn fingerprint của attachment
  vào bản đã duyệt; chỉ gửi tiếng Anh kèm ảnh + manifest khi evidence hợp lệ.
  Case cloaking cần duyệt riêng bị khóa gửi ở menu này. 13/13 test tập trung
  gồm AppTest capture và SMTP mock, compileall/diff check đạt. Full suite chạy
  380 test, còn đúng baseline Windows 3 failure + 6 error.

- 2026-10-06 — Thêm menu Xử lý đầu mối: dán ghi chú domain/IP, mặc định chọn
  nhóm backend, so DNS A với IP được cung cấp, tra RDAP contact cho IP đã nêu,
  dùng chung HTTP và recipient/cloaking precheck của Domain Worker. Kết quả
  chỉ giữ trong phiên UI, không tạo job hoặc gửi report; domain trần được
  check ở trang chủ HTTPS và vẫn cần full URL/evidence trước takedown.
  Bổ sung draft yêu cầu điều tra tiếng Anh theo từng domain từ HTTP/precheck
  và mô tả AU888/backend của sếp có caveat; IP/RDAP của sếp vẫn hiện khi DNS
  timeout nhưng được gắn nhãn chưa xác minh. Có ô observation bổ sung, email
  nhận chỉnh được và tải draft `.txt`; không dùng hosting formatter legacy
  vì nó khẳng định origin/credential theft quá mức chứng cứ hiện có.
  DNS dùng system resolver fallback có timeout khi dnspython timeout.
  Đã đạt 7/7 test menu gồm AppTest hai URL cùng domain, 10/10 test tập trung
  gồm navigation và compileall. Full suite chạy 374 test, còn đúng baseline
  Windows đã biết:
  3 failure + 6 error ở mock macOS, Domain Worker stop và Provider Replies cũ.
  Sửa lỗi `AttributeError: module lead_triage has no attribute
  build_investigation_draft` khi app đang chạy giữ module cũ: page tự reload
  helper theo `MODULE_VERSION` và giữ kết quả phiên hiện tại. AppTest tái hiện
  stale module đạt trong `.venv`; full suite 375 test còn cùng baseline 3
  failure + 6 error, compileall đạt.

- 2026-10-04 — Email SMTP bổ sung chữ ký tổ chức gồm vai trò Brand Protection,
  pháp nhân và mã đăng ký; logo OKWIN chuyển vào `assets/email/` và được nhúng
  inline 64 px bằng CID ngay trước Regards. Mail giữ plain-text fallback, HTML
  alternative và evidence attachment độc lập. Provider Replies preview cùng chữ
  ký và dùng chung MIME helper; PyInstaller bundle `assets/`.
  Đã đạt 11/11 test tập trung, AppTest Provider Replies và compileall. Full suite
  chạy 365 test, còn đúng baseline Windows: 3 failure + 6 error ở mock macOS,
  Domain Worker stop và Provider Replies cũ, không thuộc thay đổi chữ ký.
  Sửa tiếp thứ tự nội dung: `Reported URL`, Browser Evidence và cloaking evidence
  đều dùng helper chung để chèn trước Regards, không còn lọt giữa email liên hệ
  và hai dòng tổ chức trong chữ ký.
  Test thứ tự chữ ký và Browser Evidence đạt; full suite chạy 366 test, vẫn còn
  đúng baseline 3 failure + 6 error không liên quan nêu trên.
  Draft legacy đã chứa Reported URL sai vị trí được chuẩn hóa idempotent: xóa
  đúng marker trùng rồi chèn lại trước chữ ký, không yêu cầu xóa file thủ công.
  Full suite sau migration chạy 367 test, vẫn đúng baseline 3 failure + 6 error.

- 2026-10-02 — Chuẩn hóa 5+5 mẫu webform Registrar/Registry theo góc nhìn người
  đại diện thương hiệu/tổ chức, dùng `We`. Registry khi chưa có delivery Registrar
  chỉ nói tập thể phát hiện URL nghi phishing và submit để review, không còn câu
  meta máy móc phủ định lịch sử report.
  Đã đạt 15/15 test formatter và compileall. Full suite chạy 362 test, còn đúng
  baseline Windows: 3 failure + 6 error ở mock macOS/Domain Worker stop/Provider
  Replies, không thuộc thay đổi nội dung này.

- 2026-10-02 — Webform Registrar/Registry có hai pool riêng, mỗi pool 5 mẫu
  tiếng Anh thận trọng và được chọn ổn định theo domain/ngày. Formatter nhận
  observations đã xác minh theo kiểu opt-in, chỉ ghi số attachment và không lộ
  path; Registry chỉ nhắc lần báo Registrar khi caller xác nhận trạng thái.
  Quick Report giữ nguyên flow, không thêm probe/capture/network step.
  Đã đạt 14/14 test formatter, 17/17 test Quick Report và compileall. Full suite
  chạy 361 test, còn đúng baseline Windows đã biết: 3 failure + 6 error ở mock
  macOS/Domain Worker stop/Provider Replies, không thuộc thay đổi này.

- 2026-09-29 — Bảng registry tĩnh bổ sung `.org` và `.ong` của Public Interest
  Registry (PIR), định tuyến Quick Report tới `https://pir.org/report-abuse/`.
  Luồng chỉ mở form thủ công và cung cấp draft copy-ready, không tự submit.
  Đã đạt 3/3 test tập trung và compileall. Full suite chạy 357 test, còn 3
  failure + 6 error ở baseline Windows/Provider Replies ngoài thay đổi này.

- 2026-09-27 — Provider Replies đồng bộ thêm folder `3 - Cần bằng chứng`
  bên cạnh Inbox, Junk/Spam và `6 - Tự động/Cloudflare`; folder này có thể
  override bằng `imap_evidence_mailbox`, được hiển thị riêng và không gộp vào
  Inbox/Cloudflare.

- 2026-09-27 — Sửa lỗi IMAP `ascii codec can't encode` khi SELECT folder lồng
  `6 - Tự động/Cloudflare`: tên mailbox Unicode được mã hóa Modified UTF-7 ở
  ranh giới lệnh IMAP, còn tên hiển thị/cache vẫn giữ nguyên. Inbox/Junk và
  folder Cloudflare tiếp tục được cô lập lỗi riêng.

- 2026-09-27 — Provider Replies mặc định đọc phản hồi Cloudflare từ folder IMAP
  lồng `6 - Tự động/Cloudflare` theo cấu trúc mailbox thực tế; vẫn hỗ trợ
  override bằng `imap_cloudflare_mailbox` cho từng account. Inbox và Junk/Spam
  tiếp tục được quét độc lập, lỗi folder Cloudflare không làm mất dữ liệu các
  folder còn lại. Đã cập nhật test và tài liệu; không kết nối IMAP thật.

- 2026-09-25 — Cloudflare Form Worker phục hồi membership của job cũ sau
  reload/chuyển trang: snapshot RAM hoặc status thiếu `record_ids` được recover
  theo timestamp + tổng item và checkpoint `job_id` lên ledger. Mọi URL đã vào
  job tiếp tục nằm trong **Tiến trình gửi**; **Bảng loại** chỉ chứa kết quả bị
  loại sau precheck, mặc định ẩn sau toggle để ưu tiên theo dõi job. Có AppTest
  cho worker singleton mới với job lưu trên đĩa và toggle bảng loại.
  Precheck và tiến trình gửi dùng chung một dataframe **Kết quả URL**; không tách
  thêm bảng “Danh sách thực hiện” khi job đã tồn tại.
  Page mở dần theo trạng thái: trước precheck chỉ có input + nút kiểm tra; sau
  precheck mới hiện cấu hình Cookie rồi bảng kết quả bên dưới. Job `COMPLETED`
  hiển thị thông báo hoàn thành với tổng URL thành công/lỗi.
  Page dùng native Streamlit `layout="wide"` giống Domain Worker để tận dụng
  toàn bộ vùng nội dung desktop, không chèn CSS phụ thuộc DOM.
  Atomic replace của ledger/job/input retry có giới hạn khi Windows trả
  `PermissionError` tạm thời; lỗi ghi kéo dài hiển thị trong page thay vì làm
  crash Streamlit. Có test mô phỏng hai sharing violation rồi thành công.
  Bảng **Kết quả URL** cố định thứ tự theo input/precheck trong toàn bộ vòng đời
  job; `current_id` chỉ cập nhật trạng thái/callout, không được dùng để reorder.
  Dashboard batch không dùng `api_token`; `account_id` ưu tiên config và fallback
  từ cookie `curr-account`, được xác thực trước khi tạo job và không persist Cookie.
  Fragment tiến trình tự rerun toàn page khi batch kết thúc để input và action
  được mở khóa ngay, không yêu cầu người vận hành bấm **Dừng**; AppTest page đạt 7/7.
  Parser input nhận dòng có annotation/redirect chain, chỉ trích từng URL thật,
  bỏ nhãn như `(top2)` và mũi tên trước precheck/dedupe; 11/11 test tập trung đạt.
  Đã đạt compileall; full suite 356 test còn đúng baseline
  Windows đã biết gồm 1 failure + 6 error đa nền tảng, không thuộc thay đổi này.

- 2026-09-24 — Cloudflare Worker bổ sung batch theo Cookie phiên Dashboard
  dựa trên đặc tả `cloudflare-abuse-batch-spec (1).md`: nhập nhiều URL, preview
  report đã sinh, giãn cách 0–300 giây và các action gửi/tiếp tục/dừng/retry lỗi.
  Sau phép thử thực tế, adapter chỉ yêu cầu `Cookie`, không yêu cầu `x-atok`, và
  giữ Origin/Referer/x-cross-site-security. Adapter POST đúng host Dashboard và
  nhận response thực tế `result=success` khi có `abuse_rand` không rỗng (vẫn
  tương thích `success=true`); response dedupe hiển thị `msg`, đánh dấu đã gửi
  gần đây và không retry; auth lỗi
  chờ phiên mới, rate limit/challenge tạm dừng, mất response thành `UNKNOWN`
  không tự retry. Page 14 được rút gọn còn Cookie Dashboard, bỏ fallback API và
  extension khỏi UI; input/job status cache theo ngày, quay lại page vẫn theo dõi
  thread đang chạy. UI tách **Danh sách thực hiện**, **Bảng loại** và **Tiến trình
  gửi**; tiến trình chỉ dùng `record_ids` của active job, không suy từ ledger. Lỗi xác định của một
  URL thành `FAILED` rồi tiếp tục; retry chỉ lấy URL lỗi. Nút xóa cache giữ nguyên
  lịch sử `SUBMITTED`/`ALREADY_SUBMITTED`/`UNKNOWN`. Phiên chỉ ở RAM; ledger schema
  v4 và job status sanitize không chứa Cookie. Các bảng chỉ hiển thị scope URL
  trong input hiện tại. Bảng loại chốt sau precheck cho URL đã xử lý từ trước/không
  Cloudflare; record đã vào job giữ nguyên trong tiến trình kể cả terminal và retry
  merge `record_ids` cũ. URL đang gửi được sort lên đầu với icon `▶`. Record cũ ngoài scope không hiện nhưng vẫn chống gửi trùng. Extension
  Quick Report vẫn độc lập.
  Đã đạt 16/16 test tập trung gồm AppTest giữ terminal trong tiến trình và merge
  job khi retry; compileall và diff check đạt. Full suite chạy 347 test, còn đúng baseline Windows đã biết: 1 failure + 6
  error ở mock macOS `PosixPath` và stop process dùng `fcntl`/`SIGKILL`/`killpg`.
  Test chỉ dùng mock, không mở form hay gửi report thật.

- 2026-09-22 — Nâng nội dung webform Registrar/Registry để mô tả rõ suspected
  phishing/brand impersonation, luồng registration/sign-in có thể dẫn tới nội
  dung lừa đảo, nguy cơ cung cấp credential/thông tin cá nhân và hậu quả account
  compromise, fraud hoặc financial loss. Narrative vẫn dùng `appears`/`may`/`if
  confirmed`, không tự khẳng định thu thập OTP/payment; Registrar yêu cầu điều
  tra toàn flow và bảo toàn record, Registry yêu cầu phối hợp sponsoring
  registrar và áp dụng biện pháp tương xứng. Có test giữ full URL, câu rủi ro,
  ngôn ngữ điều kiện và chặn claim/scan không có bằng chứng. Đã đạt 26/26 test
  tập trung gồm AppTest Quick Report. Full suite chạy 331 test, còn 1 failure +
  6 error Windows baseline ở mock macOS `PosixPath` và stop process dùng
  `fcntl`/`SIGKILL`/`killpg`, không thuộc formatter webform. Không gửi report thật.

- 2026-09-21 — Web Form Assistant v2.8.0 bổ sung adapter XYZ.COM LLC v1.0.0
  cho ticket Anti-Abuse `gen.xyz/account/submitticket.php?step=2&deptid=6`.
  Quick Report và bảng Registry của `.xyz` cùng họ TLD XYZ dùng endpoint mới;
  extension điền reporter, loại Phishing, domain và draft nhưng không sửa CSRF,
  upload attachment, xử lý CAPTCHA hoặc submit. Host permission chỉ thêm
  `gen.xyz`; có test contract adapter, allowlist, URL mapping và UI source.
  Đã đạt 36/36 test tập trung, syntax check toàn bộ JavaScript, compileall và
  diff check. Full suite chạy 331 test, còn 1 failure + 6 error Windows baseline
  ở mock macOS `PosixPath` và stop process dùng `fcntl`/`SIGKILL`/`killpg`, không
  thuộc adapter XYZ. Không mở form bằng task thật hoặc submit report.

- 2026-09-21 — Web Form Assistant v2.7.1 sửa adapter Chống Lừa Đảo v1.1.0 theo
  form SolidJS mới của provider: bỏ selector placeholder tiếng Anh đã mất, scope
  email/URL/select/textarea trong form và chọn loại bằng value `2:PHISHING` thay
  vì text `Phishing`. Giữ `fill_only`, không upload/CAPTCHA/submit; kiểm tra DOM
  công khai chỉ đọc, không dùng task token và không gửi form thật. Đã đạt 9/9
  test extension, syntax check toàn bộ JavaScript, compileall và diff check.

- 2026-09-21 — Quick Report hiển thị caption email tố cáo Registrar/Registry
  ngay trên khối cloaking. Lookup nhẹ dùng WHOIS sẵn có, RDAP khi registrar chưa
  có email và bảng registry tĩnh; không chạy precheck Domain Worker, không tìm
  origin hosting, mở website hoặc capture evidence. Recipient privacy/Cloudflare
  không được hiển thị, registrar có web form giữ route form. Bump runtime cache
  để loại result cũ thiếu field; 14/14 test Quick Report và compileall đạt. Full
  suite chạy 327 test còn 7 lỗi đa nền tảng có sẵn ở Chrome/Domain Worker trên
  Windows (`PosixPath`, `fcntl`, `SIGKILL`/`killpg`), không thuộc thay đổi này.

- 2026-09-21 — Phản hồi NCC đồng bộ thêm folder IMAP `2-Cloudflare` bên cạnh
  Inbox và Junk/Spam; lỗi folder phụ được cô lập, thống kê và bảng email hiển
  thị Cloudflare thành nhóm riêng, Seen all tiếp tục thao tác theo mailbox nguồn.
  Page dùng thống kê của chính lượt fetch theo ngày địa phương thay vì mở lượt
  đếm Inbox/Junk riêng. Thay đổi dùng chung cũng đưa folder này vào nguồn phản
  hồi của Thống kê tổng quát. File chính: `provider_replies.py`,
  `pages/9_Provider_Replies.py`, `general_statistics.py`, test và tài liệu; chỉ
  kiểm thử bằng mock/AppTest, không kết nối IMAP hoặc gửi email thật.

- 2026-09-17 — Điều tra retry bằng log thực: job chọn 13 URL nhưng hai lượt đầu
  tiếp tục timeout SMTP, rồi nghỉ batch theo cấu hình. Sửa bảng ưu tiên trạng thái
  đang/chờ retry thay vì che bởi kết quả lỗi cũ; hiển thị scope và tiến độ theo
  lần chạy. UI/worker dùng chung completion predicate, submit lưu retry_targets
  cụ thể, không coi success của pipeline là SMTP success. Thêm test đi qua lõi
  draft/send với SMTP mock cho 12 failed + 1 pending và retry lỗi lần nữa, cùng
  AppTest trạng thái bảng. Đã đạt 66 test Domain Worker, 322/322 full unittest,
  compileall và diff check. Không sửa runtime data hoặc gửi thật.

- 2026-09-17 — Domain Worker sửa retry nhiều lần: kết quả mới thay dòng URL cũ,
  bộ đếm không cộng dồn attempt, và lượt bị ngắt giữa chừng vẫn có thể chạy tiếp.
  Thêm khóa vòng đời process, ghi PID ngay khi launch và xác minh worker nhả khóa
  trước khi báo dừng. UI có Chạy lại sau stopped/failed và Chạy lại precheck khi
  chưa hoàn tất; không tự restart. Kiểm thử mock SMTP/process và AppTest cho
  stop → chạy lại → retry lỗi lặp lại. Người dùng cho phép tiếp tục khi skill
  developing-with-streamlit bị thiếu trong checkout. Đã đạt 320/320 unittest
  (gồm AppTest), compileall và diff check; hai test bridge localhost được chạy
  lại ngoài sandbox sau lỗi quyền bind socket. Không gửi email/report thật.

- 2026-09-15 — Sửa Quick Report hiển thị `DeltaGenerator` và docstring dài
  sau khi mở form Google Safe Browsing/Microsoft SmartScreen: thay bốn
  conditional expression gọi `st.success`/`st.error` bằng `if/else` statement,
  gồm cả hai nhánh Playwright legacy. Không đổi adapter, payload hoặc
  hành vi submit. File chính: `pages/7_Quick_Report.py`, test UI; tài liệu:
  README, CLAUDE và file này. Đã kiểm tra 11/11 test Quick Report,
  AppTest không có exception, compileall và diff check; không đổi nghiệp vụ
  nên không chạy lại full suite.

- 2026-09-13 — Giảm lag Quick Report: dừng timer kết quả sau khi batch hoàn tất,
  tách mỗi domain thành fragment để click form không render lại toàn danh sách.
  AppTest kiểm tra bấm Google/Microsoft lặp lại bằng mock, không mở form thật.

- 2026-09-13 — Sửa Quick Report mất kết quả khi F5: version nằm trong cache
  dùng chung thay vì session, giữ pending futures và khôi phục danh sách sau
  F5/chuyển menu. Xóa cache dùng callback dọn kết quả/cache lọc/ô nhập. Kết quả
  chỉ giữ trong vòng đời server; có AppTest cho session mới, pending và clear.

- 2026-09-13 — Sửa các nút tự điền Quick Report báo không tìm thấy Chrome trên
  macOS: helper mở Chrome bổ sung app hệ thống/người dùng trên macOS và PATH
  trên Linux, giữ tìm kiếm Windows; lỗi khởi chạy được cô lập và thử ứng viên
  tiếp theo. Kiểm thử bằng mock, không mở form hoặc gửi report thật.

- 2026-09-15 — Web Form Assistant v2.7.0 thêm adapter Registry Co phishing
  v1.0.0 cho Quick Report: chỉ match form chính thức
  `registry.co/report-abuse/form`, điền reporter, loại phishing, brand, domain,
  full URL/path và draft registry. Upload evidence, checkbox thiện chí,
  reCAPTCHA và submit vẫn thủ công; host permission chỉ thêm origin
  Registry Co. File chính: adapter JS, manifest, bridge/worker, Quick Report và
  test extension/UI; tài liệu: README, CLAUDE, Technical Guide, file này và
  skill dự án. Đã kiểm tra syntax toàn bộ JavaScript, 28 test tập
  trung, 308/308 full unittest, AppTest Quick Report, compileall, pip check và
  diff check.

- 2026-09-13 — Microsoft SmartScreen adapter v1.0.5/extension v2.6.5 rollback
  toàn bộ tự chọn ngôn ngữ theo yêu cầu: Quick Report không truyền `language`,
  adapter chỉ điền URL và không tìm/mở/thao tác MWF language combo. Trường ngôn
  ngữ giữ nguyên mặc định của Microsoft; popup recheck chỉ xác minh URL.

- 2026-09-13 — Microsoft SmartScreen adapter v1.0.4/extension v2.6.4 sửa trạng
  thái dropdown mở nhưng không commit: option Vietnamese được focus, gắn class
  `active` theo MWF, phát pointer/mouse; nếu chưa đổi `comboLanguage` thì mở lại
  và phát Enter. Chỉ báo `FILLED` sau khi widget cập nhật thật; không submit hay
  thao tác CAPTCHA.

- 2026-09-13 — Microsoft SmartScreen adapter v1.0.3/extension v2.6.3 sửa MWF
  Combo lần hai: component chỉ cập nhật state gửi đi qua `onSelectionChanged`,
  nên adapter phát chuỗi pointer/mouse event thật sự tới nút và option Vietnamese,
  chờ input `comboLanguage` đổi rồi mới báo `FILLED`. Không gán giả input, submit
  hoặc thao tác CAPTCHA.

- 2026-09-13 — Sửa Microsoft SmartScreen adapter v1.0.2/extension v2.6.2:
  form thật dùng MWF custom combo (`comboLanguage`, `LanguageListButton`,
  `comboLanguageList`) chứ không phải select chuẩn. Adapter click option
  **Vietnamese** rồi xác minh input readonly đã đổi giá trị; không còn kiểm tra
  nhầm text của nút khiến badge luôn `!`. Không submit hoặc thao tác CAPTCHA.

- 2026-09-13 — Web Form Assistant v2.6.1 cập nhật Microsoft SmartScreen adapter
  v1.0.1: Quick Report vẫn truyền `language=Vietnamese`; adapter tìm trường theo
  thuộc tính hoặc caption “What is the main language used on the site?”, chọn
  **Vietnamese** và xác minh selected option trước khi báo `FILLED`. Recheck cũng
  chặn trạng thái hoàn tất nếu ngôn ngữ bị mất sau rerender. Không thay đổi
  submit/CAPTCHA.

- 2026-09-13 — Web Form Assistant rollback panel nổi trên trang theo phản hồi
  trải nghiệm; checklist, **Điền lại** và **Kiểm tra lại** chỉ còn trong popup.
  Badge ghim cũng được khôi phục về bộ ký hiệu `…`, `✓`, `C`, `!`, `×` của
  v2.6.0. File chính: extension `content.js`,
  `background.js`, manifest và test; tài liệu: README, CLAUDE, Technical Guide,
  AGENTS và skill dự án. Không thay đổi hành vi submit/CAPTCHA.

- 2026-09-13 — Web Form Assistant v2.6.0 thêm badge trạng thái theo từng tab:
  `…` đang xử lý, `✓` đã điền/submit, `C` chờ CAPTCHA thủ công, `!` cần thao tác
  và `×` khi lỗi. Service worker chỉ dùng state/message đã sanitize, đổi màu và
  tooltip tương ứng, đồng thời xóa badge/status cũ khi tab navigation. Không đọc
  dữ liệu form và không thay đổi hành vi submit/CAPTCHA. File chính:
  `chrome_extension/cloudflare-profile-worker/{manifest.json,background.js}`,
  test extension; tài liệu: `README.md`, `CLAUDE.md`, `03_Technical_Guide.md`,
  file này và skill dự án. Đã kiểm tra syntax toàn bộ JavaScript, 25 test tập
  trung, 304/304 full unittest, compileall, pip check và diff check.

- 2026-09-13 — Web Form Assistant v2.5.0 nâng popup lên EXT-UI 2: checklist chỉ
  hiển thị tên/trạng thái field, CAPTCHA và submit; thêm **Điền lại**, **Kiểm tra
  lại** và **Sao chép chẩn đoán** trên đúng tab. Content script giữ task/adapter
  trong RAM để refill hoặc validate, không truyền giá trị field; mọi diagnostic
  được sanitize và không action nào submit/click CAPTCHA. File chính:
  `chrome_extension/cloudflare-profile-worker/{manifest.json,background.js,content.js,popup.html,popup.css,popup.js}`,
  adapter Cloudflare, test extension; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này và skill dự án. Đã kiểm tra syntax toàn bộ
  JavaScript, 25 test tập trung, 304/304 full unittest, compileall, pip check và
  diff check.

- 2026-09-13 — Web Form Assistant v2.4.0 bổ sung popup mini khi bấm icon
  extension. Popup chỉ đọc và hiển thị version, hostname, adapter/version,
  trạng thái cùng thông báo gần nhất của đúng tab; content script publish
  metadata đã sanitize, service worker giữ fallback theo tab và popup ưu tiên
  hỏi trực tiếp content script. Không hiển thị task token, email, draft, cookie,
  CAPTCHA và không thêm submit. File chính:
  `chrome_extension/cloudflare-profile-worker/{manifest.json,background.js,content.js,popup.html,popup.css,popup.js}`,
  test extension; tài liệu: `README.md`, `CLAUDE.md`, `03_Technical_Guide.md`,
  file này và skill dự án. Đã kiểm tra syntax toàn bộ JavaScript, 25 test tập
  trung, 304/304 full unittest, compileall, pip check và diff check.

- 2026-09-12 — Tăng chiều cao mặc định riêng của bảng **Kết quả domain** lên
  tối thiểu 300 px để dễ theo dõi batch khi mới có ít dòng; các bảng phụ vẫn co
  theo nội dung. File chính: `pages/6_Domain_Worker.py`, test UI; tài liệu:
  `README.md`, `CLAUDE.md` và file này. Đã kiểm tra 8/8 test UI Domain Worker,
  compileall và diff check; không đổi nghiệp vụ worker nên không chạy lại full suite.

- 2026-09-12 — Sắp xếp lại Domain Worker theo thứ tự **tiến độ → cấu hình gửi
  worker → kết quả domain**. Tách bảng và tiến độ thành hai fragment cùng poll
  mỗi 3 giây để bảng vẫn realtime nhưng không chen lên trên form cấu hình. File
  chính: `pages/6_Domain_Worker.py`, test UI; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này và skill dự án. Đã kiểm tra 58 test tập
  trung, 304/304 full unittest, compileall, pip check và diff check.

- 2026-09-12 — Bảng **Kết quả domain** của Domain Worker chuyển vào fragment
  polling nên cập nhật cùng tiến độ mỗi 3 giây. Bảng dùng đúng một cột **Trạng
  thái tài khoản** để liệt kê mọi account trong job với **Đã gửi / Một phần /
  Lỗi / Chưa gửi** và recipient tương ứng; thêm account không làm rộng bảng, và
  đã bỏ bản sao bảng tĩnh từng chỉ đổi sau full refresh. File
  chính: `pages/6_Domain_Worker.py`, test UI; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này và skill dự án. Đã kiểm tra 58 test tập
  trung, 304/304 full unittest, compileall, pip check và diff check.

- 2026-09-12 — Sửa Domain Worker không tự bật real-time ngay sau nút **Khởi
  chạy worker**: cả precheck, launch gửi và retry đều ghi session poll marker,
  rerun page ngay để fragment bật chu kỳ 3 giây; thêm grace period cho khoảng
  trễ process con tạo/cập nhật status và tự dọn marker khi terminal. Không còn
  phải bấm **Làm mới trạng thái** để kích hoạt polling. File chính:
  `pages/6_Domain_Worker.py`, test UI; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này và skill dự án. Đã kiểm tra 58 test tập
  trung, 304/304 full unittest, compileall, pip check và diff check.

- 2026-09-12 — Bảng **Kết quả domain** của Domain Worker bổ sung cột **Đã gửi
  đến**, hợp nhất và loại trùng địa chỉ nhận từ kết quả job cùng sent ledger
  thành công của ngày hiện tại. Chỉ delivery `sent`/`already_sent` thành công
  được hiển thị; recipient gửi lỗi không bị ghi nhận nhầm là đã gửi. File chính:
  `pages/6_Domain_Worker.py`, test UI; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này và skill dự án. Đã kiểm tra 58 test tập
  trung, 304/304 full unittest, compileall, pip check và diff check.

- 2026-09-12 — Domain Worker DW-2 bổ sung theo dõi active job tự động mỗi 3 giây
  bằng fragment độc lập, ghi/hiển thị `current_stage` và full-rerun đúng một lần
  khi job kết thúc để dừng polling. Form gửi có preview route dự kiến theo
  URL/account/recipient/channel và sent ledger, không gọi đây là exact draft;
  delivery result giữ stage/error code cho lỗi draft validation, SMTP auth,
  connection, send hoặc recipient rejected. Worker vẫn tự sinh/validate draft và
  gửi sau xác nhận, không có SMTP trong preview. File chính: `domain_worker.py`,
  `pages/6_Domain_Worker.py`, test core/UI; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này và skill dự án. Đã kiểm tra 58 test tập
  trung, 304/304 full unittest, compileall, pip check và diff check.
  Sau phản hồi UX, fragment trở thành nguồn duy nhất hiển thị trạng thái/tiến độ/
  batch/countdown; đã bỏ khối metric tĩnh trùng lặp và các bảng ready, cloaking,
  delivery route ít dùng. Page chỉ giữ bảng kết quả domain, tổng delivery và các
  expander lỗi/email cần thiết.

- 2026-09-12 — Domain Worker DW-1 tối ưu UI mà không đổi pipeline gửi tự động:
  gộp lọc nội dung thô và danh sách worker thành một ô nhập, parser bỏ qua ghi
  chú/token lỗi nhưng vẫn chạy URL hợp lệ, tự lọc cache gửi/no-email trước khi
  tạo job; gom link ngoại lệ vào khu `Cần bạn xử lý`, thu gọn KPI/bảng theo dõi,
  bỏ CSS `.stale`, đặt xác nhận gửi thật mặc định tắt và chỉ tự phục hồi job của
  ngày địa phương hiện tại. Job cũ vẫn giữ để audit. File chính:
  `pages/6_Domain_Worker.py`, test UI; tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md`, file này và skill dự án. Đã kiểm tra 58 test tập
  trung, 302/302 full unittest, compileall, pip check và diff check.

- 2026-09-12 — Sửa Browser Evidence trên bản Windows chia sẻ bị Playwright báo
  thiếu `chromium-*/chrome-win64/chrome.exe`: runtime frozen không còn để Node
  tự suy ra vị trí từ `PLAYWRIGHT_BROWSERS_PATH=0`, mà trỏ tuyệt đối từ
  `sys._MEIPASS` tới `playwright/driver/package.local-browsers`. `build_app.bat`
  kiểm tra executable Chromium và Headless Shell sau COLLECT và dừng build nếu
  artifact thiếu. File chính: `browser_evidence.py`, `build_app.bat`, test
  packaging; tài liệu: `README.md`, `CLAUDE.md`, `03_Technical_Guide.md`, file
  này và skill dự án. Đã build thật với `--clean`, xác minh đủ hai executable
  trong `dist` và không có browser tree trùng; 31 test tập trung, 300/300 full
  unittest, compileall, pip check và diff check đều đạt.

- 2026-09-12 — Thêm adapter extension **GoDaddy Phishing** v2.3.0 cho Quick
  Report: chỉ hiện action tự điền khi registrar khớp GoDaddy, mở đúng
  `legalportal.godaddy.com/abuse/phishing` trên Chrome profile hiện tại và điền
  email, brand, full URL/path, draft registrar. Adapter không tự tích cam kết
  good-faith, không CAPTCHA/submit; host permission chỉ thêm origin GoDaddy.
  File chính: adapter JS, manifest, `cloudflare_profile_bridge.py`,
  `cloudflare_form_worker.py`, `pages/7_Quick_Report.py` và test; không mở hay
  submit form thật trong test. Đã kiểm tra JSON manifest, syntax toàn bộ
  JavaScript, 24 test tập trung, 300/300 full unittest, compileall, pip check
  và diff check.

- 2026-09-12 — Sửa Cloudflare Abuse Reports API HTTP 405: route submit phishing
  dùng đúng `abuse-reports/abuse_phishing` thay vì nhãn UI `phishing`. Payload
  đổi `host_notification` và `owner_notification` từ `send-anon` sang `send`
  để nhất quán với workflow báo cáo bằng danh tính doanh nghiệp.
  Đã kiểm tra 15 test tập trung, 297/297 full unittest, compileall, pip
  check và diff check; test chỉ mock API, không gửi report thật.

- 2026-09-12 — Nâng Cloudflare Form Worker thành **Cloudflare Worker API-first**:
  menu được bật lại; dùng cùng lõi Quick Report để lọc nameserver Cloudflare,
  preview draft/payload và gửi tuần tự các URL được chọn qua Cloudflare Abuse
  Reports API sau xác nhận. Token/Account ID đọc từ `[cloudflare]` trong
  `config.ini` hoặc environment, không ghi ledger/UI/log; nút kiểm tra chỉ
  verify + GET reports. Ledger schema v2 checkpoint kênh, HTTP status và Report
  ID từng URL, chống gửi trùng trong ngày và khóa hai phiên local; extension
  Chrome còn là fallback. File chính: `cloudflare_abuse_api.py`,
  `cloudflare_form_worker.py`, `pages/14_Cloudflare_Form_Worker.py`,
  `phishing_toolkit.py`, navigation, spec, config mẫu và test; tài liệu:
  `README.md`, `CLAUDE.md`, `03_Technical_Guide.md`, file này và skill;
  đã kiểm tra 297/297 unittest, AppTest, compileall, pip check, diff check và
  build PyInstaller; chỉ mock POST, không phát sinh report thật.

- 2026-09-11 — Thêm bộ icon lá chắn cho Web Form Assistant: manifest extension
  v2.2.2 khai báo icon PNG 16/32/48/128 và icon action; source chất lượng cao
  lưu tại `assets/phishingtool-extension-icon-source.png`, còn bản runtime ở
  `chrome_extension/cloudflare-profile-worker/icons/`. Extension đã nạp cần
  Reload trong trang quản lý extension của Chrome; thêm test bảo đảm manifest
  chỉ tới các file tồn tại. Tài liệu: `README.md`, `CLAUDE.md`,
  `03_Technical_Guide.md` và file này.

- 2026-09-11 — Sửa build Windows bị phình do Playwright Chromium đóng gói
  hai đường dẫn: ngoài filter `collect_data_files`, spec lọc lại
  `Analysis.datas` và `Analysis.binaries` sau khi PyInstaller hook chèn rồi
  phân loại lại package data, chỉ giữ
  `driver/package.local-browsers` mà runtime frozen sử dụng. Bản trùng
  `driver/package/.local-browsers` khoảng 701 MB không còn làm ZIP tăng kích
  thước; Chromium và Headless Shell vẫn được bundle để Browser Evidence chạy
  offline trên máy nhận. File chính: `PhishingTool.spec`, test packaging;
  tài liệu: `README.md`, `CLAUDE.md`, `03_Technical_Guide.md`, file này và
  skill dự án.

- 2026-09-11 — Thêm **Cloudflare Form Worker** độc lập Domain Worker email:
  dùng phép phát hiện Cloudflare và draft chung với Quick Report, preview URL đã
  chọn rồi mở Chrome hiển thị để chỉ điền hoặc submit sau xác nhận. Ledger JSON
  dedupe ngày địa phương + full URL. Form được điền bởi extension MV3 cục bộ trên
  đúng Chrome profile đang mở qua task localhost one-time; không dùng profile ảo,
  remote debugging hoặc đọc cookie. CAPTCHA/form không xác minh được chuyển
  manual/failed, không bypass. File chính: `cloudflare_form_worker.py`,
  `pages/14_Cloudflare_Form_Worker.py`, navigation, spec và test core/UI; tài liệu:
  `README.md`, `03_Technical_Guide.md`, `CLAUDE.md`, file này và skill dự án.
  Extension v1.0.1 xử lý tải trắng bằng một lần reload, điền confirm email và
  company từ brand, đặt draft đúng trường public evidence thay vì Comments;
  auto-submit fail closed nếu CAPTCHA/required field/success chưa xác minh được.
  Extension v1.0.2 chuyển localhost bridge sang MV3 service worker để không bị
  CSP/CORS của trang chặn; record không nhận callback sau 45 giây chuyển `FAILED`
  với hướng dẫn reload extension thay vì treo ở `OPENING_PROFILE`.
  Extension v1.0.3 hiển thị badge chẩn đoán trên form và worker phải chờ callback
  từng URL trước khi mở URL tiếp theo để tránh challenge/trang trắng do mở dồn tab.
  Extension v1.0.4 không giả định caption Cloudflare là thẻ label: chọn field theo
  vùng DOM gần nhất, loại Comments và hiển thị input/textarea count khi chờ.
  Extension v1.0.5 phải điền/kiểm tra lại cả Email và Confirm email sau React
  rerender. Không tích hợp dịch vụ hay logic tự giải/vượt Cloudflare Turnstile;
  chỉ chờ operator hoàn tất rồi mới tiếp tục submit đã được xác nhận.
  Extension v1.0.6 ánh xạ Confirm email từ caption DOM bất kể caption có phải
  `<label>` hay không, fallback theo input kế tiếp và badge phải báo `2/2`.
  Quick Report dùng cùng profile bridge cho nút Cloudflare duy nhất nhưng luôn
  `fill_only`; không giữ song song link mở form cũ và không cho submit tại page này.
  Phase adapter 1 hoàn tất ở extension v2.0.0: `content.js` chỉ điều phối task;
  `adapters/cloudflare.js` sở hữu selector/fill/validate/CAPTCHA/submit/success
  theo contract chung để thêm provider mới mà không sửa logic Cloudflare.
  Phase adapter 2 hoàn tất ở extension v2.1.0: Quick Report dùng cùng profile
  bridge để tự điền Google Safe Browsing (URL, draft, taxonomy) và Microsoft
  SmartScreen (URL); cả hai luôn `fill_only`, không tự giải CAPTCHA/submit.
  Manifest chỉ cấp quyền ba origin form chính thức và localhost; task bridge
  whitelist provider/field, không nhận dữ liệu browser nhạy cảm. Đã kiểm tra
  syntax toàn bộ JavaScript adapter/coordinator, 288/288 unittest, compileall,
  pip check và diff check; không mở form hoặc submit báo cáo thật trong test.
  Phase adapter 2.1 hoàn tất ở extension v2.2.0: Quick Report thay hai link cộng
  đồng bằng nút tự điền Chống Lừa Đảo và Cốc Cốc Safe; truyền full URL, email,
  draft community trung lập và loại phishing tương ứng. Hai adapter luôn
  `fill_only`, không giải reCAPTCHA/submit; Check Domain vẫn giữ link thủ công.
  Đã đối chiếu DOM read-only của hai form chính thức, chạy syntax check toàn bộ
  adapter, 291/291 unittest, Quick Report AppTest, compileall, pip check và diff
  check; không nhập dữ liệu hoặc submit form thật trong kiểm thử.
  Extension v2.2.1 sửa adapter Cốc Cốc: Material UI Select phải mở bằng
  `mousedown`, chọn option phishing `data-value="1"`, rồi xác minh hidden input
  `name="type"` thực sự là `1` mới được báo `FILLED`. Đã kiểm tra 22 test tập
  trung và 292/292 toàn bộ unittest.

- 2026-09-11 — Sửa build Browser Evidence cho bản Windows share: `build_app.bat`
  cài rõ Chromium + Chromium Headless Shell với `PLAYWRIGHT_BROWSERS_PATH=0`
  trước PyInstaller; spec copy chúng vào `package.local-browsers`, kiểm tra đủ
  browser runtime theo layout hiện tại (`chrome-win64/chrome.exe` và
  `chrome-headless-shell-win64/chrome-headless-shell.exe`), còn bản frozen dùng
  browser đã bundle. Kết quả generic `collect_data_files("playwright")` phải lọc
  `.local-browsers` để tránh đóng gói trùng cây browser nguồn khoảng 701 MB.
  PyInstaller phải chạy với `--clean` để không tái sử dụng Analysis/TOC cũ.
  Phải phân phối toàn bộ
  `dist/PhishingTool/`, không chỉ
  `.exe`; máy nhận không cần cài Playwright/Python. File chính:
  `build_app.bat`, `PhishingTool.spec`, `browser_evidence.py`; tài liệu:
  `README.md`, `CLAUDE.md`, `03_Technical_Guide.md`, file này và skill dự án.

- 2026-09-09 — Chặn `abuse@cloudflare.com` ở precheck, draft và ranh giới SMTP:
  địa chỉ này không được Cloudflare theo dõi và IP/ASN Cloudflare chỉ là proxy/CDN,
  không phải origin hosting đã xác minh. Domain Worker nhận diện contact/ASN/tổ
  chức Cloudflare, không tạo hosting draft/email và loại case chỉ có recipient
  này khỏi Worker; cache precheck trong ngày được làm sạch. Guard SMTP cũng chặn
  draft/job legacy. File chính: `phishing_toolkit.py`,
  `domain_worker.py`, `pages/6_Domain_Worker.py`, test worker; tài liệu:
  `README.md`, `03_Technical_Guide.md`, file này.

- 2026-09-09 — Quick Report dùng hai link chính thức **Mở Google Safe
  Browsing** và **Mở Microsoft SmartScreen** trong Browser Blocking để người
  vận hành kiểm tra/bổ sung nội dung và xác nhận trước khi submit; link mang
  query `url` của đúng URL đang báo cáo. Hai action tự mở/tự điền form bằng
  Playwright được giữ lại trong lõi nhưng tạm ẩn qua feature flag UI; không ảnh
  hưởng nút Playwright thụ động dùng riêng để xác minh cloaking. File chính:
  `pages/7_Quick_Report.py`, test Quick Report; tài liệu:
  `README.md`, `03_Technical_Guide.md`, file này.

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

- Kiểm chứng đợt UI ba cột: FE 34/34, typecheck/lint/build; backend 118 đạt,
  1 skip theo môi trường trên PostgreSQL test sạch; compileall đạt. Browser sáng/
  tối và viewport 390px không tràn ngang. CLAUDE.md không đọc/cập nhật theo chỉ dẫn.

- 2026-10-16: Fixed false SMTP/IMAP egress denial on Windows using system DNS with a 3-second deadline; preserved exact endpoint/public IP checks and separated DNS errors. Backend: 100 passed/67 skipped, focused mail/network: 14 passed. No live mail login/send/read.

- 2026-10-18: Fixed stale-session UI after role updates. Query/mutation 401 redirects to login with a Vietnamese explanation; no retry on 401, preserve session on 403. Backend role-change session revocation remains enforced. Added regression coverage.

- 2026-10-18: Role permission edits and assignments now preserve valid sessions by atomically updating their authz version; revoked/expired/stale sessions stay invalid. Backend reads current grants per request; workers retain version invalidation. FE refreshes permissions on save/focus/30s and on 403. Explicit revoke/status changes still end sessions.
- Validation: isolated admin PostgreSQL tests 8/8 passed, frontend suite 131/131 plus new tab-focus regression passed, build/typecheck/lint/compileall/diff check passed. No live mail/provider actions or runtime data changes.
- Full isolated backend: 309 passed / 2 skipped (311 tests); no related failures.

- 2026-10-18: Header now shows Telegram @username beneath display_name and actual assigned role names, including mobile. /me adds roles; role names reflect configured labels and refresh with current-user polling. Focused backend admin 8/8 and frontend session/header 4/4 passed; build/typecheck passed.

- 2026-10-18: Refined header roles into compact rounded badges beside the display-name/Telegram block, with wrapping for multiple roles. UI-only; focused frontend 4/4 and build passed.

- 2026-10-18: Added permission-scoped personal/system Dashboard with 1/7/30-day trends, URL/SMTP/Cloudflare aggregates, current-day queues, worker heartbeat/progress, paginated user summaries and all permitted shortcuts. Added dashboard.view/view_all (54 catalog codes); explicit sync-rbac --grant-dashboard rollout preserves future role choices. Local standard roles seeded; no session revoke. All dashboard reads remain local/readonly and do not grant private cross-owner access. Backend full 314 passed/2 skipped (316 tests), new focused dashboard 6/6; frontend 137/137, build/lint/typecheck and compileall/pip check passed. Visual fixtures 1440/390 no overflow or JS errors; header actions now wrap on mobile. No live mail/provider/probe operations.
- Dashboard final validation: focused FE 8/8 after responsive fixes; read-only runtime aggregate 82ms. Legacy 420 retains baseline 4 failures/7 errors. Report: docs/testing/dashboard-web-2026-10-18.md.

- 2026-10-18: Dashboard status text replaced with localized semantic badges for account/user, worker/heartbeat and recent activity outcomes. Added shared StatusBadge with uppercase normalization and distinct accepted/success semantics. UI-only; no backend/state changes.

- 2026-10-18: Replaced current web native selects with shared Radix AppSelect/AppSelectItem across Dashboard, Audit Log, reviews, Link Status, mail settings, provider replies and report drafts. Consistent popup/checkmark/focus/light-dark styling; preserved empty filters, numeric values and disabled semantics. No backend/dependency/runtime changes. Keyboard/empty/disabled regressions and desktop/mobile light/dark popup QA added.
- Select validation: frontend 140/140, build/typecheck/lint/compileall/diff check passed. Four Playwright fixture configurations (1440px/390px, light/dark) show no JS errors or page overflow; popup screenshots inspected.
