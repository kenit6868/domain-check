# Gỡ ứng dụng cũ, chỉ giữ BE/FE — 18/10/2026

Người dùng xác nhận bản web đã có các tính năng cần thiết và cho phép loại bỏ
những chức năng không có trong bản mới. Đợt này hoàn tất việc gỡ source ứng dụng
cũ; không di chuyển Streamlit sang thư mục khác để duy trì song song.

## Đã giữ và chuyển vào backend chính

7 module được bản web sử dụng nằm trong `backend/src/domaincheck/engine/`:
`phishing_toolkit`, `browser_evidence`, `cloaking_detector`, `provider_replies`,
`quick_report_actions`, `cloudflare_dashboard_session`, `cloudflare_abuse_api`.
API/application/core/worker và test import bằng namespace đầy đủ. Wheel chỉ đóng
gói `domaincheck`, không còn force-include module top-level hoặc thêm compat vào
dev path. Các adapter được đặt tên theo bản web thay cho facade Legacy*.

Giữ nguyên policy/engine identifier đã bind lịch sử, kể cả chuỗi `legacy-*`.
Không migration hay rewrite DB; owner/day/account scopes, profile/evidence/MIME
hash, intent/checkpoint/UNKNOWN, retry và Sent reconciliation giữ nguyên.

## Đã loại bỏ

- Toàn bộ Python entrypoint/wrapper ở root; Streamlit entry/home/pages/UI helpers,
  desktop launcher, theme và source-only skills chạy CLI cũ.
- `backend/compat/` và 13 module không được web dùng: CLI quick check, JSON worker,
  JSON cloaking queue/sender, local Chrome bridge, Lead Triage, link-status cũ,
  mail/report/general statistics legacy và tiện ích chỉ phục vụ chúng.
- `backend/legacy_tests`, runner legacy và test giao diện/entrypoint cũ. Giữ lại
  **50 test thuần Browser Evidence/cloaking** trong bộ backend hiện tại.
- PyInstaller/spec/build EXE, requirements desktop, root shortcut batch cũ và
  tài liệu vận hành chỉ phục vụ ứng dụng đã bỏ.
- Loader đọc config.ini, CLI check/related/brandscan/send, browser form opener
  desktop, duplicate SMTP sender, CSV logs và JSON mail cache/threaded sender
  không được ứng dụng mới sử dụng.
- Các hàm ghi draft ra thư mục global chỉ để đối chiếu bản cũ. Nội dung mẫu
  registrar/registry/hosting và HTML chữ ký được đóng băng thành fixture dummy,
  vẫn so sánh exact output trong test mà không phải giữ code cũ để chạy.
- Bridge localhost, token ptask/port và nhánh tự submit của extension desktop.
  Extension 3.1.0 chỉ nhận capability task từ exact Application origin và luôn
  fill_only; quyền Application origin localhost vẫn có thể cấp explicit khi dev.

`run_check` chỉ nhận config explicit và chạy nonpersistent; nếu yêu cầu file
draft/log hoặc capture tích hợp cũ sẽ bị từ chối. Browser Evidence được tạo qua
luồng staging/private artifact hiện tại. Parser/formatter phản hồi NCC tách khỏi
IMAP/SMTP application services. Import engine không tạo/migrate dữ liệu.

## Cấu trúc và thao tác mới

Source chỉ còn hai cây `backend/` và `frontend/`. README/AGENTS/CLAUDE, tài liệu
chung và metadata dự án vẫn có thể ở root. Compose nằm ở `backend/compose.yaml`,
giữ project/volume `domaincheck` và vẫn đọc cùng backend/.env như trước.

- Cài backend: `python -m pip install -e "./backend[test]"` trong venv thích hợp.
- API/worker chạy bằng package `domaincheck`; các script web ở `backend/scripts/`
  và `backend/run_worker.bat` còn được hỗ trợ.
- Test: backend unittest, frontend Vitest; không còn chạy bộ test legacy.
- Engine version không đổi; **deploy cần cập nhật wheel và thay process API/worker
  cùng release tại thời điểm an toàn**. Đợt sửa source không restart worker đang
  có job, không tự deploy production.

## Dữ liệu được giữ nguyên

Không đọc/hiển thị/sửa/xóa/move config.ini thật, backend/.env, DB production,
data/reports/logs/artifacts, build/dist có sẵn hoặc Chrome profile. Những thư mục
local này không phải source của bản web và không được copy vào release mới.

Source extension vẫn ở FE; `chrome_extension/` local ignored được giữ vì một
Chrome profile đang cài extension bản hiện tại có thể phụ thuộc đường dẫn đó để
giữ ID. Đây là bản sinh của **extension đang dùng bởi bản web**, không phải ứng
dụng cũ được duy trì. Helper đổi tên thành
`backend/scripts/sync_installed_extension.py`; không chỉnh profile hoặc reload
Chrome tự động. Logo runtime dùng bản đã bundle trong backend; ảnh thiết kế icon
chuyển sang `frontend/design-assets/`, root asset trùng bị bỏ.

## Kiểm thử

| Kiểm tra | Kết quả |
|---|---|
| Backend đầy đủ trên PostgreSQL/Redis cô lập, migration sạch, fresh wheel | **374/374 đạt, 0 skip** |
| Frontend trước cập nhật extension | **148/148 đạt** |
| Frontend cuối sau cập nhật extension | **154/154 đạt**, gồm 6 regression protocol/bridge/fill_only |
| Wheel ngoài repo/không editable fallback | **3/3 đạt**, gồm trong full suite |
| Guard package/import/source layout | **3/3 đạt**, gồm trong full suite |
| Characterization Browser Evidence/cloaking được giữ lại | **50 test đạt**, gồm trong full suite |
| Exact dummy mail/HTML fixtures | Đạt, gồm trong full suite |
| FE build/typecheck/lint; compileall/pip check | Đạt |
| Hai cây source được copy riêng, không có code/config/runtime ở root | FE build + ZIP đạt; wheel backend trùng SHA-256 với bản build trong repo |
| Worker shortcut hiện tại từ CWD ngoài repo | --help đạt cho precheck/Quick Report; không chạy job |

Guard source kiểm tra không bare-import engine cũ và không có runtime cũ.
Guard import dùng config.ini sentinel trong CWD tạm để phát hiện truy cập không
hợp lệ, đồng thời kiểm tra không tạo data và không nạp alias top-level. Wheel
test xác nhận engine nằm đúng package, không bundle secret/config/compat/pages.

Các lỗi 4 failure/7 error trong báo cáo migration trước thuộc bộ ứng dụng cũ đã
bị retire theo phạm vi người dùng, **không được tuyên bố là đã sửa**. Bộ kiểm thử
hiện tại bảo vệ bản web được hỗ trợ và các characterization engine còn cần dùng.
Không thử gửi/submit/IMAP/probe URL nghi vấn thật để chứng minh refactor.

## Review và rollback

Thay đổi được stage để review cùng một bộ; chưa commit hay deploy. Bản báo cáo
repository-migration trước là lịch sử của bước trung gian, không còn là hướng
dẫn cấu trúc hiện hành. Rollback chỉ revert source/version package tương ứng,
không xóa runtime, khôi phục/reset delivery, grant hay Chrome profile. Không cần
rollback database vì không có schema/data migration trong đợt này.
