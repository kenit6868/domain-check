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

- `abuse@cloudflare.com` là inbox không được Cloudflare theo dõi: không được
  tạo recipient/draft SMTP mới hoặc gửi lại từ job legacy. IP, ASN hay contact
  Cloudflare chỉ xác nhận proxy/CDN, không phải origin hosting đã xác minh.
  Domain Worker phải loại recipient này khỏi job, vẫn giữ các recipient hợp lệ
  khác và không hiển thị form/action thủ công trong workflow Worker.
- `run_check()` là pipeline dùng chung giữa CLI và UI. Không tạo một pipeline
  kiểm tra domain khác trong page Streamlit.
- Bản PyInstaller phải bundle Chromium cùng Playwright, không dựa vào browser
  đã cài trong profile của người nhận. Build Windows đặt
  `PLAYWRIGHT_BROWSERS_PATH=0`, chạy `python -m playwright install chromium
  chromium-headless-shell` trước PyInstaller và runtime frozen giữ cùng biến để Browser Evidence dùng
  `package.local-browsers` đã đóng gói. Phải lọc cây nguồn `.local-browsers`
  khỏi generic Playwright data collection để không bundle browser hai lần, và
  chạy PyInstaller với `--clean` để không tái sử dụng Analysis/TOC cũ.
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
- Browser evidence dùng chung phải phân biệt URL người dùng yêu cầu, landing URL,
  redirect HTTP và destination đọc từ DOM. Capture mặc định thụ động, không click,
  type hay submit và chỉ được gắn nhãn `dom_observed`; không gọi href là redirect
  đã xác minh. Phase 2.1 có `capture_dom_destination_evidence()` opt-in cho report
  thường: đọc anchor HTTP(S) hoặc button `data-href` không submit có nhãn
  Register/Login, chụp trang nguồn rồi mở URL đó trong tab mới cùng context và
  referrer. Manifest `dom_destination_opened` phải ghi `navigation_verified=false`
  và đúng hai ảnh; không click, nhập credential, submit form hoặc tải file. Không
  có URL DOM khác trang nguồn hoặc gặp terminal page thì fail
  closed để caller fallback thụ động/thủ công. Mỗi evidence set gồm PNG + manifest
  có SHA-256, không lưu credential, và không chấp nhận terminal page làm evidence
  nội dung.
- Provider Replies dùng Browser Evidence chung làm lựa chọn ưu tiên và giữ
  evidence set theo mail trong session state. Chỉ attachment PNG + manifest còn
  đúng hash mới được coi là browser evidence hợp lệ; upload thủ công là fallback
  duy nhất khi capture tự động không tạo được artifact. Capture tự động phải ưu
  tiên đúng hai ảnh nguồn/đích
  từ DOM destination rồi mới fallback một ảnh nguồn thụ động; UI hiển thị cả hai
  thumbnail và gửi toàn bộ ảnh + manifest. Narrative phải nói URL đích được mở
  trực tiếp, không tự nhận là đã click control.
- Check Domain chỉ mở gửi email sau khi Browser Evidence có 1–3 PNG/JPEG + đúng
  một manifest
  hợp lệ; cùng artifact đã preview phải đi qua cả gửi đơn và gửi tất cả. Gate
  trước SMTP chặn thiếu Subject/recipient/full Reported URL, placeholder,
  evidence scan legacy và attachment không tồn tại. Phiên bản hiện tại không
  submit, hiển thị hoặc đính kèm dịch vụ scan bên ngoài.
- Check Domain có `Passive DOM` mặc định và `Mở URL từ DOM` opt-in. Chế độ DOM
  chụp trang nguồn, mở URL Register/Login trong tab mới cùng browser context và
  chụp trang đích; preview URL nguồn, DOM href, URL cuối cùng cùng redirect chain.
  Không gọi đây là click hoặc redirect đã xác minh. State và widget phải khóa theo
  full URL qua rerun; quality gate phải chặn manifest của URL/path khác. Capture lỗi
  fallback sang Passive DOM hoặc uploader thủ công 1–3 ảnh, không có bước lưu riêng.
  Danh sách attachment đã preview phải được truyền nguyên vẹn cho cả gửi đơn và
  gửi tất cả; cloaking detector/Domain Worker không dùng chế độ mở DOM này.
- Email Browser Evidence phải trình bày theo hướng abuse report, không gửi raw
  telemetry làm nội dung chính: mô tả control, resolved `href`, bước tái hiện,
  URL đích cuối nếu có, attachment và yêu cầu provider điều tra/xử lý. Đặt khối
  này trước chữ ký và giữ manifest làm attachment kiểm chứng. Capture thụ động
  phải nói rõ chỉ inspect markup; DOM-open nói rõ mở URL trực tiếp, không gọi là
  click. Không tự thêm claim đánh cắp credential/OTP/payment nếu chưa quan sát.
- Narrative Browser Evidence phải phân ba case: control + HTTP(S) destination;
  control không có destination tĩnh; và không có control auth. Case 2 không được
  tạo URL giả, case 3 vẫn report suspected phishing/brand impersonation theo ảnh.
  Chỉ lưu aggregate count, không lưu value của field; chỉ mô tả khả năng thu thập
  password/OTP/payment/identity khi DOM có field hiển thị tương ứng.
- Fallback Browser Evidence thủ công cho report thường validate cả batch trước
  khi ghi, preview ngay và không có nút lưu trung gian. Domain Worker schema v4
  chỉ đưa domain thường vào `ready` khi có evidence; capture lỗi vào
  `evidence_review`, upload/gửi từng full URL và giữ evidence để retry SMTP.
  Nút gửi được phép chạy khi worker còn prechecking/running/waiting vì case đã
  tách khỏi `ready`; claim theo URL và lock preflight phải ngăn gửi trùng/ghi đè.
  Không trộn luồng này với ảnh đối chiếu cloaking 2–4 ảnh.
- Nội dung web form phải giữ full URL/path, không chèn kết quả scan bên thứ ba
  và không khẳng định hành vi thu thập OTP/payment nếu không có bằng chứng quan
  sát tương ứng. Ưu tiên mô tả suspected phishing/impersonation và yêu cầu provider
  điều tra, xác nhận rồi áp dụng chính sách.
  Pool GSB và Cloudflare phải tách riêng theo thẩm quyền xử lý, có ít nhất 5 biến
  thể mỗi nhóm và chọn ổn định theo domain + ngày để rerun không đổi nội dung.
- Domain Worker normal-report capture ưu tiên DOM destination: chụp source rồi mở
  URL HTTP(S) được khai báo trong Register/Login control ở tab mới cùng context/referrer
  để chụp destination. Không có URL tĩnh hoặc mở đích lỗi thì fallback passive source;
  chỉ khi cả hai capture không có artifact hợp lệ mới ghi `evidence_review`. Terminal
  browser/DNS source không phải content evidence và tiếp tục draft thường. Page
  `Domain Evidence Review` đọc mọi preflight v4 trong ngày, dedupe full URL và tách
  uploader khỏi Domain Worker. Sau upload 1–3 ảnh, page phải tạo dry-run preview hiển
  thị đúng body đã personalize theo account/recipient rồi mới cho xác nhận gửi; gửi
  dùng đúng delivery plan đã preview, kiểm tra fingerprint ảnh/draft, ghi rõ trạng thái
  từng delivery và retry chỉ lượt còn thiếu. Sau khi gửi thành công phải đánh dấu cả bản
  ghi trùng ở job khác để không tái xuất hiện; không tạo job review mới.
- Formatter registrar/registry phải dùng dữ kiện quan sát, không đưa VirusTotal
  không có detection ra ngoài và không tự yêu cầu `serverHold`/`clientHold` như
  kết luận mặc định. Registry chỉ được nói đã báo registrar khi có delivery state
  xác nhận; luôn giữ cả registered domain và full Reported URL, không chèn raw
  WHOIS hoặc câu ICANN chung cho mọi ccTLD.
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
  cloaking) và bỏ qua. Với `not_cloaking`, phải tạo Browser Evidence report thường
  riêng: ưu tiên hai ảnh nguồn/đích, fallback một ảnh nguồn hoặc upload 1–3 ảnh;
  chèn narrative vào draft, khóa fingerprint và đính kèm artifact đã preview.
  Không được suy ra selection từ toàn bộ queue.
- Mọi nội dung do tool soạn để gửi nhà cung cấp phải dùng tiếng Anh. Không tái
  sử dụng trực tiếp label/detail tiếng Việt của UI trong draft; dữ liệu quan sát
  nguyên gốc như page title hoặc matched keyword có thể giữ nguyên làm bằng chứng.
- Thống kê hiệu quả report phải có phạm vi một account + khoảng ngày rõ ràng.
  `pages/11_Mail_Statistics.py` chỉ đếm Inbox + Junk/Spam của một ngày và không
  mở Sent; không đặt analytics report ở page này.
  `pages/13_General_Statistics.py` là menu analytics duy nhất: sau một thao tác
  explicit, `general_statistics.py` đọc Inbox/Sent/Junk, Sent attachment metadata
  và phản hồi NCC cùng phạm vi rồi lưu snapshot sanitize. Không có menu Sent Mail
  Evidence riêng. `sent_mail_evidence.py` chỉ lưu header/URL/metadata attachment,
  không lưu body, credential hoặc bytes ảnh; một thư Sent quan sát được chỉ
  enrich report khi khớp mạnh delivery log, không tự tăng report metric.
  `report_statistics.py` vẫn phân tích local và không tự mở IMAP/gửi mail. Page
  Provider Replies giữ riêng workflow lọc/xem/trả lời; mail không liên quan
  không vào analytics. Reply chỉ được nối với report cùng account theo
  Message-ID/ticket hoặc domain + provider/sender, không chỉ domain; record reply
  thiếu account bị loại. Snapshot phải whitelist field hiển thị và redact lỗi.
  Evidence legacy thiếu metadata phải hiển thị `unknown`, không suy đoán có/không
  có ảnh.

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
