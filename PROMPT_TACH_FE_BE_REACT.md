# Prompt triển khai Domain Check Tool: React FE + Python BE

Ngày lập: 2026-10-15. Đây là đặc tả và prompt cho một đợt triển khai mới, **không phải mô tả những tính năng đã được triển khai**.

Tài liệu gồm phân tích hiện trạng, quyết định đã chốt PostgreSQL + Redis, yêu cầu cách ly dữ liệu theo user và quy trình lập plan trước khi triển khai. Có thể giao toàn bộ file này cho coding agent làm việc trong repository. Không cần sao chép secret hoặc dữ liệu vận hành vào prompt.

## 1. Nhiệm vụ dành cho coding agent

Bạn là kỹ sư full-stack phụ trách chuyển Domain Check Tool từ ứng dụng Python/Streamlit cục bộ sang ứng dụng web nhiều người dùng, tách frontend và backend.

Hãy triển khai thật từng giai đoạn, có mã nguồn, migration, kiểm thử và tài liệu. Không kết thúc bằng một giao diện demo dùng dữ liệu giả, một lớp API rỗng hoặc chỉ bản kế hoạch. Dữ liệu giả chỉ dùng trong fixture/test và phải tách khỏi môi trường thật.

**Trình tự bắt buộc khi nhận prompt để triển khai: khảo sát checkout → lập plan chi tiết theo mục 18.1 → kiểm tra độ phủ yêu cầu và phụ thuộc → thực hiện từng milestone → kiểm thử và cập nhật plan.** Không bắt đầu di chuyển code hoặc tạo schema khi chưa có plan. Sau khi lập plan, tiếp tục công việc trong phạm vi đã được giao; không tự thêm bước chờ duyệt plan nếu người dùng không yêu cầu. Nếu người dùng chỉ yêu cầu lập/cập nhật prompt hoặc plan, chỉ hoàn thành tài liệu đó, chưa triển khai ứng dụng.

Mục tiêu bắt buộc:

1. FE sử dụng React, TypeScript, Vite và Tailwind CSS; giao diện chuyên nghiệp, nhất quán, phù hợp làm việc với nhiều URL, draft, evidence và worker.
2. BE tiếp tục dùng Python, tổ chức lại thành các module rõ trách nhiệm và cung cấp API bằng FastAPI. Giữ các thuật toán điều tra, nội dung report, routing, tiêu chí review và semantics gửi hiện có.
3. Login bằng Telegram; quản lý user; có role `admin`, `user` và cho phép tạo role khác; cấp quyền theo page và theo hành động.
4. Mỗi user có cấu hình SMTP, IMAP, proxy và chữ ký riêng, tương đương schema cấu hình hiện tại. Không dùng chung cấu hình mail một cách ngầm định.
5. Đây là web tool cho nhiều user dùng đồng thời. URL, kết quả check, job, cache, draft, evidence, lịch sử gửi và trạng thái UI thuộc riêng tài khoản đăng nhập đã tạo chúng. Hai user cùng xử lý một URL vẫn có dữ liệu và quyết định gửi độc lập; một user có thể có nhiều sender account.
6. Bỏ bốn menu: **Thống kê email, Thống kê tổng quát, Case Log, Report Drafts**. Việc bỏ menu không đồng nghĩa xóa logging, draft service, delivery ledger hoặc dữ liệu lịch sử.
7. Ưu tiên hoàn thành theo thứ tự: **Quick Report → Check Domain → Cloudflare Worker → Domain Worker → Cloaking Review → Domain Evidence Review → Check Link Status**. Xây auth, phân quyền, cấu hình mail và storage cần thiết trước các luồng gửi.
8. Giữ **Phản hồi NCC** và **Xử lý đầu mối** trong phạm vi chuyển đổi, thực hiện sau bảy menu ưu tiên. Related Domains/Brand Scan hiện ẩn: giữ core/CLI, không tự đưa trở lại menu chính.

Đọc `AGENTS.md` và toàn bộ file văn bản thực có trong `.agents` trước khi sửa. Áp dụng skill nghiệp vụ takedown. Thư mục `developing-with-streamlit` trong checkout khảo sát đang trống, Git ghi nhận mục skill đã bị xóa trước lượt khảo sát; không tự phục hồi thay đổi này. Nếu phải sửa Streamlit, tìm lại skill theo quy định và báo đúng tình trạng nếu không có. Công việc xây React không cần giả định skill đó tồn tại.

Không đọc/in nội dung `config.ini` thật. Dùng `config.example.ini` và code `load_config()` để biết schema. Không tự gửi SMTP, đọc mailbox, submit report, chạy capture trên URL nghi ngờ hay mở form bên ngoài trong quá trình phát triển; kiểm thử bằng fixture, mock và dry-run.

## 2. Những gì đã xác định từ repository

Ứng dụng hiện là một hệ thống điều tra và báo cáo, không phải chỉ là bộ form Streamlit. Các lớp nghiệp vụ đã có nhiều invariant cần giữ nguyên khi thay UI.

Khảo sát tĩnh tại thời điểm lập prompt: 75 file Python ở root, `pages/`, `tests/`; 36.056 dòng; 1.172 định nghĩa hàm/phương thức, gồm 415 phương thức tên `test_*`. Đây là số đếm AST, **không phải kết quả chạy test**. `.agents` hiện có hai file văn bản: skill takedown và `agents/openai.yaml` của skill. Các số liệu này có thể thay đổi ở checkout triển khai.

### 2.1. Bản đồ chức năng và điểm tái sử dụng

| Nhóm | Nguồn hiện tại | Vai trò cần giữ |
|---|---|---|
| Điều tra đầy đủ | `phishing_toolkit.py`: `run_check()`, các helper SSL, WHOIS/RDAP, DNS, HTTP, VT, Safe Browsing, CDN, MX, reputation | Một pipeline chung cho CLI/UI/API; lỗi từng nguồn không làm mất kết quả còn lại |
| Kiểm tra nhanh | `run_cdn_check()`, `resolve_quick_report_recipients()`, `pages/7_Quick_Report.py` | CDN/registrar/registry, HTTP cloaking và routing webform; khác pipeline điều tra đầy đủ |
| Mẫu báo cáo | Các hàm `generate_*`, `get_webform_draft_text()`, `get_registry_webform_draft_text()` | Giữ full Reported URL, mẫu theo provider, cách chọn biến thể và mức khẳng định dựa trên chứng cứ |
| Email | `phishing_toolkit.py`, `email_send_ui.py` | Transport, proxy, personalize, logo CID, validation, gửi đơn/nhiều account, metadata gửi |
| Domain Worker | `domain_worker.py`, `pages/6_Domain_Worker.py` | Precheck v4, resume, stop, retry delivery thiếu, checkpoint và các nhánh review |
| Cloaking | `cloaking_detector.py`, `cloaking_ui.py` | HTTP nhiều profile trước, Playwright thụ động sau, coverage gap, terminal page, ảnh thủ công |
| Cloaking Review | `cloaking_review_queue.py`, `cloaking_review_sender.py`, page 10 | Queue theo ngày/full URL, ba disposition, exact preview, gửi SMTP trực tiếp và partial delivery |
| Browser Evidence | `browser_evidence.py`, page 1, page 12 | Passive DOM, DOM source/destination, upload, manifest/hash, validation cùng full URL |
| Cloudflare Worker | `cloudflare_form_worker.py`, `cloudflare_dashboard_session.py`, page 14 | Batch qua Cookie Dashboard, ledger v4, pause/resume, dedupe, trạng thái UNKNOWN |
| Kênh Cloudflare khác | `cloudflare_abuse_api.py`, `cloudflare_profile_bridge.py`, extension | API riêng và trợ lý điền form trên máy người vận hành; không nhập nhằng với page Cookie |
| Check Link Status | `link_status.py`, `domain_utils.py`, page 8 | Parser nhóm/brand, check song song, redirect chain, WHOIS Hold, phân loại và export |
| Phản hồi NCC | `provider_replies.py`, page 9 | IMAP theo account/mailbox, phân loại thư, draft Anh–Việt, evidence, reply đúng thread |
| Xử lý đầu mối | `lead_triage.py`, `lead_triage_cloaking_ui.py`, page 15 | Parser target/backend, DNS/RDAP context, snapshot review, dùng chung worker/evidence/review sender |
| Tìm domain liên quan | `crtsh_related()`, page 2 | Certificate Transparency; giữ core/CLI, menu hiện ẩn |
| Brand Scan | `brand_scan()`, page 3 | dnstwist; giữ core/CLI, menu hiện ẩn |
| Analytics sẽ bỏ menu | `mail_statistics.py`, `report_statistics.py`, `sent_mail_evidence.py`, `general_statistics.py`, pages 11/13 | Không port menu; giữ helper còn được Provider Replies hoặc chức năng khác sử dụng |
| Case/draft sẽ bỏ menu | pages 4/5 | Không port menu; draft preview trong workflow và ledger nội bộ vẫn tồn tại |
| Khởi chạy/đóng gói | `launcher.py`, `PhishingTool.spec`, `build_app.bat`, `domain_check.py` | Giữ tương thích legacy trong thời gian chuyển đổi; không gọi launcher desktop trên server |
| Kiểm thử | `tests/` | Characterization tests cho hành vi cũ, AppTest, mock network/browser/SMTP/IMAP |

### 2.2. Những khác biệt dễ dẫn tới triển khai sai

- Chưa có identity/tenant context thực sự. `st.session_state` không đủ để phân tách dữ liệu: `st.cache_resource`, singleton worker, hằng đường dẫn, CSV và JSON vẫn có thể dùng chung toàn server.
- `run_check()` vừa điều tra vừa sinh file draft/log. Bọc FastAPI quanh hàm này mà vẫn giữ các đường dẫn toàn cục sẽ gây đè draft và lộ dữ liệu giữa user.
- Nhiều hàm có default argument trỏ tới `LEDGER_PATH`, `SENT_LOG_PATH` hoặc đường dẫn tương tự. Chỉ thay biến global sau import không thay được mọi default đã bind và không an toàn khi concurrent requests.
- Cache Quick Report gồm kết quả/future trong RAM và file `quick_report_filter_cache.json` chứa `seen`; file lọc hiện không có ngày hết hạn. Không được nói mọi JSON hiện tại đều là cache theo ngày.
- Domain Worker có cả lookup cache trong ngày, lịch sử gửi và cơ chế lọc lịch sử riêng. Phải kiểm tra từng predicate, không thay tất cả bằng một Redis set hostname.
- `capture_verified_navigation_evidence()` hiện chỉ gọi `capture_dom_destination_evidence()`. Luồng hiện tại mở href ở tab mới, **không click thật**, dù một số mô tả/test cũ còn tên “verified”.
- Page Cloudflare hiện dùng Cookie Dashboard, không phải UI API-first. API chính thức và extension vẫn còn trong core cho mục đích khác.
- Cloudflare giữ một bảng kết quả theo thứ tự input, không đẩy dòng đang chạy lên đầu. Một số mục lịch sử mô tả layout cũ hơn.
- Quick Report dùng extension MV3 v2.8.0 trong checkout khảo sát, có tám adapter provider. Microsoft chỉ điền URL, không đổi ngôn ngữ.
- Lead Triage trong code có `MODULE_VERSION = 24`, cao hơn mục tài liệu version 23; có luồng dùng chung `prepare_lead_review()`/`send_lead_review()`. Không chỉ dựa vào changelog để quyết định phạm vi.
- Check Link Status hiện check toàn danh sách bằng thread pool; không phải Domain Worker, không gửi report và không chia batch gửi.
- Có tài liệu baseline test cũ khác nhau. Phải đo baseline ở môi trường triển khai, không dùng số test/lỗi ghi trong lịch sử như kết quả mới.

Nếu tài liệu, comment và code/test mâu thuẫn: ghi lại trong bảng parity, đối chiếu yêu cầu mới và invariant an toàn. Không tự khôi phục hành vi cũ đã bị thay thế. Giữ thuật toán hiện có bằng test; tách mọi sửa bug ngoài migration thành thay đổi được giải thích riêng.

## 3. Quyết định database

### 3.1. Đã chốt: PostgreSQL + Redis

**Chủ dự án đã chọn PostgreSQL.** Dùng PostgreSQL làm nguồn dữ liệu bền vững; Redis làm cache; file/object storage riêng cho evidence. Phân tích bên dưới giải thích lựa chọn kiến trúc, không phải kết quả benchmark tải thật.

Lý do chính là mô hình nghiệp vụ: user → role → permission; user → sender account; case → nhiều job nguồn; preview → nhiều delivery; một delivery cần trạng thái nhất quán khi hai worker/tab thao tác đồng thời. Các quan hệ, khóa duy nhất, transaction và truy vấn ledger phù hợp với database quan hệ. Dữ liệu WHOIS, cloaking và manifest thay đổi cấu trúc vẫn có thể nằm trong JSONB, không cần MongoDB chỉ vì kết quả hiện là dict/JSON. PostgreSQL hỗ trợ JSONB và index cho dữ liệu JSON. [Tài liệu JSONB](https://www.postgresql.org/docs/current/datatype-json.html).

Có thể bổ sung Row-Level Security để bảo vệ dữ liệu theo owner bên cạnh kiểm tra API. Khi dùng, runtime DB role không được có `BYPASSRLS`, không dùng superuser; cấu hình đúng việc owner table có thể bỏ qua policy. RLS không thay thế kiểm tra quyền hành động ở ứng dụng. [Tài liệu RLS](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).

| Tiêu chí của dự án | PostgreSQL | MongoDB |
|---|---|---|
| User/role/permission và quan hệ nhiều-nhiều | Tự nhiên với FK và bảng liên kết | Làm được bằng reference/embedded document; cần giữ nhất quán ở service |
| Case, job, delivery ledger, audit | Phù hợp với transaction và constraint giữa bảng | Làm được; cần thiết kế boundary document/transaction |
| WHOIS/cloaking/snapshot nhiều field tùy chọn | JSONB trong bảng có schema lõi | Document thuận tiện |
| Dedupe/concurrency | Unique constraint + atomic update/transaction | Compound unique index + atomic update/transaction |
| Phân tách theo user | Query scope, có thể thêm RLS | Query scope bắt buộc trong repository |
| Độ phức tạp vận hành ban đầu | Một primary DB + Redis | Một replica set + Redis nếu cần transaction nhiều document |
| Khi nên chọn | Mặc định cho yêu cầu hiện tại | Đội vận hành quen MongoDB hoặc đã có hạ tầng MongoDB được quản lý |

MongoDB không thiếu transaction: có transaction nhiều document trên replica set/sharded cluster; standalone không phải lựa chọn tương đương cho thiết kế này. Unique index vẫn cần để chặn trùng ở database. [MongoDB transactions](https://www.mongodb.com/docs/manual/core/transactions/), [unique indexes](https://www.mongodb.com/docs/manual/core/index-unique/).

### 3.2. Phạm vi quyết định

MongoDB chỉ còn trong bảng so sánh để giải thích quyết định. Không triển khai MongoDB adapter, collection hoặc vận hành song song hai primary database. Dùng SQLAlchemy/Alembic và PostgreSQL cho schema bên dưới; repository interface phục vụ tách nghiệp vụ và legacy compatibility, không để trì hoãn việc chốt database.

### 3.3. Redis dùng đúng vai trò

- Cache kết quả lookup có thể tái tạo, cache UI theo user/ngày, rate limit, tín hiệu thông báo cập nhật và dữ liệu tạm như OAuth state có TTL.
- Redis không phải nguồn duy nhất cho trạng thái đã gửi, `UNKNOWN`, approval, preview exact cần phục hồi, tiến trình job hoặc metadata evidence.
- Mất Redis: cache miss, có thể yêu cầu login lại nếu dùng session cache; không mất lịch sử và không gửi lại delivery đã xong.
- Redis có eviction và các lựa chọn persistence với đánh đổi mất dữ liệu. AOF không biến SMTP thành giao dịch exactly-once. [Redis eviction](https://redis.io/docs/latest/develop/reference/eviction/), [Redis persistence](https://redis.io/docs/latest/management/persistence/).
- Không persist Cookie Cloudflare vào Redis, kể cả Redis được gọi là “cache”.

## 4. Stack và ranh giới kiến trúc

### 4.1. Stack đề xuất

| Thành phần | Lựa chọn |
|---|---|
| FE | React + TypeScript strict + Vite |
| CSS/UI | Tailwind CSS, shadcn/ui/Radix primitives, Lucide icons |
| Routing/server state | React Router, TanStack Query |
| Form/table | React Hook Form + Zod; TanStack Table, virtualization khi cần |
| BE | Python + FastAPI + Pydantic |
| DB | PostgreSQL + SQLAlchemy + Alembic |
| Cache | Redis |
| Background work | Python worker service riêng; durable job repository trong DB |
| Capture | Playwright/Chromium trong worker cô lập |
| Artifact | Private filesystem volume giai đoạn đầu; interface hỗ trợ S3-compatible storage |
| Test FE | Vitest, React Testing Library, Playwright E2E |
| Test BE | Giữ unittest cũ, thêm API/integration tests có DB/Redis test riêng |
| Vận hành | Docker Compose cho dev/triển khai nhỏ; reverse proxy HTTPS cùng origin FE/API |

Chọn các phiên bản stable tương thích tại thời điểm triển khai, pin và commit lockfile. Không tự dùng phiên bản prerelease hoặc copy cấu hình Tailwind cũ không phù hợp major đang chọn. Nếu dùng Tailwind hiện hành với Vite, đối chiếu plugin chính thức. [Tailwind với Vite](https://tailwindcss.com/docs/installation/using-vite).

Không thêm Kubernetes, Kafka, microservice hoặc hai primary database khi chưa có nhu cầu đo được. API, worker và Cloudflare session worker có thể dùng chung một codebase Python nhưng chạy thành process riêng.

### 4.2. Luồng hệ thống

```text
Browser React
  ├─ HTTPS /api/v1 → FastAPI → auth + RBAC + owner scope → application services
  │                                                 ├─ PostgreSQL
  │                                                 ├─ Redis cache
  │                                                 └─ private artifact storage
  ├─ progress polling → API đọc durable job/check state
  └─ thao tác tự điền form → extension trên máy operator → provider form

Python worker → claim job trong DB → gọi core nghiệp vụ → checkpoint DB/artifact
Cloudflare session worker → Cookie trong RAM → Dashboard adapter → ledger DB
Review send request → exact preview đã duyệt → SMTP helper → delivery checkpoint
CLI legacy → compatibility facade → cùng core nghiệp vụ
```

Polling 2–3 giây khi có tác vụ đang chạy là đủ cho phiên bản đầu; dừng khi terminal, tab hidden có thể giảm tần suất. SSE là nâng cấp tùy chọn sau khi có nhu cầu, vẫn phải đọc lại snapshot khi reconnect. Không dùng event stream làm nguồn trạng thái duy nhất.

FastAPI `BackgroundTasks` không được dùng làm cơ chế bền vững cho batch gửi/capture dài. Tác vụ phải tiếp tục khi browser F5 và có thể phục hồi sau process chết. [Hướng dẫn BackgroundTasks](https://fastapi.tiangolo.com/tutorial/background-tasks/).

## 5. Cấu trúc dự án đích và quy tắc refactor

```text
frontend/
  src/
    app/                   # router, providers, shell, error boundaries
    components/ui/         # button, dialog, field, table, badge, tabs
    components/shared/     # URL input, evidence gallery, preview, job progress
    features/
      auth/ users/ roles/ mail-settings/
      quick-report/ check-domain/ cloudflare-worker/ domain-worker/
      cloaking-review/ domain-evidence-review/ link-status/
      provider-replies/ lead-triage/
    lib/                   # generated API client, query keys, permissions
    styles/
  tests/
  package.json
  vite.config.ts

backend/
  src/domaincheck/
    api/                   # v1 routers, request/response models, dependencies
    application/           # use cases; orchestration + authorization
    domain/                # types, pure policies, state transitions
    core/                  # nghiệp vụ Python được di chuyển từng phần
      investigation/       # DNS, WHOIS, RDAP, SSL, HTTP, reputation
      reports/             # formatters, recipient routing, validation
      evidence/            # browser, manual upload, manifest/hash
      cloaking/            # detector, review decisions
      mail/                # SMTP/MIME, IMAP/reply
      cloudflare/          # Dashboard/API adapters, form task contract
      leads/               # parser và backend context
      links/
    ports/                 # repository, config, artifact, clock interfaces
    infrastructure/
      db/ cache/ artifacts/ auth/ secrets/ networking/
    workers/               # job runner, capture runner, session runner
    compatibility/         # adapter cho dict/API legacy
    settings.py
    main.py
  migrations/
  tests/
  pyproject.toml

chrome_extension/cloudflare-profile-worker/  # giữ adapter; đổi transport có test
tests/                      # regression suite legacy trong thời gian chuyển đổi
docs/                       # parity, API, ADR, migration, vận hành, test results
deploy/                     # Dockerfiles, proxy, healthcheck
compose.yaml
.env.example                # chỉ placeholder
phishing_toolkit.py          # facade giữ import/CLI cũ sau khi migrate
domain_worker.py             # facade/entrypoint compatibility khi cần
```

Đây là cấu trúc đích, không phải lệnh di chuyển toàn bộ file ngay trong commit đầu.

1. Đầu tiên viết characterization tests và bảng `old module/function → new module/function`.
2. Tạo `ExecutionContext` bất biến gồm actor, owner, timezone, operation ID, repository, artifact store, config resolver và clock. `owner_user_id` được lấy từ phiên/quyền server; không tin owner do FE tự gửi.
3. Tách persistence/network/config khỏi nghiệp vụ bằng interface; `LegacyFileRepository` phục vụ CLI cũ, DB repository phục vụ web. Hai adapter dùng cùng policy nghiệp vụ.
4. Các core API đang nhận `cfg` tiếp tục nhận bản config tương thích, do service resolve riêng cho user. Các hàm còn tự `load_config()` cần nhận resolver/context khi chạy web.
5. Không thay global path, `os.chdir`, environment hoặc monkeypatch module theo từng request. Không để draft/evidence của hai user chung tên file hoặc cache singleton.
6. Rà cả default arguments đã bind đường dẫn, global session `requests`, singleton browser, threading locks, CSV reader/writer, runtime path fallback, process launcher và hàm riêng trong page đang chứa nghiệp vụ.
7. Giữ CLI `check`, `related`, `brandscan`, `send`; giữ chữ ký public tương thích qua facade. Có smoke test import/CLI, không chỉ sửa test cho khớp implementation mới.
8. FE không sinh lại report, tự tính verdict hay tự suy ra permission từ tên role. FE hiển thị DTO và gọi action; BE quyết định.
9. Không dùng iframe nhúng Streamlit để tuyên bố đã chuyển React. Streamlit chỉ còn legacy trong giai đoạn chuyển tiếp và không được public trực tiếp để bỏ qua auth mới.

## 6. Thiết kế dữ liệu bền vững

### 6.1. Model tối thiểu

Các entity owned có `id`, `owner_user_id`, `created_at`, `updated_at`, `version`; các sự kiện có thêm `actor_user_id`. Thời gian lưu UTC; `business_day` là ngày tính từ timezone IANA đã chốt cho operation. Mặc định ban đầu `Asia/Bangkok` để khớp môi trường khảo sát; không lấy timezone từ OS container.

| Entity | Field quan trọng / ràng buộc |
|---|---|
| `users` | display name, Telegram username để hiển thị, `status=pending/active/disabled`, timezone, authz version |
| `auth_identities` | user ID, provider, issuer, subject ổn định; unique `(issuer, subject)` |
| `sessions` | hash opaque token, user ID, created/expiry/revoked timestamps; không lưu raw session token |
| `roles`, `permissions` | role key/name, permission code/description; unique key/code |
| `user_roles`, `role_permissions` | composite unique, FK; kiểm soát gán role ở BE |
| `sender_accounts` | owner, label, SMTP/IMAP config, encrypted secret reference, enabled, config version |
| `user_settings` | brand/contact/company/signature defaults, logo artifact ID, integration/proxy references |
| `targets` | owner, full URL identity, hostname, identity version, first/last seen; unique theo owner + identity version + full URL identity, không theo ngày |
| `checks` | target ID, requested URL, workflow, canonical workflow key, domain, day/timezone, engine version, sanitized result JSONB, status, cache provenance |
| `jobs`, `job_items` | loại job, owner, actor, input order, account scope, stage, stop flag, attempt, lease, heartbeat, result metadata |
| `review_cases` | type, full URL, day, disposition, state, evidence version, required account IDs |
| `case_sources` | case ↔ job/item/check; không mất provenance khi dedupe |
| `preview_plans` | case/operation ID, owner, revision, fingerprint, expiry, account versions, recipients, draft/attachment references, approval |
| `deliveries` | business delivery key, account ID, recipient, draft/channel identity, preview reference, Message-ID, state |
| `delivery_attempts` | delivery ID, attempt number, SMTP stage, started/finished time, sanitized result |
| `evidence_sets`, `artifacts` | owner, check/case, requested/landing/destination URL, capture mode, manifest, file MIME/size/hash, storage key |
| `cloudflare_records` | owner, URL/day, report version, idempotency key, provider result/report ID, state |
| `review_snapshots` | owner, feature, context key, sanitized UI state/draft references; không chứa credential |
| `audit_events` | actor/owner, action/resource ID, timestamp, outcome; allowlist metadata, không raw request/body/secret |
| `migration_runs` | version, source fingerprint, owner mapping, counts, errors, completion checkpoint |

Draft body cần cho preview exact được lưu trong kho draft/artifact private có kiểm soát truy cập và retention. Ledger/audit chỉ lưu ID/hash/metadata, không copy toàn bộ body vào log. MIME/attachment references phải đủ để dựng lại đúng nội dung đã preview.

### 6.2. Constraint, dedupe và ownership

- Review case unique theo `(owner_user_id, case_type, business_day, canonical_full_url_key)`; không theo worker job hoặc sender account.
- `case_sources` giữ tất cả job nguồn. Giữ terminal state/evidence mới nhất theo policy hiện có, không tùy tiện ghi đè `SENT` thành pending khi đọc lại job.
- `job_items` unique `(job_id, item_identity)` và lưu `input_index`; không dựa vào row index FE làm ID.
- Delivery key phải xuất phát từ identity mà từng workflow hiện dùng, mở rộng thêm owner và stable sender account ID. Bảng parity phải chỉ rõ scope theo full URL/domain, ngày/lịch sử, recipient, channel và draft identity của từng workflow.
- **Không dùng preview ID hoặc content hash mới làm cách vượt dedupe đã gửi.** Fingerprint khóa nội dung; business key ngăn gửi lại. Draft chỉnh sửa không tự tạo quyền gửi lại trong cùng scope.
- Idempotency request unique theo owner + action + idempotency key; hash request body để phát hiện cùng key khác payload. Với Cookie request, không lưu Cookie trong bản request dùng đối chiếu.
- Không dùng địa chỉ SMTP `username` như identity duy nhất của account; user khác có thể khai báo cùng địa chỉ nhưng ownership độc lập. Tài khoản duplicate trong cùng user phải được normalize/chặn theo identity đã thống nhất.
- Foreign key kết hợp owner hoặc repository validation bắt buộc ngăn gắn account/evidence/preview của user B vào case của user A. UUID khó đoán không thay thế authorization.
- Soft disable account/user còn tham chiếu ledger; không cascade xóa lịch sử. Không cho xóa/gỡ quyền admin cuối cùng bằng thao tác đồng thời.
- Queue có thể giữ history dài hạn, UI chỉ lấy ngày hiện tại theo policy. Sang ngày không tự gửi/check lại, chỉ tạo case mới sau thao tác check.

### 6.3. Artifact storage

- Object key do server sinh, ví dụ `users/{owner_id}/evidence/{evidence_id}/{artifact_id}.png`; không dùng filename/đường dẫn do client cấp để đọc file máy chủ.
- API upload validate toàn batch, MIME signature, giới hạn bytes/dimensions, số lượng; chặn path traversal và file giả ảnh. Tránh ghi một phần khi batch không hợp lệ.
- Download/thumbnail qua endpoint kiểm tra ownership; nếu signed URL thì TTL ngắn và chỉ tạo sau authorize. Bucket/volume không public.
- Manifest giữ SHA-256 và URL provenance. File immutable; thay ảnh tạo evidence version mới và invalidates preview.
- Không sửa manifest gốc khi import nếu làm mất hash; dùng bảng mapping storage key/path legacy. Không xóa evidence khi bấm “Xóa cache”.

### 6.4. Khi nào ghi DB và danh sách bảng đã chọn

PostgreSQL giữ cả URL đã xử lý, kết quả kiểm tra và job/delivery. Không chỉ lưu user/config rồi để các thao tác còn lại ở Redis hoặc JSON.

| Thao tác | Ghi bền vững | Dữ liệu tạm |
|---|---|---|
| Đang gõ/dán, chưa bấm Check | Không tạo target/check/job; có thể autosave bản nháp UI vào `review_snapshots` nếu cần phục hồi F5 | Form state ở FE; cache UI theo user |
| Bấm lọc danh sách | Chỉ lưu snapshot danh sách nếu cần; chưa tạo kết quả điều tra | Parsed list; không tự network/check/send |
| Bấm Check một/nhiều URL | Upsert `targets`, tạo `checks` trước xử lý, lưu status/result/error/thời điểm; operation được khôi phục qua ID | Cache lookup có TTL ở Redis |
| Check dùng lại kết quả cache | Ghi rõ kết quả được tái sử dụng, source check và observed timestamp; không ghi thành quan sát mạng mới | Redis cache hit |
| Tạo/chạy job | `jobs`, `job_items`, account scope, quyền xác nhận và checkpoint | Cache progress có thể tạo lại |
| Chụp/upload bằng chứng | `evidence_sets`, `artifacts`, manifest/hash/provenance | File staging tạm trước validate/commit |
| Case cần duyệt | `review_cases`, `case_sources`, evidence version và disposition | Selection/expanded panel ở FE |
| Tạo preview | `preview_plans`, revision, draft/artifact references, fingerprint và expiry | Form chỉnh sửa chưa duyệt |
| Gửi mail | `deliveries`, `delivery_attempts`, intent/claim trước gửi và outcome sau từng lần gửi | SMTP credential đã giải mã chỉ trong runtime cần dùng |
| Submit Cloudflare | `cloudflare_records` và job/item state | Cookie chỉ RAM của session worker |

Đề xuất **24 bảng nền tảng** cho phạm vi đã chốt, chia migration theo giai đoạn:

1. Identity/RBAC (7): `users`, `auth_identities`, `sessions`, `roles`, `permissions`, `user_roles`, `role_permissions`.
2. Cấu hình (2): `user_settings`, `sender_accounts`.
3. URL/check/job (4): `targets`, `checks`, `jobs`, `job_items`.
4. Duyệt/gửi (5): `review_cases`, `case_sources`, `preview_plans`, `deliveries`, `delivery_attempts`.
5. Bằng chứng (2): `evidence_sets`, `artifacts`.
6. Cloudflare/hỗ trợ vận hành (4): `cloudflare_records`, `review_snapshots`, `audit_events`, `migration_runs`.

Đây không phải 24 bảng đã tồn tại. Chưa có PostgreSQL implementation trong lượt lập prompt. Khi port Provider Replies có thể cần thêm bảng mailbox/message/thread chuyên biệt; danh sách này là nền tảng cho auth/config và bảy menu ưu tiên, không giới hạn schema của mọi tích hợp tương lai.

`targets` lưu đối tượng ổn định; `checks` lưu từng lần kiểm tra. Cùng một URL của cùng user có thể có nhiều check khác ngày hoặc khác workflow; check mới không ghi đè lịch sử cũ. Khác user là scope khác, cùng hostname khác path/query là target khác. Full URL identity bảo toàn scheme/path/query theo quy tắc đã test; key dedupe riêng của Quick Report/Worker vẫn giữ semantics cũ, không ép tất cả dùng identity của `targets`.

Ví dụ: một user gửi job gồm 20 URL, hai account và mỗi URL có một recipient/draft đủ điều kiện: có một `jobs`, 20 `job_items` và tối đa 40 delivery nghiệp vụ. Mỗi delivery có một hoặc nhiều attempt; retry chỉ thêm attempt cho delivery được phép retry, không nhân đôi job hoặc reset delivery đã gửi. Số delivery thực tế còn phụ thuộc channel, recipient, case bị loại/review và dedupe.

Không cần bảng `domains` riêng ở giai đoạn này: hostname là field có index trong `targets`; khi cần shared enrichment/domain inventory mới cân nhắc tách. Không có bảng ứng với từng menu một cách máy móc: Check Domain/Quick Report/Link Status dùng `checks.workflow`, hai review page dùng `review_cases.case_type`. Fields phục vụ filter/constraint nằm ở cột có kiểu rõ ràng; payload khác cấu trúc như WHOIS/cloaking nằm trong JSONB.

Sang ngày mới tạo check/case mới khi có action; không xóa target, delivery hoặc lịch sử ngày cũ. Retention kết quả check/snapshot có thể cấu hình riêng sau khi xác định nhu cầu lưu, không dùng TTL cache làm chính sách xóa DB.

### 6.5. Hợp đồng cách ly dữ liệu cho nhiều user

**Đơn vị sở hữu dữ liệu của phiên bản này là tài khoản ứng dụng (`users.id`).** Không phải Telegram username, SMTP username, IP truy cập hoặc browser session. Chưa có workspace dùng chung/team sharing; không tự thêm cơ chế chia sẻ target/job giữa user. Nhiều session hoặc thiết bị của cùng một user nhìn thấy cùng dữ liệu đã lưu của tài khoản đó, còn thao tác chưa submit có thể chỉ ở form hiện tại.

Dùng một PostgreSQL database/schema chung với ownership rõ ràng; không tạo database/schema riêng cho từng user ở phiên bản đầu. Phân biệt ba khái niệm:

- `owner_user_id`: tài khoản sở hữu tài nguyên và phạm vi dữ liệu.
- `actor_user_id`: người thực hiện hành động, phục vụ authorization/audit; bình thường trùng owner.
- `sender_account_id`: một tài khoản mail thuộc owner; không dùng thay identity đăng nhập.

| Thành phần | Phạm vi bắt buộc |
|---|---|
| URL/check và filter duplicate | User + identity URL/workflow; thêm ngày hoặc phiên bản khi policy yêu cầu |
| Job/item/status/progress | Owner của job, item bắt buộc cùng owner; scope sender được snapshot khi tạo |
| Lookup cache/no-email/UI cache | Namespace user + feature + key/version/ngày thích hợp |
| Draft/preview/approval | Owner + resource + revision; không dùng approval của user khác |
| Evidence/upload/download/export | Owner của case/check; cả thumbnail, file tạm và signed URL đều kiểm tra quyền |
| Delivery/dedupe/retry | Owner + account + khóa nghiệp vụ workflow; không dedupe toàn cục chỉ theo domain |
| Cloaking/Evidence Review | Case và toàn bộ job nguồn cùng owner; ngày không thay ownership |
| Cloudflare ledger/Cookie/form task | Owner + operation/job; Cookie RAM và extension capability cũng có scope user |
| Mailbox/reply/lead snapshot | Owner + account hoặc context; không dùng cache mail/ghi chú chung |
| Tác vụ clear/stop/delete | Chỉ ảnh hưởng resource của user đã authorize; không có thao tác global ngầm định |

Các quy tắc thực thi:

1. Auth middleware tạo context từ session server-side. API không nhận `owner_user_id` rồi tin trực tiếp; user sửa query/body/header không thay được owner. Chỉ đường quản trị được thiết kế riêng mới có thể truy cập scope khác sau kiểm tra quyền và audit.
2. Repository method cho tài nguyên owned bắt buộc nhận owner context; filter owner áp dụng cho list, count, search, get-by-ID, update, delete, bulk operation và join. Không chỉ lọc kết quả ở FE hoặc sau khi đã đọc file.
3. Bảng liên kết/reference của resource owned phải ngăn cross-owner relation. Với PostgreSQL ưu tiên composite FK/unique constraint phù hợp cùng service checks; nếu dùng RLS phải kiểm thử context với connection pool, không để identity của request trước tồn tại trên connection được tái sử dụng.
4. Worker nhận ID job, nạp owner/actor/scope từ DB đã xác thực rồi resolve account đúng owner. Không dùng config của user vừa login gần nhất, `st.cache_resource` cũ, global `cfg` hoặc singleton session browser/Cookie chung.
5. Connection pool và tài nguyên executor có thể dùng chung để tiết kiệm tài nguyên; dữ liệu mutable của operation, browser context, credentials, output paths và locks không được lẫn giữa user.
6. Lock/dedupe key phải có owner và resource scope. A dừng job hoặc clear cache không dừng/xóa công việc của B. Hạn mức toàn hệ thống vẫn được phép để bảo vệ tài nguyên, đồng thời có hàng đợi công bằng và giới hạn theo user.
7. Admin quản lý danh tính/role không mặc nhiên có quyền đọc draft/evidence/mailbox của mọi user. Phiên bản đầu không có impersonation hoặc silent owner override. Nếu sau này bổ sung truy cập hỗ trợ, phải có permission riêng, actor/owner explicit và audit.
8. Dữ liệu được phép dùng chung gồm permission catalog, role definitions do admin quản lý, provider routing/template tĩnh không chứa thông tin riêng và cấu hình hạ tầng. Không chia sẻ check results, notes, sender config hoặc user cache giữa các tài khoản để tối ưu sớm.
9. Không tạo chung thư mục tạm theo hostname. Mọi artifact tạm/hoàn chỉnh thuộc owner + operation ID; cleanup theo scope, chống xóa file đang được job khác dùng.
10. FE clear query cache/state/subscription khi logout hoặc đổi account. Response đang bay của user cũ không được cập nhật màn hình user mới. Trên cùng user, snapshot cần có context/version để hai tab không âm thầm ghi đè preview đã duyệt.

Kịch bản bắt buộc: A và B cùng check `https://example.com/login`, cùng tạo job và cùng gửi bằng account của mình. Hệ thống giữ hai bộ dữ liệu độc lập; A đã gửi không đánh dấu B đã gửi. A clear cache không xóa target/delivery lịch sử của A và không thay đổi cache của B. A nhập ID job/artifact/account của B vào API phải bị từ chối. Nếu provider bên ngoài trả dedupe dùng chung cho cùng URL, lưu đúng phản hồi đó vào operation của B; không cam kết provider cũng phân tách theo user của ứng dụng.

## 7. Telegram login, session và phân quyền

### 7.1. Telegram login

Triển khai login web bằng **Telegram OpenID Connect Authorization Code Flow + PKCE**, backend đổi code và xác minh ID token. Đăng ký allowed URLs/redirect URI qua BotFather; client secret chỉ ở BE. Verify signature qua JWKS, issuer, audience và expiry; khóa danh tính bằng issuer + subject, không bằng username có thể đổi. Telegram hiện có hướng dẫn OIDC chính thức; không mặc định dùng widget iframe legacy hoặc trộn xác minh Mini App `initData` vào web login. [Telegram Login](https://core.telegram.org/bots/telegram-login).

Các yêu cầu ứng dụng của dự án:

- Login state ngẫu nhiên, dùng một lần, TTL ngắn, gắn với browser initiating flow; PKCE verifier không đưa vào log. Kiểm tra nonce nếu flow/provider đang dùng hỗ trợ, có test replay và callback sai state.
- Scope tối thiểu phục vụ identity; không xin số điện thoại hoặc quyền nhắn tin chỉ để đăng nhập. Không gửi tin Telegram như tác dụng phụ của login.
- Session của ứng dụng độc lập với Telegram token: cookie opaque, `HttpOnly`, `Secure`, `SameSite=Lax`, host-only; rotate lúc login, logout revoke server-side.
- Với API thay đổi dữ liệu dùng CSRF token và kiểm tra Origin. Không để access token/session token trong localStorage hoặc URL.
- User mới mặc định pending; admin kích hoạt/gán role. Trang chờ duyệt không được gọi nghiệp vụ hoặc nhìn thấy dữ liệu user khác.
- Bootstrap admin bằng danh tính Telegram ổn định được cấu hình rõ hoặc CLI quản trị cục bộ. Không cấp admin cho người login đầu tiên; không hardcode username.
- Disable user hoặc đổi quyền tăng authz version, revoke/invalidate session theo chính sách; worker kiểm tra quyền trước side effect tiếp theo. Request đang gửi ra ngoài có thể hoàn tất, phải checkpoint kết quả.
- Login callback chỉ redirect về route nội bộ allowlist; lỗi hiển thị ngắn gọn, không lộ token. Unit/integration test dùng mock issuer; test live Telegram phải được yêu cầu riêng.

### 7.2. RBAC có thể mở rộng

Role là dữ liệu quản trị, không là enum chỉ có hai giá trị. Cho phép tạo role, đổi tên/mô tả, gán permission đã đăng ký; một user có thể có nhiều role. Effective permission là hợp quyền của các role; mặc định deny. Không cần deny override hoặc role inheritance trong phiên bản đầu.

Permission catalog phải được khai báo cùng endpoint/action trong code rồi đồng bộ DB. Admin tạo một chuỗi permission tùy ý không được tự động tạo ra chức năng hoặc mở quyền không có enforcement.

| Nhóm | Permission ví dụ |
|---|---|
| Quick Report | `quick_report.view`, `.check`, `.open_form`, `.submit_netcraft`, `.export`, `.clear_cache` |
| Check Domain | `check_domain.view`, `.check`, `.submit_vt`, `.capture`, `.upload`, `.preview`, `.send` |
| Cloudflare | `cloudflare_worker.view`, `.precheck`, `.start`, `.resume`, `.stop`, `.retry`, `.clear_cache` |
| Domain Worker | `domain_worker.view`, `.precheck`, `.start`, `.stop`, `.retry`, `.clear_cache` |
| Cloaking Review | `cloaking_review.view`, `.upload`, `.preview`, `.approve`, `.send`, `.skip` |
| Evidence Review | `domain_evidence_review.view`, `.upload`, `.preview`, `.send` |
| Link Status | `link_status.view`, `.check`, `.export` |
| Mail settings | `mail_accounts.view_own`, `.manage_own`, `.test_own` |
| User/role admin | `users.view`, `.manage`, `.assign_roles`, `roles.view`, `.manage` |
| Giai đoạn sau | `provider_replies.view`, `.sync`, `.mark_seen`, `.preview`, `.send`; `lead_triage.view`, `.check`, `.preview`, `.send` |

Tên viết tắt `.action` trong bảng phải triển khai thành mã đầy đủ, ví dụ `cloaking_review.send`.

- Mỗi action cần quyền view của feature, quyền action và ownership phù hợp. Có quyền xem page không mặc nhiên có quyền gửi/capture/đọc IMAP.
- API kiểm tra độc lập FE; ẩn menu/nút chỉ là UX. `/me` trả permissions để FE dựng navigation và giải thích action bị khóa.
- Role `user` mặc định chỉ được cấp bộ quyền ít tác dụng phụ do seed quy định, có thể check/preview và quản lý mail của mình; send/start thật phải cấp rõ. Role `admin` quản lý user/role nhưng không được API trả plaintext secret của người khác.
- User ID trong job/case không được lấy từ request rồi tin ngay. Nếu cần hỗ trợ cross-user, thêm permission riêng, context owner explicit và audit actor/owner; không tự thêm “admin nhìn tất cả” ở mọi endpoint.
- Không cho người có quyền gán role tùy ý nâng quyền vượt phạm vi quản trị được cấp. Khóa thao tác admin cuối cùng bằng transaction, không chỉ check ở FE.

### 7.3. Page quản lý user và role

Trang User Management có tìm kiếm, phân trang, trạng thái pending/active/disabled, Telegram identity hiển thị, lần đăng nhập cuối, role chips và action kích hoạt/khóa/gán role/revoke sessions.

Trang Role & Permission có danh sách role, tạo/sửa role, ma trận page/action, mô tả quyền và preview quyền có hiệu lực. Role hệ thống được bảo vệ khỏi xóa nhầm. Mọi thay đổi có audit metadata; không hiển thị mật khẩu SMTP, Cookie hay token.

## 8. Menu cấu hình mail theo user

Tên menu: **Tài khoản gửi mail**. Một user quản lý nhiều account; chọn account mặc định và bật/tắt từng account. Tách các nhóm SMTP, IMAP, proxy, chữ ký và profile tổ chức để người dùng không phải sửa JSON.

### 8.1. Schema và mapping legacy

| Cấu hình hiện tại | Model/API mới |
|---|---|
| `[smtp].accounts[]`: `host`, `port`, `username`, `password`, `ssl`, `starttls` | `sender_accounts` với secret mã hóa, chế độ transport hiện rõ |
| `imap_host`, `imap_port`, `imap_mailbox`, `imap_sent_mailbox` | Nhóm IMAP, folder override; giữ semantics tương thích khi adapter gọi legacy |
| `imap_junk_mailbox`, `imap_cloudflare_mailbox`, `imap_evidence_mailbox` | Folder cho Provider Replies; tên Unicode được giữ và encode tại IMAP boundary |
| `[smtp].proxies` | Proxy configuration thuộc user, mapping account có thứ tự để giữ round-robin cũ |
| `contact_name`, `contact_email` theo account | Override chữ ký theo account; fallback theo helper hiện tại |
| `[company]`: `brand_name`, `contact_name`, `contact_email`, `company_name`, `business_registration_no`, `signature_role`, `signature_logo` | `user_settings` + override account; logo là private artifact ID |
| `[api]`, `[cloudflare]`, `[cloaking]` | Integration settings được phân quyền; không trộn secret hạ tầng với field công khai FE |
| SMTP đơn legacy `host/username/password` | Import thành một sender account của owner được chọn rõ |

Không copy tên người, email hoặc pháp nhân mẫu trong repo làm giá trị mặc định cho mọi user mới. Dùng placeholder trung lập; dữ liệu tổ chức lấy từ cấu hình của chính user.

### 8.2. Secret và hành vi lưu

- SMTP/IMAP password, proxy credential và API secret được mã hóa có xác thực bằng cơ chế thư viện chuẩn. Lưu `ciphertext`, nonce và key version hoặc secret reference; master key nằm ngoài DB/repo/FE, hỗ trợ rotation và backup key an toàn.
- Không hash password SMTP theo cách không thể khôi phục vì backend phải dùng nó để authenticate; password login riêng không được thêm khi chỉ cần Telegram.
- API GET chỉ trả `has_password` và metadata. PATCH không có password nghĩa giữ nguyên; thay secret là thao tác riêng; không gửi lại chuỗi mask `********` làm password.
- Không dùng `VITE_*` cho bất kỳ secret nào: biến build FE là công khai.
- Nút lưu chỉ validate schema và persist, không tự mở SMTP/IMAP. Test connection là action riêng, chỉ kết nối/TLS/auth, không gửi mail; test IMAP là action riêng với phạm vi rõ. Không có auto-test khi mở page.
- Port 465 hoặc `ssl=true` dùng implicit TLS; port khác STARTTLS mặc định; SMTP thường chỉ khi cấu hình `starttls=false` rõ ràng. Giữ helper timeout 60 giây cho evidence, 30 giây cho mail thường.
- Giữ `send_message`, logo CID + plain text fallback, lựa chọn proxy theo account, lỗi lưu Sent copy tách khỏi SMTP success.
- Thay account/signature/logo/recipient/evidence làm preview cũ hết hiệu lực; worker đang chạy giữ account scope snapshot và kiểm tra trạng thái account trước mỗi side effect. Không đưa password vào job payload hoặc snapshot.

## 9. Cache theo user/ngày và phục hồi UI

Thiết kế key có version và owner, ví dụ:

```text
domaincheck:v1:u:{user_id}:quick-report:{business_day}:{input_hash}:{config_version}
domaincheck:v1:u:{user_id}:recipient:{business_day}:{lookup_key}:{resolver_version}
domaincheck:v1:u:{user_id}:link-status:{input_hash}:{engine_version}
domaincheck:v1:u:{user_id}:ui:{feature}:{business_day}
```

Không đưa URL chứa token, địa chỉ mail hay credential nguyên bản vào key/log. Dùng canonical input/hash thích hợp; mỗi workflow giữ canonicalizer của nó và có test trước khi hợp nhất.

- Cache lookup theo ngày hết hạn tại nửa đêm tiếp theo trong timezone được chọn, không mặc định `86400` giây từ lúc check. Khi đọc vẫn kiểm tra `business_day`, không chỉ trông chờ TTL.
- Khi user đổi timezone, áp dụng cho operation mới; case/job đang chạy giữ timezone/day đã chốt, không sửa lại identity cũ.
- Cache no-email/recipient không thay cho lần check cloaking mới. Force recheck bỏ lookup cache theo đúng scope nhưng không bỏ sent dedupe hoặc approval.
- Cache kết quả điều tra cần có engine/config version nếu phụ thuộc cấu hình. Không dùng cache của user A cho B ngay cả khi cùng URL.
- Quick Report có thể được nâng lên cache ngày theo yêu cầu mới; đây là thay đổi vòng đời so với file `seen` không có ngày hiện tại. Ghi rõ thay đổi này, migrate có chủ đích và test chuyển ngày; không áp TTL mới lên lịch sử gửi.
- Snapshot form/review cần phục hồi qua F5 lưu vào DB khi cần bền vững; Redis chỉ là bản tăng tốc. Pending future Python không serialize vào Redis: lưu operation/check ID và đọc tiến độ từ server.
- Clear cache chỉ xóa cache/editable snapshot của user/feature/ngày đang chọn. Không xóa `SENT`, `PARTIAL`, `SUBMITTED`, `ALREADY_SUBMITTED`, `UNKNOWN`, delivery attempts hoặc evidence audit.
- FE query key chứa identity/scope; logout đổi user phải clear query cache, draft state, blob URLs và subscriptions. Request cũ trả về sau logout không được đổ dữ liệu vào phiên mới.

## 10. Worker, gửi mail và xử lý concurrent actions

### 10.1. Job bền vững

DB là nguồn công việc. Với PostgreSQL, worker claim bằng transaction ngắn và row lock/lease; có heartbeat, lease owner token và optimistic version. Không giữ transaction mở khi HTTP/capture/SMTP chạy. Redis chỉ đánh thức worker hoặc cache progress, worker vẫn tìm được job nếu mất notification.

Giữ state machine nghiệp vụ cũ; lớp job API có thể chuẩn hóa envelope nhưng phải trả được stage và state gốc. Có checkpoint từng URL/delivery, input order cố định, retry subset chính xác và stop cooperative. Đừng biến `success` của pipeline thành `sent` SMTP.

Giới hạn concurrency theo user và toàn hệ thống; không để một user chiếm mọi Chromium/SMTP slot. Browser context/job riêng, cleanup sau timeout. Kết quả UI không phụ thuộc thứ tự thread kết thúc.

### 10.2. Exact preview và send

Luồng chuẩn cho các page review:

```text
select case → chọn account/recipient/evidence → tạo preview server-side
→ xem từng delivery → xác nhận gắn với revision hiện tại
→ POST send(preview_id, revision, confirmation, idempotency_key)
→ authorize + ownership + claim + fingerprint check
→ SMTP đúng plan → checkpoint từng delivery → trả SENT/PARTIAL/FAILED
```

- Preview gắn owner, actor, target URL, disposition, account/config version, recipient, subject/body, attachment SHA-256, manifest và policy version.
- FE chỉnh body/subject/recipient phải tạo revision server mới trước khi xác nhận; không tin body/hash do client tự khai là “đã duyệt”.
- Revalidate artifact và authorization tại send boundary. Sau khi validate, đọc immutable bytes/file handle ổn định để tránh file bị thay giữa validation và SMTP.
- Cloaking Review và Domain Evidence Review tiếp tục gửi trực tiếp qua helper dùng chung, không enqueue worker job gửi review mới. Có thể chạy hàm sync trong thread pool của API để không chặn event loop.
- Mất kết nối browser không làm người dùng bấm lại gây gửi trùng; API lần sau đọc operation/ledger. Thiết lập timeout reverse proxy phù hợp và cho UI tra cứu operation đang chạy.
- Domain Worker auto-send sau xác nhận vẫn tạo draft ở worker; route preview từ preflight chỉ được ghi là **delivery dự kiến**, không gọi là exact draft.

### 10.3. Không hứa exactly-once cho SMTP

DB transaction không bao trùm máy chủ SMTP. Nếu SMTP đã nhận thư nhưng process chết trước checkpoint, có cửa sổ kết quả chưa rõ. Giữ Message-ID ổn định cho attempt, lưu intent trước side effect; đánh dấu uncertain attempt khi phục hồi, không tự coi là failed rồi retry mù.

Giữ tối đa một retry lỗi kết nối tạm thời theo helper hiện tại và cùng Message-ID; không thêm retry tầng worker quanh lỗi auth/sender/recipient. Các tình huống bất định sau gửi cần reconciliation explicit bằng metadata có thẩm quyền; không tự đọc IMAP để đối soát khi chưa có thao tác được cấp quyền. Message-ID không đảm bảo provider loại trùng.

Case lock/delivery claim có phạm vi nhỏ theo owner + resource; không khóa cả hệ thống. Lease hết hạn không cho worker mới gửi lại một delivery đang có attempt bất định. Nếu DB không ghi được intent/claim, không bắt đầu side effect mới. Nếu SMTP đã thành công mà audit phụ lỗi, giữ success; nếu checkpoint chính lỗi, dừng các delivery tiếp theo và đánh dấu cần phục hồi, không xóa dấu vết.

## 11. Hợp đồng API

Prefix `/api/v1`; schema OpenAPI là nguồn tạo TypeScript client. Không trả raw `cfg`, local path, stack trace hoặc `ProviderMail` object tùy ý. Dùng DTO allowlist; pagination/cursor cho danh sách lớn. Lỗi chuẩn: `code`, `message`, `field_errors`, `request_id`, không secret.

| Nhóm | Endpoint dự kiến |
|---|---|
| Auth | `GET /auth/telegram/start`, `GET /auth/telegram/callback`, `GET /me`, `POST /auth/logout` |
| Users | `GET /users`, `PATCH /users/{id}/status`, `PUT /users/{id}/roles`, `POST /users/{id}/revoke-sessions` |
| Roles | `GET/POST /roles`, `PATCH/DELETE /roles/{id}`, `GET /permissions`, `PUT /roles/{id}/permissions` |
| Mail config | `GET/POST /mail-accounts`, `GET/PATCH/DELETE /mail-accounts/{id}`, `POST /mail-accounts/{id}/test-smtp`, `/test-imap` |
| User config | `GET/PATCH /settings/profile`, `GET/PATCH /settings/integrations`, upload logo qua artifact API |
| Parse | `POST /targets/parse` với workflow parser explicit; chỉ parse, không network |
| Quick Report | `POST /quick-reports/checks`, `GET /quick-reports/checks/{id}`, `POST /quick-reports/{id}/form-tasks`, `/netcraft-preview`, `/netcraft-submit`, `/export` |
| Check Domain | `POST /domain-checks`, `GET /domain-checks/{id}`, `POST /domain-checks/{id}/evidence/capture`, `/evidence/upload`, `/previews` |
| Domain Worker | `POST /domain-jobs/precheck`, `GET /domain-jobs/{id}`, `POST /domain-jobs/{id}/start`, `/stop`, `/retry` |
| Cloaking | `GET /cloaking-cases`, `GET /cloaking-cases/{id}`, `POST /cloaking-cases/{id}/evidence`, `/previews`, `/skip` |
| Evidence Review | `GET /evidence-review-cases`, `POST /evidence-review-cases/{id}/evidence`, `/previews` |
| Preview/send chung | `GET /previews/{id}`, `POST /previews/{id}/revisions`, `POST /previews/{id}/send`, `GET /send-operations/{id}` |
| Cloudflare | `POST /cloudflare/prechecks`, `GET /cloudflare/jobs/{id}`, `POST /cloudflare/jobs`, `POST /cloudflare/jobs/{id}/resume`, `/stop`, `/retry`, `/session` |
| Link Status | `POST /link-checks`, `GET /link-checks/{id}`, `GET /link-checks/{id}/export` |
| Artifact/cache | `GET /artifacts/{id}`, `POST /cache/clear` với feature/day allowlist |
| Giai đoạn sau | `/provider-replies/*`, `/lead-triage/*` theo cùng ownership/authz rules |

Các suffix ngắn trong bảng là route con của resource trước đó. Tách payload/schema cho từng action; không tạo generic endpoint nhận tên hàm Python rồi gọi động.

Operation lâu trả `202` + ID và endpoint đọc tiến độ. Check Link Status có thể dùng operation kỹ thuật để vượt timeout HTTP, nhưng giữ hành vi check toàn danh sách một lượt, không biến thành Domain Worker hay yêu cầu cấu hình gửi/batch. POST send review trực tiếp có operation receipt cho việc reconnect, không là worker queue gửi lại.

Tất cả endpoint artifact, export, retry, job status, Cookie session và form task phải authorize. Dùng `404` cho resource ngoài owner khi phù hợp để không lộ tồn tại; thiếu permission action trả `403`. Request trùng hợp lệ trả kết quả cũ; key trùng payload khác hoặc preview stale trả `409`.

## 12. Thiết kế giao diện React

### 12.1. App shell và phong cách

- Sidebar có nhóm **Điều tra & báo cáo**, **Duyệt evidence**, **Cấu hình**, **Quản trị**. Giữ thứ tự bảy menu ưu tiên theo yêu cầu; role chỉ thấy menu được phép.
- Mặc định landing tại Quick Report hoặc page đầu tiên được phép; không dựng thêm dashboard analytics thay cho hai menu thống kê đã bỏ.
- Top bar gọn: tên page, ngày/timezone, trạng thái kết nối, user menu. Không bày token/config nội bộ vào màn hình nghiệp vụ.
- Nền neutral/slate, primary blue/indigo, typography dễ đọc, spacing nhất quán; bảng dày thông tin nhưng không chật. Dark/light mode lưu preference; URL/code dùng monospace có wrap/copy.
- Desktop có sidebar khoảng 240 px, khu nội dung linh hoạt; tablet dùng sidebar thu gọn; màn nhỏ dùng drawer và detail panel full width. Tránh bảng bị cắt nút action.
- Status dùng cả chữ/icon/màu; loading skeleton, empty state và lỗi có action khôi phục. Tooltip giải thích trạng thái vô hiệu hóa.
- Evidence gallery có thumbnail, mở lớn, URL/source/profile, manifest/hash summary; không tự mở URL nghi ngờ khi hover hoặc render card.
- Không dùng KPI/card trang trí lặp thông tin, đặc biệt Cloaking Review. Một nguồn progress cho một job.
- Form giữ input qua polling, lỗi và chuyển menu; selection theo ID, không index. Cần stale-response guard khi đổi case nhanh.
- Bàn phím, focus ring, label, aria, dialog focus trap và contrast phải dùng được. Kiểm tra desktop 1440/1024 px và màn nhỏ khoảng 390 px.

### 12.2. Component dùng chung

`AppShell`, `PermissionGate`, `RawTargetInput`, `TargetTable`, `JobProgress`, `AccountSelector`, `EvidenceGallery`, `EvidenceUploader`, `ReportPreview`, `DeliveryLedger`, `ConfirmSendDialog`, `CopyButton`, `ErrorNotice`.

`PermissionGate` không là lớp bảo mật duy nhất. `ReportPreview` phân biệt plain text exact, HTML signature và bản dịch để đọc; UI xác nhận ghi rõ account/recipient/attachment count. “Gửi tất cả” chỉ bao gồm scope đã preview/được phép, không tất cả account toàn hệ thống.

Mở link ngoài qua thao tác người dùng với `noopener/noreferrer`. Render dữ liệu website/email như text hoặc HTML đã sanitize; không nhúng HTML nghi phishing trực tiếp vào origin của ứng dụng.

## 13. Chuyển từng menu và tiêu chí parity

### 13.1. Quick Report — ưu tiên 1

- Hai bước: lọc nội dung thô → kiểm tra danh sách. Giữ full URL/path/query, phát hiện input lỗi và duplicate theo parser tương ứng; ghi rõ khác biệt giữa parser/filter và batch dedupe cũ trước khi thống nhất.
- Gọi lại `run_cdn_check()` và resolver nhẹ. Hiển thị CDN/registrar/registry, email báo cáo hợp lệ, chi tiết cloaking thu gọn; Playwright verify chỉ khi người dùng bấm.
- Giữ GSB, Microsoft, Cloudflare, CDN/provider forms, Registrar/Registry, Chống Lừa Đảo, Cốc Cốc, Netcraft, copy-ready draft và export nic.top Excel khi áp dụng.
- Quick Report không bị ép chạy Domain Worker precheck, dò origin hoặc capture Browser Evidence chỉ để lấy email/routing form.
- Mỗi URL có khối kết quả/actions; click form không re-run cả batch; polling dừng khi xong. F5 đọc operation đã có, không tự tạo check mới.
- Quick Report form task luôn `fill_only`; Netcraft là action submit riêng có quyền và xác nhận, không chạy theo thao tác mở card.
- Không đưa URLScan trở lại bất kỳ nút/link/draft/attachment nào.

### 13.2. Check Domain — ưu tiên 2

- Một form URL; optional submit VT hiện rõ, mặc định tắt, quyền riêng. Không thay `run_check()` bằng bản rút gọn.
- Hiển thị WHOIS, SSL, VT, Safe Browsing, HTTP/redirect, tuổi domain, MX, CDN/origin candidate và reputation. Lỗi nguồn nào hiển thị tại nguồn đó.
- Chuyển nguyên panel cloaking và các route báo cáo; giữ các action Wayback hiện có, không tự gọi khi mở page.
- Browser Evidence hỗ trợ Passive DOM và Mở URL từ DOM; fallback upload 1–3 PNG/JPEG. Không khôi phục real click dựa trên tên alias legacy.
- Giữ manifest đúng full URL, preview ảnh tức thì và dùng cùng artifact cho gửi đơn/gửi tất cả. Gate đầy đủ Subject/recipient/Reported URL/hash/attachment.
- Draft theo từng channel có preview, download và send trong workflow. Việc bỏ menu Report Drafts không loại các chức năng này.

### 13.3. Cloudflare Worker — ưu tiên 3

- Giữ adapter Cookie Dashboard của page hiện tại. Dùng parser annotation/redirect-chain chung và kiểm tra Cloudflare trước khi cấu hình gửi.
- Trước precheck chỉ hiện input/action check. Sau precheck hiển thị cấu hình Cookie, delay 0–300 giây, preview nội dung theo URL và xác nhận gửi.
- Một bảng kết quả, thứ tự input cố định; dòng hiện tại được đánh dấu tại chỗ. Bảng loại mặc định ẩn, chỉ chứa record bị loại khi precheck; terminal record trong active job không biến mất.
- Start, stop, resume và retry failed; record membership lưu bền vững. Dedupe theo owner/ngày/full URL; version/hash report vẫn được giữ.
- `WAITING_FOR_SESSION` khi auth lỗi; `PAUSED` khi rate limit/challenge; response mất thành `UNKNOWN`; provider dedupe thành `ALREADY_SUBMITTED`. Không tự retry UNKNOWN hoặc terminal.
- Cookie chỉ ở RAM, không DB/Redis/queue/log/error/APM/browser localStorage. Xóa ô Cookie sau truyền, không trả lại plaintext từ API.
- Session worker có affinity theo owner/job; truyền secret qua kênh nội bộ authenticated, không broker persist. API chỉ nhận session ID/worker routing metadata sau handoff; body logging phải bị tắt/redact tại route này.
- Restart session worker mất Cookie: job yêu cầu nhập phiên mới, không lưu Cookie để “resume tự động”. Stop chờ request đang gửi kết thúc rồi checkpoint.
- Không âm thầm đổi Dashboard flow thành API-token flow hoặc headless submit. Hành vi provider endpoint phải kiểm thử bằng response fixture, không submit thật để nghiệm thu code.

### 13.4. Domain Worker — ưu tiên 4

- Một ô nhập thô; nhiều sender account của user; invalid token không chặn URL hợp lệ. Force recheck không bỏ sent dedupe.
- Precheck v4: recipient lookup và HTTP cloaking song song từng full URL; verify browser theo điều kiện cũ; ghi kết quả tăng dần.
- Có recipient + cloaking cần duyệt → enqueue Cloaking Review ngay; không email → `excluded_no_email`, không đưa vào review queue.
- Domain thường → DOM destination rồi passive fallback; capture lỗi → `evidence_review`; có evidence hợp lệ → `ready`. Giữ ngoại lệ terminal source đã có: không nhận ảnh terminal là content evidence, không tự biến terminal thành cloaking.
- Chỉ `ready` vào batch gửi thường. Khu “Cần bạn xử lý” liên kết đúng review; Domain Worker không có uploader inline.
- Form cấu hình batch/delay/account/VNCERT theo code cũ; xác nhận auto-send mặc định tắt. Preview route là delivery dự kiến.
- Bố cục progress → cấu hình gửi → bảng kết quả; một cột trạng thái account liệt kê tất cả account, recipient gửi thành công, stage/lỗi.
- Stop/resume/retry nhiều lần không tăng sai số URL, không gửi lại delivery đã hoàn tất. Job ngày cũ giữ audit nhưng không auto-restore như active job hôm nay.

### 13.5. Cloaking Review — ưu tiên 5

- Một bảng case hôm nay; action theo stable queue ID. Không KPI hoặc bộ lọc trạng thái riêng. `SENT` không action; `FAILED` retry, `PARTIAL` gửi tiếp.
- Dedupe cùng owner/ngày/full URL; giữ source-job history và required sender scope. Không migrate/show case không recipient.
- Ba disposition: xác nhận cloaking, không cloaking, bỏ qua. Chỉ thao tác case đang chọn.
- Xác nhận cloaking dùng evidence đã duyệt dù detector check lại không tái hiện; cần manifest và hai ảnh hợp lệ. Auto selection tối đa hai ảnh đại diện; upload operator nhận 2–4 ảnh theo validator hiện có, mỗi ảnh tối đa 10 MB.
- Upload hiển thị thumbnail ngay; validate cả batch; commit evidence cùng tạo draft, không thêm nút lưu ảnh riêng. Chưa đủ ảnh khóa tạo draft xác nhận.
- `not_cloaking` loại evidence/attachment cloaking rồi tạo Browser Evidence thường riêng, nguồn–đích/passive/manual 1–3 ảnh theo gate cũ.
- Exact preview theo account/recipient; fingerprint đổi thì khóa send. Gửi trực tiếp và checkpoint từng delivery; chỉ SENT khi đủ account thuộc scope nguồn.

### 13.6. Domain Evidence Review — ưu tiên 6

- Gộp case `evidence_review` từ mọi job của user trong ngày theo full URL. Không đưa vào batch tự gửi.
- Upload 1–3 PNG/JPEG, preview thumbnail tức thì, chọn sender accounts, tạo dry-run exact plan.
- Cho gửi trực tiếp khi Domain Worker vẫn chạy vì case đã tách khỏi ready; claim theo owner/URL ngăn hai tab/worker gửi cùng case.
- Giữ evidence khi gửi lỗi; retry chỉ delivery thiếu. Thành công cập nhật mọi source-job record trùng của cùng owner để case không xuất hiện lại.

### 13.7. Check Link Status — ưu tiên 7

- Nhận nội dung thô có heading brand/nhóm; giữ mapping nhóm và input order. Gọi lại `check_links()`, `should_show_result()`, `format_result_note()`.
- Giữ `LIVE`, `BLOCKED`, `DIE`, `GEO-BLOCK`, `TEMP ERROR`, `UNREACHABLE`, `UNKNOWN`; giữ redirect chain và WHOIS Hold.
- 403/Cloudflare warning là BLOCKED, không phải bằng chứng domain bị thu hồi; 5xx là TEMP ERROR; timeout/DNS không tự đổi thành DIE.
- Giữ hai nhóm export: die/blocked/unreachable/geoblock và redirect 301/302, đầy đủ URL và nhãn nhóm. Không tự tạo report/gửi mail.

### 13.8. Các menu giữ lại sau nhóm ưu tiên

**Phản hồi NCC:** sync explicit theo user/account/date; giữ Inbox, Junk, folder evidence và Cloudflare với tên/source mailbox chính xác; Unicode IMAP; lỗi folder riêng; lọc thư cần phản hồi, Seen all theo mailbox/UID, draft Anh–Việt, DOM evidence, reply `In-Reply-To`/`References`. Không mất workflow này chỉ vì bỏ analytics. Không tự sync khi mở page. Migration auth/network timeout khác biệt legacy phải ghi riêng, không che dưới refactor.

**Xử lý đầu mối:** parse explicit; target khác backend context; per-row và batch check; giữ kết quả/cache nhưng chỉ hiển thị scope vừa chọn; DNS/RDAP và backend chưa liên kết phải có caveat; recipient theo Worker; exact report Anh và bản Việt để đọc; normal send cần DOM source/destination hợp lệ theo gate hiện có; nhánh cloaking dùng shared review sender; giữ module 24 behavior và sent-target lock. Không nhân bản detector/sender chỉ để làm page React.

**Related Domains/Brand Scan:** bảo toàn core và CLI, giữ feature flag ẩn. Homepage chỉ điều hướng/điểm vào check được phép, không phục hồi Case Log dưới tên khác.

## 14. Extension trên máy operator khi BE ở server

Đây là hạng mục bắt buộc cho Quick Report parity. Server không thể gọi `open_in_installed_chrome()` để mở Chrome/profile của máy người dùng. `127.0.0.1` trên server và trên máy operator là hai máy khác nhau.

Chọn transport mới cho extension gọi HTTPS API của BE, giữ adapter provider hiện có. FE tạo task sau action explicit; extension nhận task qua messaging giữa origin ứng dụng allowlisted và extension được cấu hình. Task capability token ngắn hạn được bind owner, provider, target và device/extension registration; exchange một lần lấy payload, nhận handle riêng để gửi status. Không đặt secret trong URL provider hoặc log.

- Backend xác minh owner/quyền `open_form`, whitelist provider URL/fields và `mode=fill_only` cho Quick Report.
- Extension service worker chỉ gọi API origin cấu hình, không arbitrary URL proxy; content script chỉ hoạt động trên origin form đã whitelist.
- Chuẩn bị manifest/permission và page kết nối extension có kiểm tra phiên bản, installed/disconnected status. Không xin `<all_urls>`.
- Task expired, wrong origin, wrong user, replay hoặc provider mismatch phải fail closed. Refresh popup chỉ đọc status, không consume task hoặc submit lại.
- Giữ tám adapter, coordinator/selector tách biệt, popup checklist/recheck/refill và badge theo tab. Không CAPTCHA bypass, không thu cookie/HTML/password browser.
- Chưa có extension thì cho mở link form và copy draft thủ công với thông báo rõ; đây là fallback, không được báo đã tự điền khi chưa có acknowledgement.
- Local bridge cũ có thể giữ cho desktop legacy, nhưng không phơi localhost HTTP server ra Internet hoặc sao chép CORS `*` sang API authenticated.
- Nếu chọn local companion thay transport HTTPS, phải bàn giao cả companion, pairing, origin validation và hướng dẫn cài; không bỏ phần này thành TODO.

## 15. Những invariant nghiệp vụ không được đổi

1. CLI/API/UI gọi cùng core. Không viết lại detector, recipient resolver hoặc formatter bằng TypeScript.
2. Giữ HTTP đa profile trước Playwright; detector browser thụ động. Không tự gửi `LIKELY`, `POSSIBLE`, `INCONCLUSIVE` hoặc coverage gap qua normal batch.
3. Path probe như `/vi-vn/` chỉ khám phá; content verdict khác cloaking verdict. Terminal page không là chênh lệch cloaking.
4. Không coi IP/ASN Cloudflare là origin đã xác minh; chặn `abuse@cloudflare.com` và recipient privacy/sai route tại ranh giới gửi, kể cả dữ liệu legacy.
5. Browser Evidence phân biệt requested URL, landing, HTTP redirect và href DOM. DOM-open không được mô tả là click thật. Chỉ giữ aggregate field indicators, không đọc credential values.
6. Report nhà cung cấp dùng tiếng Anh; bản Việt chỉ để đối chiếu. Không đưa label UI tiếng Việt vào external formatter hoặc cáo buộc OTP/payment/credential theft khi không có chứng cứ.
7. Giữ full URL/path, signature đúng account và logo CID; khối evidence/Reported URL nằm trước chữ ký. Không đưa local filesystem path ra email.
8. Không khôi phục URLScan. Giữ scrub/gate cho draft legacy có dấu vết dịch vụ này.
9. Clear cache/đổi ngày/refresh không xóa delivery truth và không tự check/gửi/approve.
10. Gửi SMTP/submit report/IMAP/capture chỉ sau action explicit có quyền; background job chỉ tiếp tục trong scope đã xác nhận. Không mặc định mọi user đều có quyền external action.
11. Lỗi log phụ/provider riêng không phá toàn bộ check; nhưng thiếu durable claim/ledger trước send không được mặc định cho gửi để “cố hoàn tất”.
12. Không gom mọi trạng thái khác thành `success/error`. Giữ PARTIAL, UNKNOWN, terminal, no-email và review distinction đủ để vận hành an toàn.

## 16. Bảo vệ backend web nhiều user

Các yêu cầu dưới đây là phần bổ sung cần thiết khi chuyển công cụ local thành web, không phải thay thuật toán điều tra:

- Kiểm tra HTTP(S) target và redirect destination. Egress của browser/probe phải chặn loopback, private/link-local, cloud metadata và địa chỉ nội bộ, gồm IPv6, redirect và DNS rebinding; áp dụng cho browser subresources, DNS/socket probe và proxy route thích hợp. Trả lỗi policy rõ, không đổi thành verdict phishing.
- Worker capture không có quyền đọc credential store hoặc network quản trị; không dùng browser profile cá nhân trên server. Tách nhiệm vụ capture và gửi mail ở mức quyền/runtime cần thiết.
- SMTP/IMAP/proxy endpoint do user cấu hình có policy egress riêng; deployment admin mới cấu hình allowlist server mail nội bộ khi cần. Không để menu mail trở thành arbitrary TCP proxy.
- Timeouts, hạn mức URL/batch/upload/capture, rate limit theo user, pagination và kiểm soát concurrency. Không gọi shell bằng string ghép input user, đặc biệt dnstwist/export/launcher.
- Không phục vụ `data/`, draft folder hoặc `.env` bằng static server. Secrets/DSN/Cookie không vào exception trace trả FE, metrics labels hay request log.
- CORS allowlist khi dev; production FE/API cùng origin qua HTTPS. CSP phù hợp Telegram flow và app, không disable CSP toàn cục để chạy extension.
- Backup database và artifact cùng metadata tham chiếu; test restore trên môi trường tách biệt. Có retention policy, không tự purge audit/evidence trong cache cleanup.

## 17. Migration dữ liệu và cách giữ tương thích

Không import/xóa/sửa runtime thật ngay khi deploy API hoặc mở page. Viết công cụ migration có `--dry-run`, chọn source root, mapping owner, checkpoint và báo cáo số lượng; chỉ chạy apply với dữ liệu thật khi người vận hành yêu cầu.

| Nguồn legacy | Đích / xử lý |
|---|---|
| `config.ini` | Công cụ import cục bộ riêng, owner explicit, encrypt; không endpoint upload toàn config hoặc in ra terminal |
| `data/worker_jobs/` | jobs/items/preflight/checkpoints; retain legacy ID và source metadata |
| `data/cloaking_review/` | review_cases + case_sources + deliveries; consolidate theo đúng ngày/full URL |
| `data/cloaking_send_jobs/`, `QUEUED_*` | Chỉ migrate/sync lịch sử; không phát sinh job review kiểu cũ |
| `data/sent_log.csv` và fallback legacy | delivery metadata; preserve Message-ID/status/account/channel; map sender thuộc owner |
| `data/no_email_log.csv`, `domain_precheck_cache.json` | Lookup/no-email cache phù hợp ngày/schema; không dùng làm approval |
| `cloudflare_form_worker.json`, `cloudflare_form_job.json`, `cloudflare_form_input.json` | Ledger/job/input; giữ SUBMITTED/ALREADY_SUBMITTED/UNKNOWN và membership |
| `quick_report_filter_cache.json` | Import seen theo policy được chọn; file không có ngày thì không tự bịa timestamp |
| `lead_triage_cache.json` | Review snapshot; evidence references giữ nguyên, backend cache khôi phục phải revalidate theo policy |
| `provider_mail_cache.json`, `provider_reply_log.json` | Khi port Provider Replies: owner/account/mailbox mapping, chỉ field cần thiết, không mở IMAP |
| `case_log.csv`, `reports/`, evidence folders | Archive/import artifact tham chiếu; không bỏ dữ liệu vì menu đã bị gỡ |
| Analytics caches cũ | Giữ nguyên/archive; không auto sync hoặc tạo menu analytics mới |

Repository có `_runtime_path()` fallback giữa `data/` và root. Migration phải dùng cùng precedence hoặc phát hiện cả hai và reconcile; không gộp mù hai nguồn rồi nhân đôi delivery.

- Dữ liệu cũ không có user ID: tạo mapping explicit cho một legacy owner được chỉ định; record không xác định owner/account vào quarantine/report import, không hiện cho tất cả user.
- Không import credential vào job JSON; không copy `config.ini` thật vào Docker image, Git hoặc artifact bàn giao.
- Dry-run không ghi source. Apply idempotent bằng source ID/fingerprint + migration version; chạy hai lần không tạo trùng hoặc reset SENT.
- Validate manifest/hash, path within allowed source root; không sửa artifact nguồn. Không lấy timestamp file làm ngày case nếu không có cơ sở, trừ migration legacy đã có quy tắc rõ và được ghi nhận.
- Khi cutover, dừng writer legacy trong scope di chuyển và chuyển sang một nguồn ghi chính. Không để web DB và desktop CSV gửi độc lập cùng một scope mà không dùng chung dedupe authority.
- Rollback sau khi hệ thống mới đã gửi thật phải giữ/reconcile ledger mới; không chỉ quay về CSV snapshot cũ khiến gửi lại. Mọi thao tác đối soát bên ngoài cần được cấp quyền.
- Giai đoạn đầu CLI compatibility có thể dùng filesystem adapter riêng; ghi rõ dữ liệu chưa hợp nhất. Chế độ web multiuser không được chạy bằng global file adapter cũ.

## 18. Thứ tự triển khai và đầu ra mỗi giai đoạn

### 18.1. Bắt buộc lập plan triển khai trước khi viết code

Sau khi đọc checkout và các chỉ dẫn, tạo `docs/implementation-plan.md` làm kế hoạch thực thi. File prompt này là đặc tả đầu vào duy nhất cần giao; plan và các tài liệu kiểm chứng là đầu ra được coding agent tạo trong đợt triển khai, không phải file người dùng phải chuẩn bị sẵn.

Trước khi sửa code, plan phải có:

1. **Hiện trạng đã xác minh:** entrypoints, core functions, UI actions, nơi lưu dữ liệu/global mutable state, test baseline hoặc bước đo baseline cụ thể; phân biệt facts đã đọc với giả định cần kiểm tra.
2. **Phạm vi và quyết định:** React/Vite/TypeScript/Tailwind, Python/FastAPI, PostgreSQL + Redis, 24 bảng nền tảng, Telegram login, RBAC, owner scope, bốn menu bỏ và các menu giữ. Không mở lại lựa chọn database đã chốt.
3. **Ma trận truy vết yêu cầu:** mỗi yêu cầu/page/action/invariant của prompt ánh xạ tới task ID, nguồn legacy, đầu ra mới và test nghiệm thu. Ghi rõ chưa làm/thực hiện/đã kiểm chứng; không bỏ Provider Replies/Lead Triage chỉ vì ưu tiên thấp hơn.
4. **Schema và API contract:** bảng, khóa, owner constraints, indexes, migration order, DTO, endpoint/permission matrix; nơi lưu preview body, idempotency receipt, form task và integration secrets. Các cơ chế cần persist phải ánh xạ vào bảng đã chọn hoặc giải thích bảng bổ sung, không chỉ nêu tên khái niệm.
5. **Work breakdown:** task đủ nhỏ để triển khai và kiểm thử độc lập; mỗi task có ID, mục tiêu, file/module ảnh hưởng, phụ thuộc, đầu ra, test, tiêu chí done, rủi ro và cách rollback. Ước lượng nếu có phải nêu giả định, không cam kết thời gian khi chưa đo.
6. **Dependency và release gate:** chỉ rõ phần storage/auth/ownership phải xong trước API multiuser; các review gate phải xong trước release auto-send. Thứ tự ưu tiên menu không loại bỏ việc xây hạ tầng dùng chung cần thiết sớm.
7. **Kế hoạch UI:** route, page layout, reusable components, loading/error/empty/partial states, phục hồi F5, permission UX, responsive và extension connection flow. Không chờ viết xong backend mới phát hiện page thiếu API.
8. **Kế hoạch dữ liệu và cutover:** legacy owner mapping, schema migrations, synthetic import rehearsal, dry-run/apply, source precedence, rollback preserving delivery truth; phân biệt dev DB với runtime thật.
9. **Kế hoạch test:** regression/parity, isolation A/B, authorization negative tests, concurrent send/stop/retry, Redis/process restart, evidence mutation, extension, FE E2E và tải nhiều user bằng mock/fixture.
10. **Triển khai và vận hành:** môi trường dev/test/production, secrets/Telegram callback placeholders, API/worker/session worker/artifact topology, healthcheck, limits, observability không lộ dữ liệu, backup/restore và cách chạy trên môi trường dự kiến.
11. **Giả định/câu hỏi còn mở:** ưu tiên xử lý bằng code/context đã có. Với tải dự kiến, tài nguyên server hoặc retention chưa được cung cấp, ghi giả định cấu hình được và kế hoạch đo; không biến thiếu thông tin không chặn thành lý do dừng mọi công việc.
12. **Theo dõi tiến độ:** task status, bằng chứng test, quyết định mới và bước tiếp theo; cập nhật cùng code để phiên làm việc sau tiếp tục được, không phải khảo sát lại từ đầu.

Mẫu dòng task:

| Task ID | Mục tiêu / đầu ra | Module dự kiến | Depends on | Kiểm thử / tiêu chí done | Rủi ro / rollback | Trạng thái |
|---|---|---|---|---|---|---|
| AUTH-01 | Telegram callback tạo session đúng user | API auth, identity/session repository | DB-01, SEC-01 | Mock callback hợp lệ/sai signature/replay; không tự cấp admin | Revoke session, tắt route lỗi | todo |
| ISO-01 | A/B cách ly check/job/account/artifact/cache | Repositories, API dependencies, worker context | DB-01, AUTH-01 | Cross-owner requests bị từ chối; clear/stop của A không đổi B | Chặn release multiuser khi fail | todo |

Đây là mẫu định dạng, không phải danh sách task đầy đủ và không phải bằng chứng đã làm. Plan thật phải xác định cả `DB-01`/`SEC-01` và mọi dependency được tham chiếu, không có task treo hoặc vòng phụ thuộc.

**Kiểm tra plan trước khi triển khai:** mỗi menu/action trong bảng parity có task; mỗi entity có ownership/retention; mỗi external side effect có permission/confirmation/idempotency/recovery; mỗi milestone có nghiệm thu. Các yêu cầu chưa được ánh xạ phải bổ sung vào plan trước khi đánh dấu plan hoàn chỉnh.

Sau khi hoàn thành plan, báo ngắn gọn quyết định, milestone đầu tiên và điểm chưa chắc chắn, rồi tiếp tục task đã được giao. Không tự dựng bước duyệt kế hoạch bắt buộc. Nếu người dùng yêu cầu plan-only thì bàn giao plan và dừng đúng phạm vi đó; không chạy migration/deploy/send thật chỉ vì plan có liệt kê chúng.

### 18.2. Khung milestone và phụ thuộc bắt buộc

| Milestone | Đầu ra kiểm chứng được | Phụ thuộc / điều kiện qua mốc |
|---|---|---|
| M0 | Plan chi tiết, parity/storage map, baseline, thiết kế schema/API | Hoàn tất bước 18.1 và khảo sát; PostgreSQL đã chốt |
| M1 | Auth/RBAC/user/mail settings, storage/context, job/operation primitives cần thiết | M0; isolation A/B phải đạt trước mở API multiuser |
| M2 | Quick Report và Check Domain React nối API thật, extension transport và evidence/preview | M1; parity tests và kiểm thử gửi bằng mock |
| M3 | Cloudflare Worker, session affinity, ledger/recovery | M1 + core adapter/recipient routing của M2; Cookie không persist |
| M4 | Domain Worker và hai review page với direct send/partial retry | M1 + evidence/preview của M2; đủ ba page trước release auto-send |
| M5 | Link Status, rồi Provider Replies/Lead Triage; bốn menu bỏ hoàn toàn | Hạ tầng M1/M2/M4 cần thiết; baseline semantics giữ nguyên |
| M6 | Rehearsal migration, restore/load/security tests, tài liệu và release candidate | Các milestone chức năng đạt; thao tác production theo phạm vi được cấp |

Khung trên là ràng buộc ưu tiên và release, không thay thế work breakdown. Tách M2/M4/M5 thành task theo từng menu để giữ thứ tự ưu tiên người dùng yêu cầu. Việc xây job runner, artifact storage, preview/claim chung được thực hiện sớm khi feature trước cần; không để Check Domain phụ thuộc vào một hạ tầng chỉ được viết ở cuối M4.

Trạng thái task dùng `todo`, `in_progress`, `blocked`, `done` trong tài liệu plan. `done` cần bằng chứng test/acceptance; `blocked` ghi đúng dependency, không ngầm bỏ task. Nếu bối cảnh thay đổi, cập nhật plan và decision log; không đổi behavior hoặc bỏ scope âm thầm. Mỗi lần bàn giao/tiếp tục phải đọc tiến độ đã có, không restart toàn bộ plan.

### 18.3. Nội dung từng giai đoạn

### Giai đoạn 0 — baseline và hợp đồng

Hoàn thành `docs/implementation-plan.md` theo mục 18.1. Lập `docs/feature-parity.md` theo page/action/core function; `docs/storage-map.md` cho mọi file/cache/global; ghi baseline tests theo môi trường và `docs/architecture-decisions.md` ghi nhận PostgreSQL đã chọn. Tạo fixture synthetic cho từng state quan trọng, không copy runtime/secret vào test. Ghi rõ những thay đổi có chủ đích: auth/RBAC, ownership, daily cache Quick Report, network boundary và menu removal.

### Giai đoạn 1 — nền tảng

Tạo frontend/backend packages, Compose, DB migrations, Redis, artifact abstraction, ExecutionContext, Telegram OIDC, session, RBAC, user/role pages, mail settings. Nghiệm thu bằng hai user khác nhau, không truy cập chéo, config secret không xuất hiện trong response/log.

### Giai đoạn 2 — Quick Report và Check Domain

Expose core qua service; React UI thật; check/evidence/preview/send mock parity; extension transport hoàn chỉnh và fallback manual. Không bỏ các provider action hoặc nic.top export. Giữ CLI bằng facade.

### Giai đoạn 3 — Cloudflare Worker

Durable ledger + worker affinity + RAM-only Cookie; UI input/precheck/config/table; start/resume/stop/retry; test auth/rate-limit/dedupe/response mất. Không đổi sang token API.

### Giai đoạn 4 — Domain Worker và hai review page

Hoàn thiện job runner/preflight v4, incremental review enqueue, Browser Evidence fallback, direct review send, delivery checkpoint và concurrent claims. Không release auto-send công khai nếu các case bị cách ly chưa có review page hoạt động.

### Giai đoạn 5 — Link Status và phần còn lại

Port Link Status đầy đủ; Provider Replies, Lead Triage sau bảy menu ưu tiên. Giữ Related/Brand Scan ẩn. Gỡ bốn menu yêu cầu khỏi router/navigation React và kiểm tra không còn link tới chúng.

### Giai đoạn 6 — migration, hardening và cutover

Rehearsal import bằng dữ liệu synthetic, restore test, failure/restart tests, user acceptance, tài liệu vận hành/dev và rollback. Chỉ cutover dữ liệu thật/deploy public/gửi thật khi có yêu cầu tương ứng. Không đánh dấu hoàn tất toàn bộ chỉ vì giai đoạn ưu tiên đã xong.

Mỗi giai đoạn bàn giao mã chạy được, test evidence, parity checklist, migration tương ứng và hạn chế còn lại; không hỏi lại các lựa chọn routine đã chốt trong prompt.

## 19. Kiểm thử và tiêu chí nghiệm thu

### 19.1. Regression và parity

- Chạy full `python -m unittest discover -s tests -v` trước refactor để đo baseline, rồi sau thay đổi core theo AGENTS. Không bịa baseline xanh từ số test trong tài liệu.
- Chạy `python -m compileall -q .`; `python -m pip check` khi đổi dependency/config/build. Test target dùng môi trường tách biệt, không đụng runtime thật.
- Preserve tests của SMTP transport/retry, report formatting, browser evidence hash/URL, terminal handling, precheck v4, dedupe, partial retry và Link Status.
- So kết quả legacy/new bằng fixture cùng clock/seed/config; bỏ khỏi so sánh các trường có chủ đích đổi như artifact ID/path, nhưng so subject/body exact và attachment bytes/hash khi cần.
- Không sửa expected verdict chỉ để test xanh. Các lỗi baseline phải ghi rõ tên test, platform, tác động và có test mới chứng minh luồng migration liên quan đạt.

### 19.2. Auth, RBAC và ownership

- Telegram signature/issuer/audience/expiry sai, state reuse, callback sai browser, code replay đều bị từ chối.
- Pending/disabled user không chạy nghiệp vụ; logout/session expiry/revoke có hiệu lực; callback không open redirect.
- User A không đọc/sửa/gửi/export/download/subscribe job, case, account, preview, evidence của B qua ID trực tiếp hoặc nested reference.
- Có page view nhưng thiếu send không gọi API gửi được; đổi role trong lúc job chạy ngăn side effect tiếp theo.
- Hai thao tác đồng thời không gỡ admin cuối cùng. Không cấp admin do username hoặc user đầu tiên.
- HTTP GET/config/audit/frontend bundle không có password/token/Cookie; secret PATCH mask không ghi đè password.
- A và B cùng full URL/ngày nhưng có target/check/job/review/delivery độc lập; filter duplicate và cache hit không tiết lộ việc user khác đã xử lý URL đó.
- Clear cache/stop/retry của A không thay dữ liệu B; B không được gắn sender account, preview hoặc artifact của A vào request/job của mình.
- Kiểm thử connection/thread reuse giữa request/job A rồi B, không chỉ hai request tuần tự trên hai client riêng. Nếu bật RLS, test trên runtime DB role thật để phát hiện bypass/context còn sót.
- Hai phiên của cùng một user khôi phục đúng dữ liệu đã lưu; cập nhật snapshot/preview stale phải báo conflict hoặc merge theo policy rõ, không làm mất approval scope.

### 19.3. Bền vững và concurrency

- Hai tab gửi cùng preview; hai worker claim cùng job; case review trong lúc worker đang chạy.
- Crash trước side effect, sau side effect trước checkpoint, sau một delivery trong nhiều account; không tự retry uncertain outcome.
- Redis restart/flush test instance không làm mất ledger/approval/artifact reference; cache clear không mở khóa case đã gửi.
- Restart Cloudflare session worker yêu cầu Cookie mới; UNKNOWN và ALREADY_SUBMITTED không bị retry.
- Đổi ngày/timezone, cùng host khác path/query, same URL khác user, same URL ở nhiều job; preserve workflow-specific canonicalization.
- Retry nhiều lần không double count; active job giữ record membership, Cloudflare input order không nhảy; lỗi log phụ không đổi SMTP success.
- Xóa/tắt sender đang được tham chiếu, đổi signature sau preview, thay evidence file, stale case selection đều có handling rõ.
- Kiểm thử nhiều user chạy check/job đồng thời bằng mock HTTP/SMTP/browser; một user đạt quota không làm user khác bị từ chối vì lock/global active-job của legacy. Hạn mức toàn hệ thống trả trạng thái queue/backpressure rõ.
- Plan phải định nghĩa workload, số user/URL, tài nguyên test và ngưỡng chấp nhận trước khi chạy bài tải. Báo số đo latency/queue wait/error rate theo user, không tuyên bố đáp ứng quy mô chưa đo và không phát sinh report thật trong load test.

### 19.4. Frontend/E2E

- Login/pending/admin activate; tạo role và gán permission; menu/nút cập nhật đúng.
- Tạo/sửa sender, mask password, test connection bằng mock, preview signature đúng account.
- Bảy menu ưu tiên có workflow thật qua API test, đủ empty/loading/error/partial/success states.
- F5/chuyển menu/quay lại phục hồi dữ liệu đúng user mà không check/send lại; đổi case không hiển thị ảnh/draft cũ; logout clear dữ liệu.
- User thao tác form trong lúc polling không bị mất input/focus; reconnect đọc được kết quả cũ.
- Extension handshake/expired task/wrong provider/replay/manual fallback; chỉ fill, không submit/CAPTCHA.
- Bốn menu bị bỏ không còn navigation/route trực tiếp, nhưng preview/draft generation và ledger nội bộ vẫn hoạt động.
- Build FE, typecheck, lint và component tests đạt; kiểm tra keyboard, responsive, URL dài và bảng nhiều dòng.

### 19.5. Hoàn thành toàn dự án khi

FE và BE chạy độc lập qua cấu hình mẫu; auth/RBAC/ownership được enforce server-side; các menu ưu tiên và hai menu còn giữ đã chuyển đầy đủ; logic core được kiểm chứng parity; migration dry-run/apply/rollback và backup/restore có test; không có credential hoặc dữ liệu thật trong artifact; tài liệu phản ánh đúng phần đã làm. Ma trận yêu cầu trong plan không còn task bắt buộc chưa nghiệm thu; isolation A/B và bài tải nhiều user đã chạy với kết quả/giới hạn được ghi lại. Những external integration chưa test live phải được nêu rõ, không tuyên bố đã xác minh production.

## 20. Tài liệu và cách bàn giao

Trong đợt triển khai phải cập nhật:

- `README.md`: setup FE/BE, Telegram callback, SMTP settings, chạy dev, các menu mới và removed menus.
- `CLAUDE.md`: module map, ExecutionContext, ownership, transaction/claim, secret handling, compatibility.
- `03_Technical_Guide.md` hoặc `plan_phishing_takedown.md`: cách dùng React, preview/approve, vận hành worker/retry và extension.
- `AGENTS.md`: kiến trúc/lệnh/test baseline mới, recent changes; sửa các mô tả đã không còn đúng sau migration.
- Skill nghiệp vụ: bổ sung invariant multiuser/auth/storage nếu workflow thay đổi; không sao chép toàn bộ AGENTS vào skill.
- `docs/implementation-plan.md`: task breakdown, dependency, status, requirement mapping, acceptance/test evidence và các bước tiếp theo; phải được lập trước implementation và cập nhật trong suốt dự án.
- `docs/feature-parity.md`, `docs/api.md`, `docs/architecture-decisions.md`, `docs/migration.md`, `docs/operations.md`: trạng thái từng chức năng, schema/API, quyết định DB, import/cutover/rollback, recovery.

Bàn giao nêu rõ: phần đã chạy được; database thực sự chọn; test đã chạy và kết quả; migration nào đã/chưa apply; các chức năng external mới kiểm bằng mock; phần chưa hoàn tất nếu có. Không dùng câu “giữ nguyên logic” làm thay cho bằng chứng parity.

**Bắt đầu bằng khảo sát checkout, lập plan chi tiết và baseline; sau đó triển khai nền tảng và các menu theo thứ tự trên, cập nhật plan sau từng task. PostgreSQL + Redis đã chốt; mọi dữ liệu nghiệp vụ thuộc user của tài khoản đăng nhập. Không chỉnh runtime thật hoặc gửi báo cáo thật để chứng minh tính năng.**
