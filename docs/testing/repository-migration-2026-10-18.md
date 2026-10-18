> Historical intermediate migration. Superseded by [web-only cutover](web-only-cutover-2026-10-18.md); current source has no desktop/root wrappers or backend/compat.

# Rà soát và di chuyển source — 18/10/2026

Đây là đợt di chuyển có giữ tương thích, chưa phải xóa toàn bộ runtime ở root.
Mục tiêu là hai cây source `backend/` và `frontend/`; tài liệu dùng chung,
metadata dự án và dữ liệu vận hành không được coi là source cần chuyển máy móc.

## Những nhóm đã chuyển

| Nhóm | Trước | Sau | Tương thích |
|---|---|---|---|
| Web Form Assistant, 20 file | `chrome_extension/cloudflare-profile-worker/` | `frontend/extensions/cloudflare-profile-worker/` | Vite ZIP/dev route cập nhật; frozen bundle vẫn dùng destination `chrome_extension/` |
| 13 module nghiệp vụ Python | Source đầy đủ tại root | `backend/compat/` | Root chỉ còn exec wrapper, giữ import/CLI/namespace/`__file__` và BASE_DIR |
| Test legacy | `tests/` | `backend/legacy_tests/` | `python backend/run_legacy_tests.py` tự chọn workspace đúng |
| Worker shortcuts và builder | Implementation trong `.bat` root | `backend/scripts/` | Root `.bat` chuyển tiếp, giữ tham số và CWD dự kiến |
| PyInstaller spec | `PhishingTool.spec` root | `backend/packaging/PhishingTool.spec` | Root spec chuyển tiếp; build command cũ còn dùng được |
| Dependencies desktop/legacy | `requirements.txt` root | `backend/requirements-legacy.txt` | Root requirements dùng `-r`, giữ lệnh cài cũ |

13 module mới chuyển: `cloaking_review_queue`, `cloaking_review_sender`,
`cloudflare_form_worker`, `cloudflare_profile_bridge`, `domain_check`,
`domain_utils`, `domain_worker`, `general_statistics`, `lead_triage`,
`link_status`, `mail_statistics`, `report_statistics`, `sent_mail_evidence`.
Cộng với 7 module đã có, source nghiệp vụ canonical là **20 module** trong
`backend/compat/`. 12/13 file mới chuyển giữ nguyên byte; file Cloudflare worker
chỉ đổi resolver đường dẫn extension source/frozen. Nội dung 20 file extension
giữ nguyên SHA-256 trước/sau.

## Bảo toàn extension đang cài

Chrome unpacked không có manifest key ổn định có thể đổi ID khi đổi thư mục
cài. Vì vậy đường dẫn cũ không bị biến thành source thứ hai: script
`backend/scripts/sync_legacy_extension.py` sinh bản sao runtime tại đó, được
gitignore. Đã sync bản sao local để profile hiện tại còn tìm được file. Script
không sửa profile, không reload/cài Chrome, không xóa file ngoài danh sách copy,
và không copy `.env` hay `config.ini`. Sau mỗi lần sửa source, sync và Reload
Chrome rõ ràng; cài mới có thể dùng source FE hoặc ZIP từ Quick Report.

## Bằng chứng kiểm thử

| Kiểm tra | Kết quả |
|---|---|
| Frontend đầy đủ trước di chuyển | 148/148 đạt |
| Frontend đầy đủ sau di chuyển | 148/148 đạt |
| Backend lượt cuối trên PostgreSQL 17 riêng, migration 0001–0005 sạch + wheel mới | 321 test: 320 đạt, 1 skip Redis integration |
| Cache/Redis trên Redis container riêng | 7/7 đạt, bao gồm integration test bị skip trong lượt full backend |
| Wheel mới, chạy ngoài repo và không dùng editable paths | 3/3 đạt, gồm fresh-wheel test vốn bị skip ở lượt full |
| Backend chỉ có cây backend, không có root source/FE | Build wheel đạt; wheel cùng SHA-256 với build trong repo |
| Frontend chỉ có cây frontend, không có root source/BE | Build đạt, có ZIP extension; dùng lại installed node_modules, không thử cài dependency offline |
| Extension ZIP production + download Vite local | HTTP 200; CRC hợp lệ; đủ 20 file khớp source |
| Canonical source và bản sao extension đường dẫn cũ | Khớp byte với 20 file gốc |
| Legacy trước di chuyển | 420 test: 4 failure, 7 error đã có |
| Legacy sau di chuyển | 424 test: 4 regression mới đạt; cùng 4 failure, 7 error, trùng danh tính test trước di chuyển |
| Build/typecheck/lint/compileall và pip check system/backend | Đạt |
| Shortcut Domain Worker/Quick Report từ CWD ngoài repo | `--help` đạt, không khởi chạy job; Quick Report chuyển tiếp tham số tới CLI |

4 regression legacy mới kiểm tra wrapper/runtime location, CLI `--help` từ
working directory khác, source/frozen extension path và tất cả input của spec
đều tồn tại, không bundle config.ini thật. CLI smoke chạy UTF-8 vì terminal
Windows mặc định cp1252 không biểu diễn được help tiếng Việt; không thay đổi
encoding của ứng dụng trong đợt này.

Baseline legacy chưa xanh: các test liên quan mock macOS Chrome launcher,
stop/terminate process, lead-triage cloaking/formatter, retry worker và provider
reply mailbox/UI vẫn giữ lỗi trước đó. Không quy các lỗi này là do migration,
đồng thời không tuyên bố toàn bộ legacy đã đạt. Không sửa logic để che baseline.

Không chạy SMTP/IMAP/provider submit thật, không probe URL nghi vấn. Không build
EXE bằng builder vì builder legacy có bước copy config.ini thật; chỉ kiểm tra
đường dẫn/spec/dependency và browser-packaging contract. Không thay đổi dữ liệu
production hoặc restart worker đang chạy. PostgreSQL test riêng không dùng DB
production.

Bộ thay đổi source được stage để các file di chuyển và wrapper có thể được
review/commit cùng nhau; chưa tạo commit hay deploy. PostgreSQL/Redis test là
container tạm do đợt kiểm thử tạo, đã dọn sau kiểm thử; không dừng container của
ứng dụng.

## Phần còn ở root và điều kiện dọn tiếp

| Phần còn lại | Lý do giữ | Đích/điều kiện tiếp theo |
|---|---|---|
| 20 `.py` wrapper | Legacy imports, CLI và subprocess còn dùng đường dẫn cũ | Chỉ xóa sau khi cutover entrypoint và xác nhận không còn caller cũ |
| `streamlit_app.py`, `streamlit_home.py`, `pages/` | Navigation/AppTest và desktop launcher đang dùng | Tách thành `backend/legacy/streamlit/` trong đợt riêng, cập nhật navigation/launcher/asset/runtime resolver đồng bộ |
| `cloaking_ui.py`, `community_report_ui.py`, `email_send_ui.py`, `lead_triage_cloaking_ui.py` | UI helpers của Streamlit còn import trực tiếp | Chuyển cùng runtime Streamlit; giữ chung core canonical |
| `launcher.py`, `.streamlit/`, `assets/` | Frozen entrypoint, theme và logo path legacy | Xác minh frozen app + fallback logo, không sửa đường dẫn secret/runtime tùy tiện |
| `config.ini`, `data/`, logs/reports/artifacts | Secret và dữ liệu vận hành, không phải source migration | Chỉ di chuyển bằng kế hoạch runtime/storage riêng có kiểm chứng |
| Root `.bat`, spec và requirements | Shortcut chuyển tiếp giúp lệnh cũ tiếp tục chạy | Có thể bỏ sau khi cập nhật mọi cách chạy/cài/build bên ngoài |
| `chrome_extension/`, cache, build/dist cũ local | Bản sinh tương thích/cache/artifact có sẵn, không canonical source | Không xóa profile/artifact; bỏ đường dẫn extension cũ sau khi người vận hành chuyển cài và xác nhận ID |
| README/AGENTS/CLAUDE, docs, compose và metadata | Tài liệu/orchestration dùng chung hai cây source | Có thể giữ root; không cần ép chúng vào BE hoặc FE |

Skill Streamlit mà AGENTS nhắc tới không có trong workspace hiện tại. Không
thay widget/navigation/business UI trong đợt này. Trước đợt chuyển runtime
Streamlit cần khôi phục skill và xử lý/parity-test những baseline ảnh hưởng.

## Rollback có giới hạn phạm vi

Lưu thay đổi đợt này thành commit riêng trước khi deploy. Khi rollback chỉ
revert commit source tương ứng; không revert/xóa runtime, secret, DB hoặc Chrome
profile. Root wrapper và shortcut vẫn tồn tại nên cách gọi cũ không đổi. Bản sao
extension compatibility là generated local, đồng bộ lại từ source của version
đang dùng nếu cần. Không rollback bằng cách xóa toàn bộ thư mục dự án.
