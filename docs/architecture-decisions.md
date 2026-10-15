# Architecture decisions

## ADR-006 — Owner scope cố định tại repository boundary

Repository của resource owned nhận `owner_user_id` một lần khi khởi tạo; các thao tác
get/list/count/update/delete không nhận owner lần nữa. API lấy owner từ opaque session,
resource ID ngoài owner trả 404. Quan hệ giữa hai bảng owned dùng composite FK gồm
`owner_user_id`; worker claim toàn hàng đợi là ngoại lệ có chủ đích, sau claim lấy owner
và actor từ chính row DB. Lease worker dùng token cùng optimistic version, transaction
claim ngắn với `FOR UPDATE SKIP LOCKED`; không giữ transaction trong side effect.

Đã kiểm tra trên pool một connection dùng lần lượt A rồi B, cùng URL và cùng SMTP
identity giữa hai owner, nested FK, HTTP API ID chéo và stop job chéo. RLS vẫn là
defense-in-depth về sau, không thay các điều kiện owner này.

## ADR-001 — PostgreSQL + Redis

**Status:** accepted, 2026-10-15.

PostgreSQL là nguồn durable cho identity, owner-scoped operation, jobs, approval,
delivery và audit. JSONB chứa payload biến đổi nhưng không thay schema lõi. Redis
chỉ cache/signal/rate limit/OIDC state có TTL. Lý do là FK/unique/transaction và
concurrent claims phù hợp ledger nhiều account. MongoDB không được triển khai.

Hệ quả: SQLAlchemy/Alembic, integration test bằng PostgreSQL thật, không SQLite
để chứng minh lock/constraint; mất Redis không mất send truth.

## ADR-002 — Shared schema, explicit owner

**Status:** accepted.

Một DB/schema, `owner_user_id = users.id`; actor và sender account tách riêng.
Repository bắt buộc nhận ExecutionContext. Composite constraints/service checks
chặn cross-owner relations; RLS chỉ defense-in-depth sau khi pool-context tests đạt.
Admin không mặc nhiên đọc dữ liệu nghiệp vụ user khác.

## ADR-003 — Durable DB worker, không FastAPI BackgroundTasks

**Status:** accepted.

Worker claim row bằng transaction ngắn/lease/heartbeat rồi chạy side effect ngoài
transaction. Redis có thể wake worker nhưng DB có thể tự recover. Long operations
trả operation ID và polling snapshot. SMTP không exactly-once: intent trước send,
stable Message-ID, uncertain state và reconciliation explicit.

Job/item giữ membership và `input_index` bền vững. Worker chỉ chuyển một item từ
`pending` sang `running` khi còn lease hợp lệ, tăng attempt tại thời điểm bắt đầu và
checkpoint terminal ngay sau mỗi item. Nếu lease chết khi item đang chạy, cả reclaim
thông thường lẫn stop recovery đều chuyển item đó sang `unknown`; không tự đổi thành
`failed` và không cho retry qua action retry-failed. Stop là cooperative: không nhận
item mới, item đang chạy được checkpoint, còn pending được giữ cho resume explicit.

## ADR-004 — Private artifact port

**Status:** accepted.

Filesystem volume là adapter đầu; interface cho S3-compatible sau. Server sinh
key owner/evidence/artifact, upload batch atomic, validate signature/MIME/size/hash,
file immutable. Download/thumbnail luôn authorize. Static FE không phục vụ `data/`.

## ADR-009 — Exact preview là immutable private artifact

**Status:** accepted, 2026-10-15.

Mỗi preview revision lưu toàn bộ delivery plan exact (account, recipient, subject,
plain/HTML body, policy version và artifact bindings) trong JSON artifact private do
server tạo. `preview_plans` chỉ giữ revision, SHA-256 fingerprint, expiry, snapshot
account/profile version và references; audit không chứa recipient hay body. Revision
được cấp tuần tự dưới PostgreSQL advisory transaction lock cho owner + resource.

Approve và send boundary phải đọc lại artifact, kiểm size/SHA-256/canonical payload,
revision mới nhất, expiry, owner, sender enabled/version, profile version, evidence-set
version và bytes attachment/inline logo. Mọi chỉnh sửa tạo revision server-side mới;
frontend không được tự cung cấp fingerprint để biến nội dung chưa duyệt thành hợp lệ.
Schema foundation đã có đủ cột và constraint nên quyết định này không cần migration mới.

## ADR-010 — Delivery intent trước SMTP và uncertain recovery

**Status:** accepted, 2026-10-15.

Delivery được dedupe bằng business identity do workflow adapter phía server cung cấp,
kết hợp workflow/recipient/channel/draft identity và sender account scope. Preview ID
và content fingerprint không tham gia như cơ chế tạo quyền gửi lại. Mỗi delivery có
Message-ID ổn định từ lúc tạo.

Claim dùng row lock + optimistic version và ghi `delivery_attempts.state=intent` cùng
lease trước side effect. Transaction phải commit rồi transport mới được chạy; kết quả
được checkpoint trong transaction khác. `FAILED` xác định có thể retry explicit với
cùng Message-ID. Lease hết hoặc kết nối mất sau intent mà không có checkpoint chuyển
delivery/attempt sang `UNKNOWN` và không tự retry. PostgreSQL bảo vệ ledger nhưng không
được mô tả là exactly-once SMTP. Schema 0001 đã đủ nên không thêm migration.

Một exact preview mới đã duyệt có thể rebind cùng ledger row khi delivery còn `pending`
hoặc `failed`; việc này không tạo business key hay Message-ID mới. Delivery `sending`,
`sent` hoặc `unknown` không được rebind, nên chỉnh draft không mở lại quyền gửi.

## ADR-005 — Telegram OIDC và opaque application session

**Status:** accepted.

Authorization Code + PKCE, verify JWKS/issuer/audience/expiry, identity bằng
issuer+subject. Session cookie opaque HttpOnly/Secure/SameSite=Lax, CSRF + Origin
cho mutation. User mới pending; admin bootstrap explicit, không first-user admin.
System role `admin` nhận toàn bộ permission catalog nhưng vẫn chịu owner scope;
bootstrap chỉ gán role này. Tài khoản thường nhận role `user` khi được kích hoạt,
còn permission của `user` và role tùy chỉnh đều do admin chọn trong DB, không seed
một bộ quyền nghiệp vụ mặc định.

## ADR-006 — React client không chứa business policy

**Status:** accepted.

React hiển thị DTO và gọi action. Verdict/routing/report/permission nằm BE/core.
OpenAPI tạo client. Streamlit là compatibility trong chuyển tiếp, không iframe
public và không bypass auth.

## ADR-007 — Extension capability transport

**Status:** accepted.

Server không mở Chrome operator. FE tạo one-time short-lived capability bound
owner/provider/target/device; extension gọi allowlisted HTTPS API/origin và content
script chỉ chạy provider origins. Quick tasks fill-only; Netcraft submit là action
khác. Không `<all_urls>`, Cookie/HTML/CAPTCHA collection hoặc arbitrary proxy.
Device và task status dùng `review_snapshots` owned hiện có; chỉ hash capability/device
được persist, raw capability trả một lần và sau exchange được thay bằng status token.
Payload tạm chỉ nằm trong `chrome.storage.session`, không nằm trong provider URL,
`localStorage` hay Redis. Google/Microsoft là hai adapter đầu tiên; sáu adapter còn lại
thuộc EXT-02, không được suy diễn là đã port chỉ vì manifest vẫn whitelist origin cũ.

## ADR-008 — Side-effect safety and network boundary

**Status:** accepted.

SMTP/IMAP/provider/capture cần explicit permission/action/confirmation, durable
claim và timeout. Capture/probe chặn loopback/private/link-local/metadata, redirect,
IPv6/DNS rebinding/subresource. Mail endpoint có egress policy riêng. Policy error
không bị đổi thành phishing/link verdict.

## ADR-011 — Feature-based React source và Axios boundary

**Status:** accepted, 2026-10-15.

Frontend chia theo feature; `app` chỉ giữ composition/router/layout, `components`
chứa primitive dùng chung, feature sở hữu API/hooks/components/pages/schema riêng và
`lib` giữ hạ tầng HTTP/query. Một Axios instance cùng origin quản lý cookie, timeout,
CSRF cho mutation và chuẩn hóa error allowlist. Component không gọi HTTP trực tiếp.
TanStack Query key chứa user/resource scope; logout xóa query cache và snapshot trình
duyệt. API response quan trọng được Zod kiểm trước khi render. Quyết định này thay lớp
`fetch` phẳng ban đầu nhưng không chuyển business policy/verdict/routing sang React.

