# Review giao diện và UX frontend — 2026-10-21

## Kết luận

Giao diện hiện có nền tảng ổn: sidebar, sáng/tối, component biểu mẫu và phân cấp
nội dung tương đối thống nhất. Không cần thiết kế lại toàn bộ. Nên ưu tiên sửa
trạng thái job, giữ nội dung đang soạn và các thao tác bị che/khuất, rồi mới tối ưu
mật độ thông tin trên mobile.

Báo cáo ghi nhận 10 vấn đề cần xử lý và 4 đề xuất cải thiện. Mức ưu tiên thể hiện
ảnh hưởng đến thao tác người dùng; không phải kết luận về lỗi bảo mật backend.
Lượt này không sửa source ứng dụng, không deploy hoặc restart dịch vụ.

## Phạm vi và cách kiểm tra

- Đọc router, shell, CSS, component dùng chung, source các trang và test liên quan.
- Chromium headless với API fixture chặn toàn bộ `/api/*`; chặn truy cập ra host
  bên ngoài. Không dùng phiên đăng nhập thật, tạo job thật, gửi report/email, đọc
  mailbox hay probe URL nghi vấn.
- 14 route ở 1440px sáng, 768px sáng, 390px sáng/tối: 56 lượt xem cơ bản.
- Bổ sung Check Domain có kết quả; Domain Worker có domain đủ evidence và cấu hình
  batch; Phản hồi NCC có thư và editor; bốn trang tại 320px; lỗi lưu role, trạng
  thái UNKNOWN, tài khoản pending, filter empty và điều hướng khi chưa lưu.
- Không có lỗi JavaScript ở 56 lượt xem cơ bản. Không tràn ngang toàn trang tại
  390/768/1440px trong fixture; phát hiện tràn ngang Cấu hình mail tại 320px.
- Không coi ảnh logo bị thiếu trong fixture là lỗi sản phẩm: endpoint ảnh logo
  không được dựng trong harness. Không đánh giá Safari/iOS hoặc thiết bị thật.
- Các trạng thái streaming, gửi SMTP/provider, mất mạng trong lúc gửi được đọc
  source/test; không chạy end-to-end với dịch vụ thật.

| Trang | Nội dung đã rà soát |
|---|---|
| Dashboard | Thống kê, chart, bảng người dùng, quick links, responsive |
| Quick Report | Nhập/lọc URL, daily results, cache, extension, UNKNOWN |
| Check Domain | Input, kết quả, nhóm thông tin và mẫu báo cáo |
| Domain Worker | Tài khoản, precheck, metrics, domain ready, cấu hình batch, lịch sử |
| Cloaking Review | Queue, chọn case, kết luận, ảnh/draft và điều kiện gửi |
| Domain Evidence Review | Queue, mở case, upload/draft và điều kiện gửi |
| Cloudflare Worker | CHECKED/READY, progress, Cookie, nút gửi/điều khiển, bảng report |
| Check Link Status | Input/filter/table, code đọc stream và xử lý gián đoạn |
| Phản hồi NCC | Filter, danh sách, editor, dữ liệu chưa lưu, trạng thái rỗng |
| Cấu hình mail | Profile, Cloudflare, mail account và modal thêm account |
| Người dùng | Danh sách, tìm kiếm, role, trạng thái và phân trang |
| Role & quyền | Card, editor, danh mục quyền và lỗi lưu |
| Audit Log | Filter, ngày/múi giờ, tabs, bảng, chi tiết/phân trang |
| Login / trạng thái phiên | Đăng nhập, pending, hồi phục lỗi và điều hướng |

## Các vấn đề cần sửa

### R01 — Ưu tiên cao: Quick Report hiển thị sai trạng thái UNKNOWN

Khi API trả `check.status = unknown`, card hiển thị **“Đang chờ”** và **“URL đang
chờ worker nhận xử lý.”** Đây không phải mục đang đợi worker. Phần đếm hoàn tất
cũng chỉ tính succeeded/failed; thông báo kết thúc chỉ kiểm tra failed nên có thể
báo thành công khi job partial chứa UNKNOWN mà không có failed.

Đã tái hiện card bằng fixture. Khi chỉ tải daily history, mặc định ẩn kết quả cũ
còn có thể che cả mục chưa rõ kết quả, vì mọi bản ghi ngoài operation hiện tại
được tính là cũ, không xét trạng thái terminal hay chưa.

Nên có nhãn UNKNOWN riêng, giải thích worker bị gián đoạn và không tự chạy lại;
đưa UNKNOWN vào tổng kết cảnh báo và không mặc định ẩn mục cần chú ý. Phân biệt
“đang tạo lượt” với “worker đang kiểm tra”: hiện nút nhập vẫn dùng “Đang tạo
operation…” suốt thời gian operation active.

Nguồn: [QuickReportResultCard.tsx:15](../../frontend/src/features/quick-report/components/QuickReportResultCard.tsx),
[QuickReportOperationView.tsx:34](../../frontend/src/features/quick-report/components/QuickReportOperationView.tsx),
[useQuickReportOperation.ts:43](../../frontend/src/features/quick-report/hooks/useQuickReportOperation.ts),
[QuickReportInput.tsx:47](../../frontend/src/features/quick-report/components/QuickReportInput.tsx).

### R02 — Ưu tiên cao: mất nội dung phản hồi đang soạn khi chuyển trang

Tái hiện: nhập thông tin bổ sung trong Phản hồi NCC → sang Dashboard bằng menu →
quay lại mở thư. Nội dung vừa nhập đã mất, không có thông báo trước khi rời trang.
Đây là nội dung local chưa tạo preview; không phải draft đã lưu ở backend.

Editor case và profile cũng dùng state local, nên có cùng nguy cơ khi unmount.
Nên cảnh báo khi rời editor đang có thay đổi hoặc giữ bản nháp phù hợp theo user,
account và case. Không lưu Cookie/password/ảnh vào localStorage để giải quyết.

Nguồn: [ProviderRepliesPage.tsx:120](../../frontend/src/features/provider-replies/ProviderRepliesPage.tsx),
[EvidenceReviewPage.tsx:44](../../frontend/src/features/domain-worker/EvidenceReviewPage.tsx),
[MailSettingsPage.tsx:70](../../frontend/src/features/mail-settings/pages/MailSettingsPage.tsx).

### R03 — Ưu tiên vừa: bấm Mở case nhưng không thấy phần xử lý

Ở Evidence/Cloaking Review, phần editor đặt sau toàn bộ bảng và không có scroll
hoặc chuyển focus khi chọn case. Fixture 12 dòng ở 390×844: sau khi bấm nút đầu
tiên, editor bắt đầu tại y≈937px, scroll vẫn 0, focus vẫn ở “Mở case”. Người dùng
có thể tưởng thao tác không có phản hồi. Queue dài hơn làm vấn đề nặng hơn.

Nên đưa editor vào vùng nhìn thấy, chuyển focus có nhãn, thêm lối về danh sách và
giới hạn chiều cao/phân trang queue. Phản hồi NCC đã có cơ chế scroll/focus tương
tự, có thể dùng cùng cách tương tác.

Nguồn: [EvidenceReviewPage.tsx:39](../../frontend/src/features/domain-worker/EvidenceReviewPage.tsx),
đối chiếu [ProviderRepliesPage.tsx:46](../../frontend/src/features/provider-replies/ProviderRepliesPage.tsx).

### R04 — Ưu tiên vừa: lỗi lưu role nằm ngoài hộp thoại đang mở

Tái hiện API trả 422 khi tạo role: modal vẫn mở nhưng không có lỗi bên trong.
Thông báo cụ thể nằm ở trang phía sau overlay; toast chỉ báo chung “Thao tác chưa
hoàn tất…”. Người dùng không biết trường nào cần sửa. Gán role cho người dùng có
cùng cấu trúc lỗi ở component cha.

Nên truyền lỗi vào editor, đặt lỗi gần trường hoặc footer, giữ nguyên dữ liệu
đã nhập và reset lỗi cũ khi mở editor khác. Mail account editor đã nhận prop lỗi.

Nguồn: [RolesPage.tsx:27](../../frontend/src/features/admin/pages/RolesPage.tsx),
[UsersPage.tsx:41](../../frontend/src/features/admin/pages/UsersPage.tsx).

### R05 — Ưu tiên vừa: Cấu hình mail tràn ngang ở màn hình 320px

Fixture với nhãn “Mail báo cáo” và email `operator@example.com`: viewport 320px
nhưng document rộng 355px, card account rộng khoảng 344px. Hàng tên account và
nút Sửa/Tắt không co hoặc xuống hàng đủ; grid nhận chiều rộng tối thiểu đó.

Nên cho khối tên/email co bằng min-width:0, ngắt email phù hợp, đưa nhóm nút xuống
hàng ở màn nhỏ. Kiểm tra lại nhãn dài và nhiều account, không chỉ viewport 390px.

Nguồn: [MailSettingsPage.tsx:54](../../frontend/src/features/mail-settings/pages/MailSettingsPage.tsx).

### R06 — Ưu tiên vừa: danh sách người dùng bị giới hạn 100 nhưng không có phân trang

FE luôn gửi `limit=100`, không gửi offset và không dùng total để hiển thị tổng
hoặc điều hướng. Khi hệ thống có hơn 100 tài khoản, không thể duyệt toàn bộ danh
sách; chỉ còn cách tìm tên đã biết. Backend đã có offset/limit/total.

Nên bổ sung phân trang và “đang xem X–Y / tổng Z”, đồng thời giữ bộ lọc khi chuyển
trang. Không cần thay đổi API phân trang hiện có.

Nguồn: [adminApi.ts:5](../../frontend/src/features/admin/api/adminApi.ts),
[UsersPage.tsx:42](../../frontend/src/features/admin/pages/UsersPage.tsx),
[admin.py:89](../../backend/src/domaincheck/api/admin.py).

### R07 — Ưu tiên vừa: tài khoản chờ duyệt thiếu Đăng xuất/đổi tài khoản

Trang pending dùng màn hình trạng thái chung. Nút “Về Dashboard” lại trở về chính
màn hình chờ duyệt, “Quay lại” phụ thuộc lịch sử trình duyệt; không có Đăng xuất.
Đã xác nhận bằng fixture pending. Hook vẫn có polling cập nhật /me, nên đây là
thiếu lối thoát/đổi tài khoản, không phải không nhận được kích hoạt từ admin.

Nên có Đăng xuất và Kiểm tra lại trạng thái; thay lối Dashboard không hữu ích.

Nguồn: [AppShell.tsx:31](../../frontend/src/app/layouts/AppShell.tsx),
[StatusPage.tsx:15](../../frontend/src/components/shared/StatusPage.tsx).

### R08 — Ưu tiên vừa: trạng thái rỗng của Phản hồi NCC hướng dẫn sai

Fixture có 1 thư đã lưu nhưng bị checkbox “Chỉ cần phản hồi” loại khỏi danh sách:
UI vừa báo “Đang hiện 0/1 thư theo bộ lọc”, vừa báo “Không có phản hồi… Bấm Đồng
bộ phản hồi…”. Dữ liệu đã có; vấn đề là bộ lọc, không phải chưa đồng bộ.

Nên phân biệt chưa có account, chưa có dữ liệu, không khớp filter và lỗi tải.
Trường hợp không khớp filter nên cho bỏ bộ lọc hoặc xem tất cả thư.

Nguồn: [ProviderRepliesPage.tsx:67](../../frontend/src/features/provider-replies/ProviderRepliesPage.tsx),
[ProviderRepliesPage.tsx:97](../../frontend/src/features/provider-replies/ProviderRepliesPage.tsx).

### R09 — Ưu tiên vừa: lỗi đầu vào Domain Worker không chỉ rõ dòng cần sửa

FE gửi danh sách sau khi trim; backend validate cả danh sách trước khi tạo job.
Một URL không hợp lệ có thể chặn cả lượt nhưng thông báo không có số dòng. Bản
sửa fragment trước đó đã giải quyết `#/register`, còn khả năng lỗi nhập khác vẫn
giữ trải nghiệm này. Mục này được xác định từ source, không tạo precheck thật.

Nên trả lỗi kèm vị trí dòng và lý do, đánh dấu đầu vào lỗi; nếu có chức năng loại
mục lỗi thì phải cho người dùng thấy rõ danh sách sẽ chạy. Server vẫn là nơi
quyết định URL hợp lệ. Không đưa userinfo hoặc URL chứa secret vào toast/log.

Nguồn: [DomainWorkerPage.tsx:62](../../frontend/src/features/domain-worker/DomainWorkerPage.tsx),
[domain_worker.py:47](../../backend/src/domaincheck/application/domain_worker.py).

### R10 — Ưu tiên thấp: thời gian hiển thị chưa thống nhất theo múi giờ tài khoản

Dashboard/Audit Log chỉ rõ timeZone tài khoản; lần đăng nhập cuối, thời điểm đồng
bộ phản hồi và giờ dự kiến batch lại dùng múi giờ trình duyệt. Nếu máy vận hành
có timezone khác tài khoản, cùng một timestamp có thể hiện khác giờ giữa các
trang dù header luôn ghi timezone tài khoản.

Nên dùng formatter chung theo me.timezone, ghi nhãn timezone khi cần. Đây là vấn
đề trình bày; không kết luận backend tính sai business day hoặc countdown.

Nguồn: [UsersPage.tsx:44](../../frontend/src/features/admin/pages/UsersPage.tsx),
[ProviderRepliesPage.tsx:92](../../frontend/src/features/provider-replies/ProviderRepliesPage.tsx),
[WorkerProgress.tsx:116](../../frontend/src/features/domain-worker/WorkerProgress.tsx).

## Bốn cải thiện UI nên làm sau các lỗi trên

1. **Bảng trên mobile:** `.data-table` tối thiểu 900px; ở Domain Worker 390px,
   vùng bảng chỉ rộng khoảng 333px. Nút Mở case/Mở thư và trạng thái bên phải cần
   cuộn ngang mới thấy. Nên có card view mobile hoặc cột hành động cố định; giữ
   bảng desktop. Đây là giới hạn trải nghiệm, khác lỗi tràn toàn trang R05.
2. **Modal thêm mail quá dài:** ở viewport 390×844, modal cao khoảng 758px nhưng
   nội dung cuộn gần 1987px. SMTP/IMAP/proxy/chữ ký đều mở, footer không cố định.
   Nên tách nhóm rõ hơn, thu gọn phần tùy chọn và giữ thao tác Lưu/Hủy dễ tiếp cận.
3. **Mật độ mobile:** header xếp hai hàng ở 390px, ba hàng ở 320px khi có hai role.
   Domain Worker xếp ba metric lớn thành ba khối trước nhóm metric và bảng kết
   quả. Nên thu gọn header/role và số liệu phụ; ưu tiên URL đang xử lý và nút thao
   tác. Có thể giữ phong cách hiện tại mà giảm đáng kể chiều dài trang.
4. **Nhãn và lý do nút bị khóa:** chuẩn hóa các từ hiển thị cho trạng thái job,
   chuyển ID/stage/engine/origin sang phần chẩn đoán khi phù hợp. Cloudflare hiện
   đặt nhiều nút gửi/pause/resume/retry/ngừng cùng lúc dù đa số bị khóa; nên làm
   rõ hành động chính theo phase và giải thích điều kiện còn thiếu. Giữ nguyên
   tên tính năng quen thuộc, nội dung draft tiếng Anh và toàn bộ guard backend.

## Kiểm thử và bằng chứng

- `npm test -- --maxWorkers=2`: 161 test, **160 đạt / 1 fail**. Fail ở test Quick
  Report `displays current input order and numbering even when daily history
  arrives in a different order`, chờ dòng tiêu đề daily results tại dòng 68.
- Chạy lại riêng `tests/quick-report.test.tsx`: **21/21 đạt**. Có dấu hiệu timeout
  nhạy tải; chưa đủ bằng chứng để khẳng định nguyên nhân hoặc coi lượt tổng đạt.
- `npm run lint`: đạt. `npm run typecheck`: đạt.
- Không chạy build để ghi đè dist đang có; không cần backend unittest cho báo
  cáo chỉ đọc này. Không sửa test để che lỗi baseline.
- Browser fixture chính và bổ sung đều hoàn tất sau khi sửa selector trong
  harness để thao tác qua menu mobile. Không sửa code ứng dụng để chạy harness.
- Bằng chứng tạm ở
  `C:/Users/Admin/AppData/Local/Temp/domaincheck-fe-review-20261021/`:
  `results.json`, `extra-results.json`, `browser.log`, `extra.log`, ảnh từng trang.
  Tất cả dữ liệu là fixture; ảnh logo thiếu do endpoint fixture như ghi ở phạm vi.

## Thứ tự xử lý đề xuất

1. R01, R02: trạng thái công việc và nội dung người dùng đang soạn.
2. R03–R09: mở case, phản hồi lỗi, responsive, điều hướng và danh sách/filter.
3. R10 và bốn cải thiện UI: thống nhất trình bày và tối ưu thao tác mobile.

Không thay đổi luồng SMTP, dedupe, quyền, evidence hoặc dữ liệu lịch sử chỉ để
cải thiện UI. Các đề xuất trong báo cáo chưa được triển khai.
