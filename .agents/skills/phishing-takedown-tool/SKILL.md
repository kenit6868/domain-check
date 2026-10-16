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

- Canonical compatibility source nằm trong `backend/compat/`; file root chỉ là
  wrapper giữ runtime legacy. Sửa lõi tại backend/compat và kiểm tra cả legacy
  unittest lẫn wheel cô lập ngoài repo; không đưa source root trở lại backend build.
  Logo luôn có default bundled WIN, custom upload thuộc owner; reset quay về default
  chứ không bỏ logo. Exact SMTP preview phải materialize/bind cả logo mặc định thành
  owned inline artifact trước fingerprint; không chỉ khóa HTML CID. DW ready preview
  đã làm việc này mà không sửa UserSettings. Preview normal khóa cả manifest JSON,
  check version/result hash, manifest hash và exact From/plain/HTML; GET phải revalidate.
  Mail connectivity status là read-only, không mở socket; chỉ explicit test mới
  login SMTP/IMAP qua exact endpoint/public DNS policy, không gửi/đọc thư.

- Logo chữ ký web là owned `mail_settings:logo`, tách khỏi evidence/count ảnh.
  Load phải verify hash/size và kind/owner; HTML dùng CID `logo-{artifact_id}`,
  ảnh 64×64 trước Regards như legacy, plain body giữ nguyên. Không truyền local
  path/config.ini vào FE hoặc email, không inject HTML draft chưa escape vào UI.
  Exact preview/send sau này phải bind inline artifact và profile version hiện có.

- Domain Worker web có precheck và normal SMTP batch: worker `domain_worker_precheck`, schema 4,
  owned Check/Job và ReviewCase/CaseSource; không ghi runtime legacy. HTTP cloaking
  và contacts song song, no-email không tạo case; capture thường validated mới ready.
  Playwright cloaking còn pending ở web review, không suy ra approval từ HTTP.
  Luồng UI phải theo legacy: chọn account trước nhập URL/precheck, giữ account_scope
  của job và source queue qua F5; không chọn lại theo từng dòng hoặc biến precheck
  thành wizard draft/preview. Main list chỉ ready có email + evidence; review có page
  riêng, no-email không vào queue. Evidence Review upload hợp lệ resolve mọi source
  check, giữ account của từng job. Artifact root phải độc lập CWD API/worker; không
  bypass hash khi đường dẫn lưu bị cấu hình lệch.
  Advisory email dùng pure formatter cũ (Registrar/Registry/Hosting), không webform;
  không persist body, không coi mẫu này là exact SMTP preview/account approval.
  Gửi batch normal dùng `domain_worker.start` + CSRF + xác nhận; server build/approve
  immutable plan nội bộ, không wizard preview. Worker `domain_worker_send`/`all`,
  mặc định 5 domain/300s, delay next_run_at bền vững, stop hết domain hiện tại.
  Dedupe owner/ngày/domain/account/channel/recipient; no-email/cloaking/thiếu ảnh
  không gửi. Precheck bỏ capture nếu normal đã gửi đủ; DB giữ metadata/ledger, bytes
  lớn ở private artifacts. Sang ngày không dùng batch/ledger cũ cho gửi mới. Intent
  commit trước SMTP, checkpoint từng thư; disconnect sau DATA thành UNKNOWN, không
  retry mù; failed retry giữ SENT. SMTP allowlist/TLS/60s, transient pre-DATA retry một lần.
  Giữ bước Sent của legacy: sau commit SMTP accepted dùng `_imap_save_sent()` lưu
  cùng MIME qua owned allowlisted IMAP, Gmail server-managed. `sent_copy_status`
  riêng ở attempt, lỗi copy không retry SMTP. Helper kiểm APPEND OK, timeout/TLS,
  folder SPECIAL-USE/quoted và tra Message-ID trước APPEND. Phục hồi Sent chỉ dùng
  approved immutable original payload/hash/Message-ID, không render profile mới.
  Email evidence phải dùng shared legacy formatter đầy đủ, không tự viết block
  generic thay thế. Capture projection giữ nhãn/href/control, source/final/navigation/
  redirects và aggregate form signals đã redact; bỏ raw HTML/field values/path.
  Legacy validate file và web validate owned artifact trước khi gọi pure formatter
  chung. DOM thiếu metadata cũ phải capture lại, không đoán URL hoặc nói đã click.
  UI chỉ hiển thị Đã gửi/Hoàn tất gửi; Sent lưu tự động, chỉ cảnh báo lỗi thực tế.
  Luồng exact preview tương thích có `domain_worker.preview`: chỉ ready/current day/item
  succeeded, server resolve recipients và 1–3 ảnh + manifest, account-owned signature,
  revision/expiry 30 phút. Body chỉ persist trong private immutable artifact, không DB/
  audit. Chỉ xác nhận start batch mới phê duyệt gửi thường; create preview riêng
  không phê duyệt gửi. Cloaking approve/send web vẫn chưa triển khai.
  Dừng hợp tác giữ URL đang chạy đến checkpoint; heartbeat stopping không hủy probe,
  pending giữ để resume, lease chết thành unknown. Queue terminal không bị reset.

- Quick Report Netcraft dùng ledger owner/provider/ngày/full URL hash và exact
  immutable preview có revision/expiry/profile version. Commit SENDING intent
  trước HTTP; lease hết hạn thành UNKNOWN, không tự retry. Chỉ FAILED được
  retry explicit. Gửi thật mặc định tắt bằng `DOMAINCHECK_NETCRAFT_SUBMIT_ENABLED`.
  Export nic.top chỉ lấy check thành công của owner, giữ full URL và dùng literal
  XLSX cells. Provider form được routing phía server; HTTPS coordinator luôn
  chạy tám adapter ở chế độ fill-only.
  Trước check/task/preview/submit/export, API phải kiểm tra hồ sơ owner có
  thương hiệu, tên liên hệ và email hợp lệ; readiness không phụ thuộc SMTP/IMAP.
  Giao diện fail closed khi chưa tải được readiness hoặc thiếu cấu hình.
  Quick Report worker là process riêng; startup kiểm tra dependency/legacy import
  trước claim job, giữ heartbeat khi adapter chạy lâu. FE không được mô tả
  pending/queued là đang probe; polling GET không thay thế worker process.
  Check Domain web chỉ nhận một target/full URL mỗi thao tác; job nội bộ có đúng
  một item và DTO trả một check. Quick Report và Domain Worker mới nhận nhiều URL.
  React Check Domain là trang tra cứu và hướng dẫn form báo cáo với nội dung copy;
  không yêu cầu Browser Evidence để xem/sử dụng form, không tự gửi hoặc submit.
  Web check vẫn xác định email registrar khi không tạo draft file; guidance chỉ
  hiện mẫu email khi có recipient hợp lệ và không thuộc registrar chỉ nhận webform;
  loại địa chỉ WHOIS privacy, giữ biến thể email theo domain + UTC day từ legacy.
  Registrar chỉ nhận webform vẫn dùng biến thể webform riêng trong bộ nhớ.
  Registry guidance phải dùng `registry_contact` của kết quả check; webform và
  email dùng formatter riêng, cùng có hai kênh thì hiện hai draft khác nhau.

- Trong web multiuser, mọi target/check/job/case/preview/delivery/evidence/cache
  phải scope bằng `owner_user_id` lấy từ session server-side; `actor_user_id` và
  `sender_account_id` là identity riêng. Không dùng global path/config/cache hoặc
  owner do client gửi. Redis không là nguồn duy nhất của approval, progress hay
  delivery truth. Cross-owner reference phải bị chặn ở repository và database.
  Repository owned phải cố định owner từ session/context khi khởi tạo; method resource
  không nhận owner override. Worker chỉ được lookup toàn cục lúc claim, sau đó dùng
  owner/actor/account scope từ row và lease token + optimistic version.
  Job phải giữ membership/input order bền vững và checkpoint từng item. Stop là
  cooperative, không nhận item mới. Lease chết khi item đang chạy phải phục hồi thành
  `UNKNOWN` kể cả trong stop recovery; không tự đổi thành `FAILED` hay retry. Retry
  chỉ reset đúng failed subset do operator/action hợp lệ chọn.
  Cache key phải có owner/feature/business day/version và hash input nhạy cảm; TTL
  tới local midnight theo timezone operation. Redis loss chỉ là cache miss, còn
  clear chỉ xóa namespace được cấp quyền, không xóa approval/delivery/evidence.
- External action qua API cần permission riêng, xác nhận explicit, durable
  intent/claim và idempotency/recovery. Preview exact gắn revision, account config
  version và attachment hash; SMTP outcome bất định không được retry mù. Secret
  mail/proxy/integration mã hóa có key version, không trả plaintext/mask như value.
  Exact subject/plain/HTML body và delivery plan phải nằm trong private immutable
  artifact; DB/audit/job metadata chỉ giữ hash/reference và metadata allowlist. Mỗi
  edit tạo revision server-side mới. Approve/send phải revalidate latest revision,
  expiry, account/profile/evidence version và artifact bytes; không tin fingerprint
  hoặc nội dung do client tự khai là đã duyệt.
  Quick Report web phải ghi target/check/job trước khi worker gọi `run_cdn_check()`;
  giữ full URL/path/query và input order. Cache hit theo owner/ngày/engine vẫn tạo check
  mới với provenance, không được ghi thành quan sát mạng mới. DTO không chứa raw HTML,
  credential hoặc local evidence path; không thay pipeline nhẹ bằng `run_check()`.
  React Quick Report phải gọi API qua shared Axios boundary, validate DTO và giữ
  operation/query key theo user; component không tự tính verdict/routing/recipient.
  F5 chỉ GET lại operation bền vững, không POST check mới. Khi form-task/submit/export
  chưa có API permission/idempotency tương ứng, phải khóa và mô tả là chưa khả dụng,
  không dùng mock/demo success trong runtime.
  Delivery business key phải do adapter workflow phía server tạo và không dùng preview
  ID/content hash để vượt sent dedupe. Ghi attempt intent + stable Message-ID trước
  SMTP; transaction intent phải commit trước side effect. Chỉ `FAILED` xác định được
  retry explicit. Claim hết hạn hoặc mất checkpoint sau intent phải thành `UNKNOWN`,
  không tự retry hoặc suy ra chưa gửi từ Redis/session.
- Artifact web phải dùng object key do server sinh có owner/operation scope, không
  dùng filename/path client. Validate toàn batch ảnh (decode, MIME/signature, byte,
  dimension, hash) trước commit; file immutable và download phải kiểm lại owner,
  permission feature cùng size/SHA-256. Không phục vụ artifact bằng static route.
  Browser Evidence web capture chạy trong process/job riêng, staging tạm rồi mới ghi
  evidence set owned; terminal/manifest lỗi không tạo evidence. Capture mặc định
  tắt; upload thủ công độc lập. Theo yêu cầu hiện hành, capture web dùng trực tiếp
  `capture_normal_report_evidence()` với HTTP/DNS mặc định, DOM rồi passive fallback;
  không thêm DNS resolver/pool, http_probe hoặc network_guard riêng ở adapter/worker.
  Browser proxy chỉ truyền khi cấu hình explicit; adapter chỉ chuyển ảnh/manifest
  hợp lệ thành owned artifact. Network isolation thuộc deploy worker. Khi sửa
  lỗi capture runtime phải kiểm đúng URL đã báo lỗi nếu đã được operator yêu cầu,
  không chỉ dựa vào smoke fixture để khẳng định luồng thực tế đã hoạt động.
  Mail connectivity test cần flag riêng, exact host:port allowlist và firewall.
- Telegram web login dùng OIDC Authorization Code + PKCE, state/nonce one-time và
  app session opaque HttpOnly. User mới pending; không cấp admin theo username hay
  người login đầu tiên. FE chỉ dùng permission cho UX, backend vẫn enforce action.
  Telegram token exchange dùng HTTP Basic cho Client ID/Secret theo provider contract;
  không gửi secret trong form body. Dev callback phải cùng browser origin/host với FE
  để giữ binding cookie; không trộn `localhost` và `127.0.0.1`. Public auth status chỉ
  trả configured/issuer/redirect URI, không client ID/secret/raw provider error.
- Bootstrap admin chỉ bằng thao tác local explicit theo issuer + OIDC subject ổn
  định. Thay status/role phải revoke session qua `authz_version`, ghi audit allowlist
  và dùng transaction khóa invariant admin hoạt động cuối cùng. Theo yêu cầu hiện hành,
  role `admin` sửa được permission/tên/mô tả qua UI/API như user; seed full chỉ khi tạo
  lần đầu. Role `user` mới seed toàn permission trừ `users.*`/`roles.*`; khóa role giữ
  nguyên và không xóa system role. Sync RBAC giữ quyền/tên/mô tả đã chọn cho cả hai;
  role save tự upsert code đã đăng ký nhưng thiếu trong DB trước khi thay bindings.
  `--reset-user-permissions` thay bằng bộ mặc định và revoke phiên thành viên.
  Actor chỉ có quyền gán role (kể cả `user`) không được cấp permission vượt
  effective permission nếu thiếu quyền quản trị role đầy đủ.

- `abuse@cloudflare.com` là inbox không được Cloudflare theo dõi: không được
  tạo recipient/draft SMTP mới hoặc gửi lại từ job legacy. IP, ASN hay contact
  Cloudflare chỉ xác nhận proxy/CDN, không phải origin hosting đã xác minh.
  Domain Worker phải loại recipient này khỏi job, vẫn giữ các recipient hợp lệ
  khác và không hiển thị form/action thủ công trong workflow Worker.
- Cloudflare web-form automation phải độc lập với Domain Worker email, chỉ nhận
  full URL Cloudflare do operator chọn và luôn preview draft. Submit cần xác nhận
  explicit, không bypass CAPTCHA. Dedupe theo ngày + full URL, checkpoint từng
  dòng để retry/resume. Muốn dùng profile Chrome đang mở phải dùng extension cục
  bộ giới hạn origin + task one-time qua localhost; không dùng remote debugging
  trên default profile, không đọc/sao chép cookie, CAPTCHA hay HTML.
- Cloudflare Abuse Reports API dùng cùng danh sách lọc với Form Worker nhưng là
  kênh riêng, ưu tiên khi `[cloudflare]` có token/account ID hợp lệ. Token không
  được ghi ledger, UI, log, extension hoặc error. Mọi POST phải có preview và xác
  nhận explicit; gửi tuần tự, checkpoint Report ID từng full URL, khóa chống gửi
  đồng thời và không gửi lại URL đã SUBMITTED trong cùng ngày. Kiểm tra kết nối
  chỉ được verify token + GET reports; extension là fallback khi API không sẵn sàng.
  Submit phishing phải dùng `abuse_phishing` cho cả route
  `abuse-reports/{report_param}` và field `act`; workflow công khai danh tính
  doanh nghiệp dùng `send` cho cả host/owner notification.
- Cloudflare Dashboard session là adapter riêng với API chính thức: nhận Cookie
  do operator nhập sau preview, không yêu cầu `x-atok`, POST đúng host
  `dash.cloudflare.com` với Origin/Referer/x-cross-site-security đã xác minh và
  không sao chép browser telemetry khác. Cookie chỉ giữ trong RAM, không được
  đưa vào queue, ledger, snapshot, log hoặc error. Batch gửi tuần tự; auth lỗi
  dừng `WAITING_FOR_SESSION`, rate limit/challenge dừng `PAUSED`, mất response
  thành `UNKNOWN` và không tự retry. Chỉ nhận `result=success` khi có
  `abuse_rand` không rỗng (hoặc response tương thích `success=true`). Resume auth cần phiên mới; pause/stop không
  được ngắt một request đang gửi. `error_code`/`err_code=dedupe` phải giữ `msg`
  đã sanitize, chuyển terminal `ALREADY_SUBMITTED` và không retry. Idempotency
  dùng full URL + report version. Page Cloudflare Worker chỉ dùng adapter Cookie,
  không hiển thị fallback API/extension; cache input và job status theo ngày.
  UI tách bảng thực hiện, bảng loại và bảng tiến trình; tiến trình chỉ lấy đúng
  `record_ids` của active job. Lỗi xác định của từng URL thành `FAILED` rồi
  tiếp tục, và retry chỉ nhận `FAILED`. Xóa cache không được xóa `SUBMITTED`,
  `ALREADY_SUBMITTED` hoặc `UNKNOWN`. Các bảng chỉ render record thuộc full URL
  trong input hiện tại; record đang submit phải được sort lên đầu và có dấu hiệu
  trực quan. URL terminal/không Cloudflare không nằm trong bảng thực hiện.
  Bảng loại chỉ chốt record bị loại ở precheck. Record đã vào job phải ở nguyên
  bảng tiến trình sau terminal; retry merge `record_ids` cũ, không chuyển success
  sang bảng loại hoặc làm biến mất khỏi job. Job legacy thiếu `record_ids` phải
  recover một lần theo timestamp/tổng item rồi lưu `job_id` trên từng record.
  UI phải dùng một bảng kết quả chung từ sau precheck đến hết job, cập nhật state
  tại chỗ; bảng loại mặc định ẩn và chỉ render khi operator bật toggle.
  Trước khi precheck đủ scope chỉ hiện input + action kiểm tra; sau đó mới render
  Cookie config rồi bảng kết quả. State terminal phải báo rõ job đã hoàn thành.
  Checkpoint JSON của Cloudflare Worker phải chịu được Windows sharing violation:
  retry `PermissionError` có giới hạn và vẫn surface lỗi kéo dài lên UI.
  Thứ tự bảng kết quả Cloudflare phải cố định theo input/precheck; không sort
  `current_id` lên đầu. Focus URL đang chạy bằng state/callout tại chỗ.
  Khi batch chuyển từ busy sang terminal, polling fragment phải rerun toàn page
  để mở khóa input/action ngoài fragment; không dùng nút Dừng như thao tác refresh.
  Input Cloudflare có annotation/redirect chain phải tách thành từng URL thật ở
  hàm lõi; không để khoảng trắng, mũi tên hoặc label trở thành một phần URL/path.
  Cookie Dashboard flow không phụ thuộc `api_token`; `account_id` có thể fallback
  từ đúng một value hex 32 ký tự trong cookie `curr-account`, không persist Cookie.
- Web Form Assistant extension phải giữ coordinator không chứa selector provider;
  web multiuser truyền task bằng capability ngắn hạn một lần, bind owner/device/
  provider/target. Chỉ persist hash; raw token không nằm trong provider URL, Redis,
  localStorage, log hoặc popup. Sau exchange, payload/status token chỉ ở
  `chrome.storage.session` của tab; restart yêu cầu task mới. Extension phải so exact
  app origin đã cấu hình và provider origin allowlist trước khi mở tab.
  mỗi adapter riêng tuân theo contract `matches`, `waitUntilReady`, `fill`,
  `validate`, `captchaPending`, `submit`, `detectSuccess`. Thêm provider không
  được làm thay đổi hành vi adapter Cloudflare đã kiểm thử.
  Adapter Google Safe Browsing và Microsoft SmartScreen từ Quick Report luôn
  `fill_only`, không tự giải CAPTCHA/submit; task chỉ được mang các field provider
  đã whitelist và extension chỉ có quyền trên các origin form chính thức. Adapter
  Microsoft chỉ điền URL; không mở hoặc thay đổi MWF language combo, để nguyên
  giá trị mặc định của provider.
  Adapter Chống Lừa Đảo và Cốc Cốc Safe cũng chỉ `fill_only`, dùng nội dung
  community trung lập thay vì draft gọi đích danh Google/Cloudflare, chọn đúng
  loại phishing và không thao tác reCAPTCHA hoặc nút gửi.
  Chống Lừa Đảo dùng form SolidJS: selector phải scope trong form và chọn option
  theo value `2:PHISHING`, không phụ thuộc placeholder/text dịch theo locale.
  Với Cốc Cốc, loại vi phạm là Material UI Select: phải phát `mousedown`, chọn
  option `data-value="1"` và xác minh hidden `input[name="type"]` bằng `1`; chỉ
  tìm thấy text option chưa đủ để báo `FILLED`.
  Popup extension chỉ được hiển thị metadata chẩn đoán đã sanitize của tab hiện
  tại (extension version, hostname, adapter/version, state, message, timestamp).
  Không đưa token, contact, draft, cookie, CAPTCHA hoặc action submit vào popup;
  ưu tiên hỏi content script trực tiếp và chỉ dùng service worker làm fallback.
  Checklist popup chỉ mang tên field + boolean/manual. Action refill được gọi lại
  adapter hiện tại, recheck chỉ wait/validate và copy chỉ xuất diagnostic đã
  sanitize; cả ba không được submit hoặc thao tác CAPTCHA.
  Badge icon phải scope theo tab và chỉ phản ánh state đã sanitize: working,
  filled/submitted, CAPTCHA manual, manual khác hoặc failed. Khi tab navigation
  mới phải xóa badge/status cũ để không gắn kết quả của form trước.
  Adapter GoDaddy phishing chỉ match `legalportal.godaddy.com/abuse/phishing`,
  điền email/brand/full URL/draft registrar và luôn `fill_only`; không tự tích
  checkbox cam kết good-faith hoặc submit thay operator.
  Adapter Registry Co phishing chỉ match `registry.co/report-abuse/form`, điền
  reporter/type phishing/brand/domain/full URL/draft và luôn `fill_only`; không
  chọn evidence, tích good-faith, xử lý reCAPTCHA hoặc submit thay operator.
  Adapter XYZ.COM chỉ match `gen.xyz/account/submitticket.php` với `deptid=6`,
  điền reporter/type Phishing/domain/draft và luôn `fill_only`; không sửa CSRF,
  chọn attachment, xử lý CAPTCHA hoặc submit thay operator.
  `.org` và `.ong` route tới form PIR `https://pir.org/report-abuse/` bằng link
  thủ công + draft registry; không suy diễn thành adapter tự điền hoặc tự submit.
- `run_check()` là pipeline dùng chung giữa CLI và UI. Không tạo một pipeline
  kiểm tra domain khác trong page Streamlit.
- Bản PyInstaller phải bundle Chromium cùng Playwright, không dựa vào browser
  đã cài trong profile của người nhận. Build Windows đặt
  `PLAYWRIGHT_BROWSERS_PATH=0`, chạy `python -m playwright install chromium
  chromium-headless-shell` trước PyInstaller. Runtime frozen phải ghi đè biến
  bằng đường dẫn tuyệt đối từ `sys._MEIPASS` để Browser Evidence dùng đúng
  `package.local-browsers` đã đóng gói, không phụ thuộc working directory hoặc
  môi trường máy nhận. Sau COLLECT, build phải thất bại nếu thiếu executable
  Chromium hoặc Headless Shell. Phải lọc cây nguồn `.local-browsers`
  khỏi generic Playwright data collection và lọc lại cả `Analysis.datas` lẫn
  `Analysis.binaries` để hook PyInstaller không bundle browser hai lần, và
  chạy PyInstaller với `--clean` để không tái sử dụng Analysis/TOC cũ.
- Một lỗi provider bên ngoài, log hoặc sinh draft phải được cô lập để kết quả
  điều tra còn lại vẫn được trả về.
- Worker phải giữ tính resume, chống gửi trùng và trạng thái job có thể đọc lại.
  Mọi thay đổi các phần này cần có test hồi quy.
- Domain Worker UI dùng một ô nhập nhận URL/nội dung thô; token lỗi không được
  chặn URL hợp lệ còn lại. Xác nhận gửi thật luôn mặc định tắt. Page chỉ tự phục
  hồi job ngày địa phương hiện tại, còn job cũ giữ để audit. Link sang Cloaking
  Review và Domain Evidence Review phải được gom tại một khu hành động, không
  lặp cảnh báo ở nhiều vị trí.
- Poll tiến độ Domain Worker phải cô lập trong fragment và tự dừng sau terminal
  state; không rerun form nhập theo chu kỳ. `current_stage` chỉ lưu mã stage,
  không lưu body/credential. Preview trước gửi chỉ được gọi là delivery dự kiến
  nếu mới dựa trên preflight route; draft/body chỉ exact sau pipeline tạo draft.
  Lỗi delivery phải giữ stage/error code để retry phần còn thiếu có thể chẩn đoán.
  Fragment là nguồn duy nhất hiển thị metric/current URL/countdown của active
  job. Domain Worker không lặp bảng ready, cloaking hoặc delivery route; chỉ giữ
  bảng kết quả chính, tổng delivery trước gửi và chi tiết lỗi/email dạng thu gọn.
  Bảng chính phải liệt kê recipient đã gửi thành công/đã gửi trước đó trong ngày;
  không được hiển thị recipient lỗi như đã gửi.
  Bảng chính phải nằm trong fragment polling và chỉ có một cột trạng thái tài
  khoản; mỗi ô liệt kê mọi sender account của job (`sent`/`partial`/`failed`/
  `pending`) cùng recipient. Không tạo cột động theo account và không render
  thêm bản sao tĩnh bên ngoài fragment. Bố cục phải đặt fragment bảng sau form
  cấu hình gửi worker, còn fragment tiến độ ở phía trên.
  Launch precheck/worker/retry phải đặt session poll marker và full-rerun ngay để
  fragment bật chu kỳ theo dõi mà không cần operator bấm refresh; giữ grace
  period ngắn cho race trước khi process con ghi status và xóa marker ở terminal.
- Gửi email, submit report, đọc IMAP và mở URL nghi ngờ đều là hành động ngoài
  hệ thống: chỉ thực hiện khi người dùng cho phép rõ ràng; mặc định là draft/
  preview.
- Connection test của web là action explicit riêng theo owner và permission:
  SMTP chỉ được connect/TLS/login rồi đóng, không gọi `send_message`; IMAP chỉ
  TLS/login/logout, không select/list/search/fetch mailbox. Lỗi và audit chỉ giữ
  mã/transport sanitize, không raw exception hoặc secret. Lưu account không được
  tự kích hoạt connection test.
- Lead Triage giữ bản nháp cục bộ qua chuyển menu/F5 trong cache review riêng,
  chỉ khôi phục dữ liệu đã có, không tự check/capture/gửi khi nạp cache. Nút xóa
  cache chỉ bỏ trạng thái review; sent log và file evidence audit vẫn được giữ.
  Không lưu cấu hình SMTP, credential hoặc secret vào snapshot.
- Lead Triage includes every backend lead explicitly supplied for the report,
  with supplied IPs, current DNS A and RDAP organization for supplied IPs.
  Explicitly label RDAP IPs absent from current DNS A. Label DNS/URL overlap or
  its absence and ask the provider to verify any
  relationship before associating it with the reported case. Never state that
  an unlinked lead serves the target. Preserve evidence URLs when sanitizing
  brand names.
- Lead Triage reports follow concise sections for brand impersonation, observed
  redirect/evidence, related backend findings, and requested action. Preserve
  Domain Worker evidence and only include registrar metadata when supplied.
  Do not allege credential, financial, or OTP theft unless captured evidence
  supports that specific behavior; distinguish observed behavior from concerns.
- Khi report Lead Triage đã có DOM evidence, giữ bước tái hiện/redirect và
  attachment từ formatter chung nhưng không lặp URL, hướng dẫn, yêu cầu điều tra
  hoặc DNS target không liên quan trong addendum. Bản Việt tóm tắt cùng dữ kiện.
- Lead Triage tách kết quả đã cache khỏi nhóm target đang hiển thị: Check dòng
  hiện đúng một target, Check batch hiện nhóm vừa chọn; giữ cache các target
  khác và xóa observation dùng chung khi đổi target đơn.
- Web Form Assistant không chèn panel/overlay vào trang web; checklist, refill và
  recheck chỉ nằm trong popup, không có action submit/CAPTCHA. Badge Chrome dùng
  ký hiệu trạng thái theo tab và phải xóa trạng thái cũ khi navigation.
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
  Formatter Registrar/Registry có thể mô tả luồng nghi vấn registration/sign-in,
  khả năng cung cấp credential/thông tin cá nhân và rủi ro account compromise,
  fraud/financial loss, nhưng phải giữ dạng `suspected`/`may`/`if confirmed`,
  không biến nguy cơ chung thành dữ kiện đã quan sát.
  Pool GSB và Cloudflare phải tách riêng theo thẩm quyền xử lý, có ít nhất 5 biến
  thể mỗi nhóm và chọn ổn định theo domain + ngày để rerun không đổi nội dung.
- Quick Report có thể hiển thị lookup email tố cáo Registrar/Registry bằng
  WHOIS, RDAP fallback và bảng registry tĩnh, nhưng không được gọi precheck
  Domain Worker, khám phá origin hosting hoặc yêu cầu Browser Evidence chỉ để
  hiển thị dòng thông tin này. Phải loại WHOIS privacy và recipient bị chặn.
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
  xác nhận. Pool webform Registrar/Registry phải tách biệt, có ít nhất 5 biến thể
  ổn định theo domain/ngày. `observations` chỉ là input opt-in đã xác minh; không
  suy diễn field thiếu và không đưa đường dẫn evidence cục bộ vào draft. Quick
  Report không được thêm probe/capture chỉ để làm giàu nội dung webform.
  Luôn giữ cả registered domain và full Reported URL, không chèn raw WHOIS hoặc
  câu ICANN chung cho mọi ccTLD.
  Draft do người đại diện thương hiệu/tổ chức submit phải dùng góc nhìn `We`;
  khi chưa có delivery Registrar thì không chèn câu meta phủ định lịch sử, chỉ
  mô tả tự nhiên việc phát hiện URL nghi phishing và gửi để provider review.
- Chữ ký tổ chức phải được personalize theo SMTP account ở lõi dùng chung. Logo
  thương hiệu nếu cấu hình phải nhúng inline bằng CID trong HTML alternative,
  giữ plain-text fallback và không xuất hiện trong danh sách evidence attachment.
  Provider Replies phải preview đủ dòng chữ ký và dùng cùng MIME helper khi gửi.
  Helper thêm Reported URL/evidence phải chèn trước `Regards`/`Kind regards`,
  không append cuối draft làm URL hoặc evidence lọt vào giữa chữ ký tổ chức.
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
  Đồng bộ Provider Replies phải đọc Inbox, Junk/Spam và folder `2-Cloudflare`;
  mỗi mail giữ đúng `source_mailbox`, lỗi folder phụ không được làm mất Inbox và
  UI không được gộp nhầm `2-Cloudflare` vào Thư rác.
  Evidence legacy thiếu metadata phải hiển thị `unknown`, không suy đoán có/không
  có ảnh.

## Tài liệu và kiểm tra

- Khi sửa module legacy được backend đóng gói bằng Hatch force-include, cài lại
  package trong backend venv trước khi kiểm tra API đang chạy; editable install
  vẫn có bản sao site-packages. Reload process sau khi cài, không restart worker
  đang có job. Chạy regression `test_compatibility_packaging` ngoài repo cwd để
  bắt formatter cũ; tải lại guidance từ check đã lưu, không probe lại URL.

- Đọc `AGENTS.md` trước; chỉ mở `CLAUDE.md` khi cần invariant/hành vi cũ,
  `README.md` khi thay đổi cách dùng và playbook khi thay đổi quy trình.
- Không đưa giá trị `config.ini` vào kết quả; chỉ dùng `config.example.ini` để
  xem schema cấu hình.
- Chạy test liên quan và toàn bộ `python -m unittest discover -s tests -v` sau
  thay đổi nghiệp vụ. Với cấu hình/build, chạy thêm `compileall` và `pip check`
  khi phù hợp.
- Tuân theo quy tắc cập nhật tài liệu trong `AGENTS.md`; không lặp lại quy tắc
  chung đó tại đây.

## Quick Report direct actions

Theo yêu cầu chủ dự án, nút Netcraft Gửi API là thao tác explicit tạo exact
server preview rồi submit ngay, không yêu cầu modal/checkbox lần hai. Giữ
permission, configured profile, submit-enabled flag, durable intent/dedupe và
UNKNOWN lock. Không gửi khi render/refresh; không bật server flag chỉ để test.
Form thường mở thẳng URL vì draft đã hiển thị inline; không thêm modal copy.

## Quick Report daily history

Ô nhập luôn hiển thị. Danh sách tích luỹ trong ngày phải đọc DB owned theo
workflow/day và chọn check mới nhất cho mỗi workflow_key, không dùng operation
cuối/sessionStorage làm toàn bộ lịch sử. Cache clear không xoá danh sách này.
Mặc định ẩn cũ theo mỗi lượt, cho phép xem lại; không gắn nhãn Redis cache cho kết quả
chỉ vì nó thuộc lượt trước. Query daily không tạo job/probe/gửi report.

Xoá cache thành công phải dọn operation đang xem/session pointer để thẻ cũ biến mất;
DB history vẫn giữ. Nhập lại URL sau clear tạo check mới và hiển thị lại bình thường.
