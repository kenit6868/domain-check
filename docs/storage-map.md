# Storage và mutable-state map

Không đọc nội dung runtime trong `data/`; inventory này dựa trên code/path.

| Legacy source/state | Semantics | Vấn đề multiuser | Đích | Owner/retention/clear |
|---|---|---|---|---|
| `config.ini` | company/API/mail/proxy | global secret | settings/accounts encrypted | owner; import explicit; không API upload |
| Web `sender_accounts` secret | SMTP/IMAP/proxy credential | per owner + stable account ID | AES-GCM ciphertext/nonce/key version + field-presence metadata | API không trả plaintext/mask; một default/owner bằng partial unique index |
| `case_log.csv` | check/case history | shared path | checks/audit or archive mapping | owner; không cache-clear |
| `reports/` | draft files | shared names/path | private draft artifacts | owner+operation; retention policy |
| `sent_log.csv` | delivery truth/metadata | shared lock/account strings | deliveries/attempts | owner+stable account; immutable history |
| `worker_jobs/` | jobs/preflight/checkpoint | global active locks/path | jobs/job_items/checks | owner; durable; stop scoped |
| `domain_precheck_cache.json` | recipient/day/preflight | shared cache + approval risk | Redis lookup + DB checks/cases | owner/day/version; cache clear không approval |
| `no_email_log.csv` | no recipient history/cache | shared | checks sanitized result/cache | owner/workflow/day policy |
| `cloaking_review/` | queue/evidence/delivery | global queue/file locks | review/case sources/evidence/delivery | owner/day/full URL; audit retained |
| `cloaking_send_jobs/`, `QUEUED_*` | legacy review jobs | old flow | migration history only | owner mapped; no new writes |
| `evidence/browser`, `evidence/cloaking` | PNG/manifests | path/name collision/access | private artifact storage + rows | owner+operation; immutable |
| Cloudflare worker JSONs | input/job/ledger | global Cookie-adjacent operation | jobs/items/cloudflare records/snapshot | owner/day; terminal retained |
| Quick filter cache JSON | seen input, no day | shared/no expiry | Redis+optional snapshot | owner/day; legacy day unknown |
| Quick `st.cache_resource` | futures/results/executor | process global | DB operation + scoped cache | owner; futures not serialized |
| Web form task/device | legacy localhost token + URL fragment | server/operator boundary, replay/leak risk | owned `review_snapshots` task/device rows + extension session storage | DB giữ hash/status/expiry; raw capability one-time, không Redis/provider URL/localStorage |
| Provider mail cache/reply log | sanitized mail/reply status | account string not owner | later mailbox/message/thread tables | owner+account+mailbox; explicit sync |
| Lead Triage cache | review snapshot | one global file | review_snapshots/evidence refs | owner+context+revision; clear editable only |
| analytics caches | stats snapshots | removed UI, some helper deps | archive/owned snapshot if required | no new analytics route |
| `st.session_state` | current form/selection | browser process/session only | FE state + durable snapshot where needed | owner session; clear logout |
| global/default path arguments | implicit adapter | cannot swap per request | repository ports/context | no web call may depend on globals |
| global requests/browser/executors | pooled mutable runtime | identity leak/lock contention | shared pools with operation-local state | credentials/context never pooled mutable |
| Cloudflare Cookie | Dashboard auth | catastrophic if persisted/shared | session-worker RAM only | owner+job affinity; lost on restart |

Filesystem artifact adapter dùng key
`users/{owner_uuid}/evidence/{evidence_set_uuid}/{artifact_uuid}.{png|jpg}` do server
sinh. Tên client chỉ là metadata đã kiểm tra, không tham gia đường dẫn. Toàn bộ batch
được decode/kiểm MIME signature, byte limit, dimension và SHA-256 trước khi ghi vào
staging; lỗi commit xóa cả file đã move. Khi đọc, adapter resolve trong private root
và so lại size/hash. Thư mục này không được mount vào static frontend.

## New durable entity policy

Mọi owned row có owner, timestamps và version. Target/check/job/review/preview/
delivery/evidence/Cloudflare state là durable. Redis chỉ chứa dữ liệu tái tạo được,
OAuth state/rate limit/notification và UI acceleration. Draft exact nằm trong
private artifact storage; DB giữ revision/hash/reference. Clear cache không xóa
target, checks history, delivery, approval, evidence hay terminal Cloudflare row.

Exact preview dùng key server-generated
`users/{owner_uuid}/previews/{preview_uuid}/{artifact_uuid}.json`. JSON này chứa
subject/body HTML + plain text và delivery plan chính xác; không copy body vào
`preview_plans`, audit, job metadata hoặc log. DB giữ SHA-256, revision, expiry,
account/profile version và evidence references. Mỗi lần sửa tạo revision mới; file cũ
immutable để audit/retention, còn send boundary chỉ chấp nhận revision mới nhất đã duyệt.

Redis key mới có dạng
`domaincheck:v1:u:{owner}:{feature}:{business_day}:{bucket}:v{schema}:h:{sha256}`.
URL/email/input chỉ đi vào SHA-256, không xuất hiện nguyên văn trong key. Payload
cache lặp lại `business_day` và schema để kiểm tra khi đọc; TTL được tính tới nửa
đêm kế tiếp của timezone IANA, kể cả DST, không phải 86.400 giây cố định. Redis
down làm lookup miss/set bypass; clear explicit trả unavailable thay vì báo xóa giả.

