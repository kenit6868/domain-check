# API contract — trạng thái triển khai

Prefix `/api/v1`. OpenAPI ở `/api/v1/openapi.json`; docs chỉ bật ngoài production.
DTO không được trả raw config, local path, secret hoặc exception.

Delivery send endpoints của từng workflow chưa public. Primitive nội bộ hiện yêu cầu
preview đã approve, server-derived business identity và owner context; nó ghi intent
bền vững trước side effect, dùng Message-ID ổn định và checkpoint `sent`/`failed`/
`unknown`. Request đồng thời chỉ một request claim được. `unknown` không retry tự động;
route public chỉ được thêm cùng permission, confirmation và idempotency contract của
feature tương ứng.

## Đã có

| Method/path | Auth | Hành vi |
|---|---|---|
| `GET /health/live` | public | liveness thuần process, không gọi provider |
| `GET /auth/telegram/start` | public | discovery, PKCE/state/nonce, browser-bound cookie; 503 nếu chưa cấu hình |
| `GET /auth/telegram/status` | public | chỉ trả configured/issuer/redirect URI để login page chẩn đoán; không trả client ID/secret |
| `GET /auth/telegram/callback` | flow cookie | consume state một lần, verify ID token, tạo pending/active session |
| `GET /me` | opaque session | trả allowlisted identity/status/timezone/effective permissions |
| `POST /auth/logout` | session + Origin + CSRF | revoke DB session và xóa cookie |
| `GET /users` | `users.view` | tìm kiếm/phân trang user và role, không trả OIDC subject |
| `PATCH /users/{id}/status` | `users.manage` + Origin + CSRF | pending/active/disabled, revoke session, bảo vệ last admin |
| `PUT /users/{id}/roles` | `users.assign_roles` + Origin + CSRF | thay role nguyên tử; chặn delegated manager tự nâng quyền |
| `POST /users/{id}/revoke-sessions` | `users.manage` + Origin + CSRF | tăng authz version và revoke mọi session của user |
| `GET /roles`, `GET /permissions` | `roles.view` | role và permission catalog đã đăng ký trong code |
| `POST/PATCH/DELETE /roles` | `roles.manage` + Origin + CSRF | CRUD role tùy biến; `user` chỉ sửa permission, `admin` không sửa, role hệ thống không xóa |
| `GET/POST /mail-accounts` | `mail_accounts.view_own` / `.manage_own` | list/create sender account thuộc user hiện tại |
| `GET/PATCH/DELETE /mail-accounts/{id}` | own view/manage + CSRF khi ghi | metadata/patch/soft-disable; ID ngoài owner trả 404 |
| `POST /mail-accounts/{id}/test-smtp` | `mail_accounts.test_own` + Origin + CSRF | TLS/login theo transport đã lưu; không gửi message; kết quả/lỗi sanitize |
| `POST /mail-accounts/{id}/test-imap` | `mail_accounts.test_own` + Origin + CSRF | implicit TLS/login/logout; không select/list/search/fetch mailbox |
| `GET/PATCH /settings/profile` | own view/manage + CSRF khi ghi | profile tổ chức/chữ ký mặc định, không nhận owner từ client |
| `POST /cache/clear` | feature `.clear_cache` + Origin + CSRF | chỉ xóa Redis namespace đúng owner/feature/day; không chạm DB/artifact/ledger |
| `GET /artifacts/{id}` | session active + owner + quyền view của feature trong artifact kind | trả bytes đã kiểm size/SHA-256 với `private, no-store`; ID ngoài owner trả 404, kind lạ/sai quyền trả 403 |
| `POST /quick-reports/checks` | `quick_report.check` + Origin + CSRF | tạo target/check/job bền vững theo owner, giữ full URL/thứ tự, trả `202`; không chạy network trong request |
| `GET /quick-reports/checks/{operation_id}` | `quick_report.view` + owner | polling snapshot bền vững; operation ngoài owner trả 404 |
| `POST /extension/devices` | `quick_report.open_form` + Origin + CSRF | đăng ký/cập nhật device ID đã hash cho đúng owner; không nhận capability từ extension |
| `POST /quick-reports/checks/{check_id}/form-tasks` | `quick_report.open_form` + owner + CSRF | tạo task Google/Microsoft `fill_only` từ check đã hoàn tất; capability raw chỉ trả một lần |
| `POST /form-tasks/exchange` | one-time capability + registered device | extension đổi capability lấy payload allowlist và status token; replay/expiry/wrong device fail closed |
| `POST /form-tasks/{id}/status` | status capability | checkpoint trạng thái sanitize; không nhận HTML, Cookie hoặc dữ liệu CAPTCHA |
| `GET /form-tasks/{id}` | `quick_report.view` + owner | FE polling status; ID ngoài owner trả 404 và không trả capability/status token |

RBAC policy: activation tự gán system role `user`, nhưng role này chỉ có các
permission admin đã chọn. System role `admin` luôn đồng bộ với toàn permission
catalog và không sửa qua API. Mọi thay đổi permission role revoke session của user
bị ảnh hưởng; permission không thay thế owner check.

React gọi các route trên qua Axios client dùng `withCredentials`; mutation lấy CSRF
từ cookie `dc_csrf`. Quick Report giữ operation ID trong session storage với namespace
user chỉ để phục hồi F5, còn toàn bộ state/progress/result authoritative vẫn đọc từ
API/PostgreSQL. Response được kiểm schema trước khi render; component không tự tính
verdict, routing hoặc recipient.

Cookie session HttpOnly/SameSite=Lax; CSRF dùng double-submit header với hash lưu
DB. Callback lỗi trả thông báo chung. User pending có permission rỗng. Đổi
`authz_version` làm session cũ mất hiệu lực.

Lỗi HTTP/validation dùng DTO allowlist `code`, `message`, `field_errors`, `request_id`;
response có `X-Request-ID`. Validation không echo body/input. Endpoint ghi không tin
`owner_user_id` hoặc actor trong payload.

Artifact kind do server tạo theo `feature:purpose`; client không được dùng kind để
tự nâng quyền. Endpoint download không trả `storage_key`, local path hoặc filename
nguồn. Upload nghiệp vụ chưa mở thành endpoint chung: Check Domain/Cloaking/Evidence
Review sẽ áp count policy riêng qua EVID-01.

## Chưa mở

`JOB-01` đã cung cấp primitive nội bộ cho create/snapshot/claim/heartbeat/item
checkpoint/stop/resume/retry và crash recovery, nhưng không mở generic `/jobs` endpoint.
Mỗi workflow sẽ công bố route và DTO riêng, kiểm tra permission/owner rồi mới gọi các
primitive này; client không được truyền tên hàm, owner hoặc worker lease token.

`PREVIEW-01` tương tự đã có service nội bộ tạo revision, đọc exact payload và approve.
Không có endpoint generic nhận subject/body rồi coi đó là nội dung đã duyệt. Endpoint
workflow sau này gọi formatter server-side để tạo revision; `GET /previews/{id}` và
approve/send chỉ được mở cùng permission mapping của feature, owner scope và CSRF.

Sáu adapter form task còn lại, Netcraft/export của Quick Report và toàn bộ feature routes còn lại vẫn theo plan. Không thêm
generic endpoint gọi tên hàm Python. Khi bổ sung, lỗi chuẩn sẽ có code/message/
field errors/request ID và owned ID ngoài scope trả 404 khi phù hợp.

