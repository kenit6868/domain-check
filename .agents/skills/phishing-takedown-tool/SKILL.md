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
- Helper SMTP phải tôn trọng transport của từng account: port 465/`ssl=true`
  dùng implicit TLS; port khác mặc định STARTTLS và chỉ dùng SMTP thường khi có
  `starttls=false` rõ ràng. Gửi bằng `EmailMessage`/`send_message`; email evidence
  dùng timeout dài hơn email thường. Chỉ retry một lần với cùng Message-ID cho
  lỗi kết nối tạm thời; không retry lỗi auth, sender hoặc recipient.
- Khi đổi phân loại link, chốt semantics của `LIVE`, `BLOCKED`, `DIE`,
  `GEO-BLOCK`, `TEMP ERROR` và `UNREACHABLE`, rồi đồng bộ code, test và UI.
- Detector cloaking phải thụ động: HTTP dùng session tách biệt theo profile;
  Playwright không click, type hoặc submit. Chỉ chạy Playwright sau lớp HTTP,
  ưu tiên cho `POSSIBLE`/`INCONCLUSIVE`; trong worker cũng chạy cho `LIKELY` để
  chụp lại bằng chứng trước khi duyệt/gửi. Email chỉ đính kèm tối đa hai ảnh tự
  động đại diện cho cặp profile khác biệt mạnh nhất; ảnh quan sát còn lại chỉ lưu
  nội bộ trong evidence.
- Khác biệt giữa URL gốc và một path probe như `/vi-vn/` chỉ là khám phá đường
  dẫn, không được cộng điểm cloaking. Phân loại nội dung nhạy cảm phải tách khỏi
  verdict cloaking.
- Credential proxy/vantage chỉ tồn tại trong cấu hình runtime; không ghi vào UI,
  error, manifest hoặc draft. Nếu server khai báo biến theo quốc gia và thiết bị
  nhưng chưa có vantage ngoài mạng hiện tại, worker phải fail closed sang manual
  review thay vì coi `NO_SIGNAL` là đủ an toàn.
- Bằng chứng ảnh do người vận hành tải lên phải kiểm tra signature, giới hạn số
  lượng/kích thước, chỉ nâng tối đa lên `POSSIBLE` và không tự phê duyệt. Worker
  không tự gửi; Cloaking Review chỉ đính kèm sau bước approve rõ ràng. Cặp ảnh
  thủ công đã xác nhận phải nâng cả `NO_SIGNAL` và `INCONCLUSIVE` lên `POSSIBLE`;
  result legacy đã có đủ operator evidence phải được chuẩn hóa khi đọc. Tại
  Cloaking Review, phải giữ active queue ID qua rerun, hiện thumbnail ngay sau
  khi chọn file và commit evidence cùng thao tác tạo draft; không thêm một nút
  “lưu ảnh” trung gian. Validate toàn bộ batch trước khi ghi artifact.
- Trang terminal do provider/trình duyệt tạo ra (cảnh báo phishing Cloudflare,
  DNS/browser error như “Không thể truy cập trang web này”) không được dùng làm
  chênh lệch cloaking. Khi mọi profile đều terminal, trả
  `BLOCKED_OR_UNAVAILABLE`, bỏ manual review cloaking và cho worker tiếp tục gửi
  draft bình thường; không diễn giải trạng thái này thành bằng chứng chắc chắn
  domain đã bị thu hồi nếu chưa có WHOIS Hold/link status xác nhận.
- Worker không tự gửi bất kỳ case cloaking nào. `LIKELY`, `POSSIBLE`,
  `INCONCLUSIVE` và coverage gap phải được tách khỏi luồng gửi để duyệt; chỉ
  xử lý đúng URL người vận hành đã chọn và xác nhận. Bước precheck của
  Domain Worker phải chạy lookup recipient và detector cloaking đồng thời trên
  từng full URL. Chỉ enqueue case cloaking khi có ít nhất một email nhận hợp lệ;
  case không email phải vào nhóm no-email, không migrate/hiển thị ở Cloaking
  Review. Chỉ đưa case không cloaking có email vào danh sách gửi thường và không
  đợi pipeline gửi mới phân loại. Queue review phải lưu bền vững tách khỏi worker
  job; số đếm/link trên Domain Worker chỉ tính case có email của ngày hiện tại.
- Cloaking Review xử lý từng queue record: tạo preview bằng pipeline dùng chung,
  hiển thị chính xác account/recipient/subject/body, yêu cầu xác nhận đã đọc rồi
  gửi trực tiếp bằng SMTP helper. Không được tạo worker job hoặc launch process;
  body đã preview phải chính là body chuyển vào SMTP. Lock ngắn theo queue ID chỉ
  ngăn hai phiên gửi cùng case, không khóa Domain Worker.
- Result/evidence đã duyệt trong queue là nguồn sự thật khi gửi cloaking; một lần
  tạo draft mới không tái hiện tín hiệu không được làm mất evidence đã approve.
  Xác nhận cloaking phải thêm kết luận tiếng Anh rõ ràng và yêu cầu manifest +
  hai ảnh hợp lệ (mỗi file không rỗng, tối đa 10 MB). Quyết định không cloaking
  phải loại evidence block và attachment trước cả preview. Ghi fingerprint của
  attachment lúc preview và chặn gửi nếu nội dung file thay đổi trước SMTP.
- Vẫn nhận diện `QUEUED_*`, `data/cloaking_send_jobs/` và job review cũ trong
  thư mục worker để migrate/sync delivery ledger, nhưng luồng mới không được tạo
  thêm các artifact job này.
- Queue review phải dedupe theo ngày địa phương + full URL chuẩn hóa, không
  theo worker job ID hoặc tài khoản SMTP. Observation trùng ngày phải giữ
  source-job history, dùng evidence mới nhất và không làm mất terminal state.
  Cloaking Review chỉ render ngày hiện tại; ngày mới cần check lại để tạo case
  mới nhưng lịch sử cũ vẫn được giữ cho audit. Bảng dùng action theo queue ID:
  `SENT` không selectable, `FAILED` retry được và `PARTIAL` tiếp tục được.
- Một queue case phải có delivery ledger tích lũy theo account + recipient +
  draft. Chỉ đặt `SENT` khi mọi account thuộc phạm vi nguồn đã hoàn tất; một phần
  thành công phải là `PARTIAL` và vẫn selectable. Retry phải giữ delivery thành
  công/`already_sent`, chỉ ưu tiên account còn thiếu, checkpoint từng kết quả SMTP
  trước delivery tiếp theo và migration job cũ phải phục hồi được lượt
  `already_sent_today` từ event.
- Quyết định review phải tách ba disposition: xác nhận cloaking (gửi kèm
  evidence), không phải cloaking (gửi report thường, không evidence/attachment
  cloaking) và bỏ qua. Không được suy ra selection từ toàn bộ queue.
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
