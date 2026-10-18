# Cloudflare Worker web — 2026-10-18

## Phạm vi

Menu web dùng Cookie Cloudflare Dashboard: nhập URL → kiểm tra Cloudflare → xem
draft → xác nhận gửi tuần tự. Account ID cấu hình tại Cài đặt mail, thuộc người
dùng, độc lập với tài khoản SMTP. Không dùng extension hay Chrome profile.

API và runner riêng dùng chung PostgreSQL, private artifact root và master key.
Cookie chỉ đi qua request đến runner, giữ trong RAM; không có trong job, snapshot,
ledger, artifact, audit hoặc lỗi trả về. API có thể chạy nhiều process trên cùng
host; tất cả gọi runner tại địa chỉ nội bộ đã cấu hình. Runner có PostgreSQL
singleton lock và claim ID theo batch; port mặc định chỉ bind loopback.

## Hành vi đã kiểm tra

- Chuẩn hóa full URL, tách ghi chú/mũi tên, bỏ trùng, chặn URL local/userinfo/port lạ.
  Parser thuần dùng chung với Cloudflare Worker legacy. Precheck legacy chạy trong
  subprocess giới hạn 180 giây; không truyền Cookie vào subprocess.
- Phân quyền theo owner, CSRF/Origin, hồ sơ tên/email/thương hiệu bắt buộc;
  runner kiểm lại quyền trong DB trước gửi và giữa các URL.
- Account ID gồm 32 ký tự hex; lưu riêng theo user. Ưu tiên cấu hình, fallback
  `curr-account`. Sai hoặc mơ hồ giữa Cookie/cấu hình/batch phải chặn trước request.
- Draft và cấu hình gửi ở immutable artifact. Kiểm owner/kind/size/hash, version
  profile và fingerprint nội dung trước start và ngay trước request.
- Một bảng theo thứ tự input, cập nhật trạng thái tại chỗ; draft có thể mở xem.
  URL bị loại ở precheck ẩn mặc định. Thành công trong batch vẫn giữ nguyên dòng.
- Gửi tuần tự, delay 0–300 giây có timestamp bền vững. Pause/cancel hoàn tất request
  đang chạy rồi mới dừng. Reload phục hồi trạng thái từ DB, không tự start lại.
- Checkpoint intent trước HTTP và kết quả ngay sau từng request; HTTP timeout 30 giây,
  không tự retry và không follow redirect khi mang Cookie.
- Cookie hết hạn chờ Cookie mới; rate limit/challenge tạm dừng; mất response thành
  `UNKNOWN`. Retry chỉ thêm delivery lỗi chắc chắn, giữ accepted/duplicate/unknown.
- Hai tab không tạo hai sender; runner khác không chiếm batch còn heartbeat.
  Restart làm mất Cookie; sau heartbeat stale 90 giây, intent đang gửi trở thành
  `UNKNOWN`, phần chưa gửi chờ Cookie mới. Qua ngày, batch cũ đóng, không khóa lượt mới.
- Xóa cache ẩn workspace ngày hiện tại; giữ Cloudflare records và audit. Lọc terminal
  theo owner/full URL xuyên lịch sử web, không bỏ khóa gửi bằng việc đổi draft.
- API health trả 200; route Cloudflare Worker không đăng nhập trả 401; runner
  không có token nội bộ trả 403.
- Chromium fixture: desktop 1440×1000 và mobile 390×844 cho Worker/Cài đặt mail;
  không lỗi JavaScript, document không tràn ngang. Không dùng tài khoản thật.

## Chạy và cấu hình

```powershell
# Từ root dự án; dùng backend venv và backend/.env hiện có
.\backend\.venv\Scripts\python.exe -m pip install -e ./backend
.\run_cloudflare_worker.bat
```

Hoặc từ backend: `domaincheck-cloudflare-runner` / `python -m domaincheck.cloudflare_runner`.
Runner cần `DOMAINCHECK_SECRET_MASTER_KEY` đã dùng cho API. Token nội bộ được suy
ra bằng HMAC riêng cho runner, không cần nhập secret mới trên giao diện.
`DOMAINCHECK_CLOUDFLARE_RUNNER_URL` mặc định `http://127.0.0.1:8789`.
`DOMAINCHECK_CLOUDFLARE_SUBMIT_ENABLED=false` có thể tắt submit; mặc định cho phép
submit sau permission, Cookie và xác nhận explicit. GET/render không gửi report.

Không expose runner ra Internet. Nếu API tách host, phải thiết kế mạng riêng/TLS và
địa chỉ runner thống nhất; CLI mặc định phục vụ topology API/runner cùng máy.

## Giới hạn và dữ liệu cũ

Không gửi SMTP, mở IMAP, probe domain nghi vấn hoặc submit Cloudflare thật trong
kiểm thử. Khả năng Cookie thật được Cloudflare chấp nhận từ IP server chưa được xác
nhận; lỗi auth/challenge sẽ đi qua các trạng thái chờ tương ứng.

Không đọc `config.ini`, không sửa dữ liệu runtime legacy. Web giữ ledger riêng theo
owner, không tự gán lịch sử JSON legacy cho người dùng web. Cần đối chiếu lịch sử cũ
trước khi đưa lại danh sách đã submit ở Streamlit; Cloudflare báo dedupe sẽ được giữ
terminal. Chưa có màn hình tự giải quyết `UNKNOWN`; các URL này tiếp tục bị khóa.

Không migration DB hoặc dependency mới. Hai adapter Dashboard/API có canonical source
trong `backend/compat`; root giữ wrapper. Wheel và PyInstaller spec đã bổ sung module.

## Kết quả kiểm tra cuối

| Kiểm tra | Kết quả |
|---|---|
| Toàn bộ backend, PostgreSQL mới + wheel mới | 272 đạt / 1 skip (273 test), 142 giây |
| Riêng Cloudflare backend | 16 test đạt trong bộ full |
| Wheel cô lập ngoài repo | 3/3 đạt trong bộ full |
| Toàn bộ frontend | 106/106 đạt |
| Riêng giao diện Cloudflare/Account ID | 6/6 đạt |
| ESLint, TypeScript/Vite build | Đạt |
| `compileall -q .`, `pip check`, `git diff --check` | Đạt |
| Legacy với Python hệ thống | 420 test, giữ 4 failure / 7 error baseline |

Test Redis tích hợp bỏ qua vì không cấp Redis test cô lập. Các lỗi legacy vẫn ở
Chrome/stop-process, lead triage, provider folders và UI legacy đã ghi nhận trước;
không có lỗi mới trong adapter Dashboard, API Cloudflare, parser hoặc bộ web.
Lần full đầu có fixture report dưới giới hạn 20 ký tự; đã sửa fixture, chạy lại
toàn bộ bằng DB mới và wheel mới cho kết quả đạt bên trên.

Runner cục bộ đã được khởi động bằng backend venv; API autoreload có route mới.
Chỉ kiểm tra health/route protected và fixture browser, không cấp Cookie thật.

## Follow-up: measured progress and Account ID clarification

Precheck UI adds indeterminate preparation, persisted processed/total progress,
current URL and completion, with a cyan/blue progress track and accessible native
progress semantics. Batch Account ID is displayed; manual configuration remains
optional because Cookie curr-account can supply it. New regression verifies missing
both sources makes no submission request. Focused backend 17/17, UI 9/9 passed.
Desktop 1440px/mobile 390px fixture checks have no page overflow or JS errors.
This follow-up changes UI and tests only; backend fallback policy is unchanged.

## Follow-up: misleading Cookie/account mismatch error

Read-only live metadata showed two new CHECKED batches had no Account ID binding
and the owner had no configured Account ID. The old private runner classified any
exception mentioning account as account_mismatch, including missing/unreadable
curr-account. Explicit typed reason codes now distinguish these cases without
returning raw exceptions or Cookie. No identity validation rules were relaxed.
Regression verifies a fresh precheck can use a different valid Cookie account,
while retry of an already bound batch cannot. Runner was reloaded only after
confirming there were no active batches. No real provider request was performed.

Validation after diagnostic fix: clean PostgreSQL backend 274 passed/2 skipped
(276 tests, 136 seconds), including 19 Cloudflare tests. Skips: Redis integration
and wheel test without a new wheel flag. compileall/diff check passed. Frontend
logic unchanged. Runtime Cookie/ledger/config values were neither modified nor
printed during investigation.

## Follow-up: configured Account ID priority

Fixed an overly strict runner guard: a valid saved Account ID is now used when
curr-account is absent, malformed or ambiguous. An unambiguous differing account
still blocks, as does changing the identity of a previously bound batch. Cookie-only
fallback now understands bounded URL/JSON wrappers, scalar IDs, explicit account
fields and account.id; unrelated user IDs do not make an explicit account ambiguous.
Conflicting duplicate cookies fail closed. Configured IDs require exactly 32 hex.
Regression covers configured IDs with all three unreadable/missing Cookie forms,
36 wrapper/field samples plus ambiguous/duplicate cases, and preserved mismatch
guards. A read-only live check confirmed the saved ID was valid; no Cookie/raw ID
was printed, no runtime record was edited and no Cloudflare request was made.

Final priority-fix validation: PostgreSQL clean + fresh wheel 277 passed/1 Redis
skip (278 tests, 143 seconds), including 21 Cloudflare tests and wheel smoke 3/3.
Legacy 420 keeps the known 4 failure/7 error baseline. compileall/pip check/diff
check passed. No frontend behavior or dependency changed. Runner reloaded while
idle, preserving all runtime records and configured values.

2026-10-18 - Identity investigation: read-only batch metadata shows recent sending
batches bound to the currently configured Account ID, different from older batches.
Report email/email2 come from the immutable owner contact_email profile, independent
of Dashboard Cookie/account identity. Initial duplicate history may be excluded
without a new POST. Added a transport regression proving new account path + exact
new Cookie header while reporter email remains profile-based. No production logic,
runtime records or credentials changed; core regressions 5/5 passed.
