# Quick Report registrar/registry parity — 2026-10-16

## Phạm vi đã đối chiếu

Nguồn là `phishing_toolkit.py` và `pages/7_Quick_Report.py`, không phải bảng
nhà cung cấp mới được tự dựng trong React. Audit không gửi report, SMTP/IMAP
hoặc mở form nhà cung cấp thật.

| Phần | Nguồn legacy | Trạng thái web sau sửa |
|---|---|---|
| Check | `run_cdn_check` | Worker gọi cùng hàm, lưu kết quả owned |
| Registrar web form | 23 quy tắc `WEB_FORM_REGISTRARS` | API lấy trực tiếp bảng, FE mở form và xem/copy draft |
| Registrar email | 48 quy tắc `REGISTRAR_ABUSE_EMAILS`, WHOIS/RDAP fallback | Dùng cùng resolver, hiển thị contact; registrar form-only không bị chuyển sang email |
| Registry | 76 mục `CCTLD_REGISTRY_CONTACTS` | Giữ tra 2-level trước 1-level, email/form/note của bảng |
| Nội dung form | `get_webform_draft_text`, `get_registry_webform_draft_text` | API explicit đọc check/profile đúng owner, giữ full URL, không network |
| Tự điền | 8 adapter | Chỉ provider đã có adapter; form khác mở thủ công |
| Email SMTP | Quick Report legacy không có nút SMTP | Không tự gửi; email ở kết quả là contact để đối chiếu |

## Lỗ hổng đã sửa trong lần audit

- Trước audit, FE bỏ sót form registrar ngoài GoDaddy và nhiều form registry.
- XYZ adapter chỉ xét `.xyz`, bỏ sót `.monster`, `.quest` và các TLD dùng cùng form.
- Suffix `.co` có thể chọn nhầm adapter .CO cho `com.co` của CentralNic.
- Backend mới chọn adapter theo form URL trong chính bảng cũ; không suy diễn chỉ
  dựa vào đuôi TLD. API không sửa lịch sử check hoặc chạy lại probe khi render.

## Cách kiểm thử người dùng

1. Nhập một URL đầy đủ trong danh sách cần điều tra → Lọc danh sách → Kiểm tra.
2. Đợi trạng thái hoàn tất; kiểm tra registrar, registry và email liên hệ.
3. Kiểm tra thứ tự nút Google, Microsoft, Chống Lừa Đảo, Cốc Cốc, Cloudflare,
   Registrar, Registry; chỉ hiện kênh áp dụng.
4. Form có adapter chỉ có một nút tự điền (icon extension), không thêm link thường.
5. Form thủ công: bấm Registrar · tên hoặc Registry · tên, kiểm tra draft đúng
   kênh, brand/contact/full URL rồi copy và mở form. Chưa cần Submit để test.

Các case `.monster`/`.quest` phải có XYZ adapter; `fixture.com.co` phải ra
CentralNic, không có .CO adapter. Kiểm thử các case này bằng fixture, không cần
tra cứu hoặc gửi report tới domain thật.

## Giới hạn đánh giá

Audit xác nhận routing và draft registrar/registry, không tuyên bố toàn bộ web
migration/production release hoàn tất. Browser Evidence, Cloaking Review và
gửi mail có approval/delivery ledger là workflow riêng. Bảng contact vẫn do
chủ dự án quản lý; audit kiểm tra dùng đúng bảng, không xác nhận mọi URL bên
ngoài vẫn giữ nguyên markup hoặc chính sách.

## Inline comparison follow-up

Desktop results have three columns: community providers including Netcraft,
infrastructure, and registrar/registry. Each column contains actions and a draft
panel with a provider/channel selector where needed. Autofill actions explicitly
say “Mở và tự điền”; unsupported forms retain their manual preview action. The
optional include_providers=true endpoint response uses the same task adapter for
supported forms, so role-specific drafts match the extension formatter. Microsoft
has no text body. Netcraft still requires its own exact immutable approval preview.
Validation: frontend 34/34, typecheck/lint/build; backend 118 passed, 1 conditional
skip on fresh isolated PostgreSQL; compileall passed. No live report submitted.

Latest layout: general forms share the top full-width block. CDN, Registrar and
Registry are three separate columns below. Manual buttons open their URL directly;
inline drafts replace the old copy dialog. Netcraft click creates and submits its
server preview directly; no extra confirmation UI. Server send gates still apply.
