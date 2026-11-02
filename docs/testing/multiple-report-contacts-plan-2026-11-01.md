# Báo cáo và kế hoạch hỗ trợ nhiều form/email báo cáo

Ngày: 2026-11-01. Phạm vi: FastAPI/worker và React hiện tại.

Tài liệu này thay thế các bản kế hoạch trước trong cùng file. Bỏ đề xuất loại email registry khi registry có form và bỏ đề xuất chế độ both cho registrar. Chưa triển khai code sản phẩm.

## 1. Mục tiêu và quy tắc nghiệp vụ

Mở rộng số lượng form/email có thể khai báo, giữ chính sách chọn kênh hiện hành:

| Đối tượng | Có form | Không có form |
| --- | --- | --- |
| Registrar thuộc WEB_FORM_REGISTRARS | Hiển thị các form; loại email registrar khỏi recipient và draft báo cáo | Không tự fallback SMTP nếu cấu hình form của mục này lỗi hoặc rỗng; cần phát hiện lỗi cấu hình |
| Registrar khác | Khi được đưa vào danh sách dùng form sẽ áp dụng chính sách form-only | Dùng email abuse hợp lệ theo thứ tự nguồn hiện tại; hỗ trợ nhiều địa chỉ |
| Registry | Hiển thị các form và giữ các email abuse nếu có | Giữ email abuse nếu có |

Quy tắc registrar chỉ tác động registrar, không làm mất email registry hoặc hosting. Registry có form không làm mất email của chính registry. Không suy diễn chính sách từ việc URL mở thành công/thất bại.

Ví dụ bắt buộc giữ đúng: .xyz có form https://gen.xyz/account/submitticket.php?step=2&deptid=6 và email xyz_abuse@gen.xyz trong source. Sau thay đổi, cả hai vẫn tồn tại; adapter xyz_registry_abuse vẫn hoạt động.

“Có email” ở đây là kênh đủ điều kiện hiển thị/chọn trong workflow hiện có, không đồng nghĩa tự gửi. SMTP vẫn phải đi qua evidence, preview, xác nhận và ledger hiện tại.

## 2. Kết quả rà source

| Thành phần | Hiện trạng | Ý nghĩa với yêu cầu |
| --- | --- | --- |
| engine/phishing_toolkit.py: CCTLD_REGISTRY_CONTACTS | Mỗi entry có registry, abuse_email và report_webform; .xyz có cả mail và form; .hk.com có form UDR và abuse_email=None | Mở rộng dữ liệu nhiều giá trị, không đổi chính sách registry |
| REGISTRAR_ABUSE_EMAILS | Mỗi key trả một chuỗi contact | Nhiều email cần được chuẩn hóa có kiểm soát, không ép list thành str |
| WEB_FORM_REGISTRARS | Mỗi key có một URL; key cũng xác định registrar chỉ dùng form | Giữ membership/policy; cho phép nhiều form trong mục |
| resolve_quick_report_recipients | Bỏ email registrar nếu thuộc danh sách form; vẫn thêm email registry dù có form | Giữ nhánh quyết định, mở rộng chuẩn hóa email |
| resolve_worker_report_recipients | Cùng nguyên tắc registrar form-only; email registry hiện chỉ lấy nguồn static_table | Giữ điều kiện nguồn, không mở rộng recipient từ IANA/nguồn chưa xác minh |
| core/quick_report_routes.py: report_forms | Trả list nhưng chỉ một form registrar khớp đầu tiên và một form registry | Cần lặp các form của mục đã match; không gộp mọi alias cùng khớp |
| api/quick_reports.py: form-drafts | Lặp report_forms; draft registry lấy contact tĩnh chung | Cần bind đúng nhãn/provider và nội dung của từng form |
| core/report_guidance.py | Registrar loại mail khi có form; registry sinh form và email độc lập | Giữ hai nhánh registry; mở rộng từ một thành nhiều |
| QuickReportProviderActions.tsx | Đã render danh sách form | Chủ yếu bổ sung nhãn/ID; không cần thiết kế lại trang |
| QuickReportDraftPanel.tsx | Key/value dựa channel+label | Hai form trùng tên có thể chọn nhầm draft; cần ID ổn định |
| DomainCheckPage.tsx | Key card dựa channel+label | Cần ID cho nhiều form/email cùng nhà cung cấp |
| core/worker_precheck.py: valid_recipients | Tách email bằng getaddresses, validate/dedupe từng địa chỉ; không có recipient thì excluded_no_email | Tái sử dụng; không biến form-only thành ready SMTP |
| core/worker_precheck.py: email_examples | Registry formatter nhận registry_contact chung | Khi nhiều mail, cần đảm bảo To trong mỗi draft đúng recipient đang xử lý |
| application/previews.py, domain_worker_preview.py, delivery_dispatch.py | Có exact recipient/profile/evidence và intent/checkpoint | Tái sử dụng, không thiết kế lại cơ chế gửi |
| core/form_tasks.py và extension | Provider cố định; có .xyz, chưa có UDR/Host Hong Kong | Giữ provider cũ; form bổ sung mở thủ công ở giai đoạn đầu |

Tên file đúng trong repository là backend/src/domaincheck/engine/phishing_toolkit.py.

Quick Report là luồng kiểm tra và hướng dẫn báo cáo; không tự bổ sung chức năng gửi mail trên trang này. Check Domain cung cấp mẫu hướng dẫn. Domain Worker/review dùng mail qua pipeline preview/gửi hiện tại.

## 3. Thiết kế dữ liệu nhỏ nhất cần thiết

Giữ các bảng trong phishing_toolkit.py; không tạo DB catalog hoặc trang quản trị.

### Form

Một mục hỗ trợ danh sách form, mỗi form có:

- id: ổn định trong catalog, dùng cho API, key và chọn draft.
- label: tên phân biệt trên giao diện.
- url: HTTPS, giữ query chức năng.
- note: tùy chọn.
- autofill_provider: tùy chọn, giữ mapping provider hiện có.

Helper nội bộ chuẩn hóa URL đơn hiện tại thành danh sách một form. Mục có nhiều form khai báo danh sách; không bắt buộc chuyển hàng trăm entry hiện tại ngay. Chọn một nguồn dữ liệu chuẩn cho từng mục, không đồng thời khai báo URL đơn và list gây không rõ ưu tiên. Validation phát hiện cấu hình trùng ID, URL sai hoặc mục form-only rỗng.

Đây là chuẩn hóa dữ liệu nội bộ package hiện hành, không thêm root wrapper/compat hoặc phục hồi ứng dụng cũ.

### Email

Cho phép khai báo nhiều địa chỉ rõ ràng; helper chuẩn hóa đầu vào hiện tại và list mới thành danh sách email hợp lệ. Không dùng str(list). Không cộng tất cả contact từ WHOIS/RDAP/static: giữ ưu tiên explicit WHOIS abuse, RDAP rồi static fallback ở luồng hiện có.

Giữ điều kiện source riêng của từng consumer; không lấy email admin/registrant, không biến contact chưa xác minh thành recipient. Dedupe từng địa chỉ sau chuẩn hóa, giữ cơ chế chặn recipient và privacy hiện có.

Registrar thuộc danh sách form-only: không xuất email dù WHOIS/RDAP/static có địa chỉ. Registry: chuẩn hóa form và email độc lập, có cả hai thì trả cả hai.

## 4. Áp dụng .hk.com và hồi quy .xyz

### .hk.com

Giữ form UDR, bổ sung lựa chọn thứ hai theo yêu cầu:

| ID dự kiến | Nhãn | URL |
| --- | --- | --- |
| hk-com-udr-abuse | UDR — Report Abuse | https://www.udr.hk.com/report-abuse |
| hk-com-hosthongkong-ticket | Host Hong Kong — Ticket bổ sung | https://www.hosthongkong.net/billing/submitticket.php?step=2&deptid=3 |

Chỉ áp dụng .hk.com, không tự lan sang .hk.org/.ltd.hk/.inc.hk. Giữ email hiện tại None; không tự tìm hoặc thêm mail mới trong hạng mục này. Nếu sau này cấu hình email registry hợp lệ, email đó vẫn cùng tồn tại với hai form.

Host Hong Kong có nhãn và ghi chú riêng trong phần hướng dẫn liên quan; không gọi bên này là UDR hoặc khẳng định là registry/registrar/hosting của mọi domain .hk.com. Mẫu ticket bổ sung phải mô tả dữ kiện và đề nghị xác minh/chuyển bộ phận thích hợp, không tái dùng nguyên lời chào registry UDR.

Bỏ utm_source=chatgpt.com khỏi URL UDR. Giữ step=2 và deptid=3 của ticket; dedupe không xóa mọi query parameter.

### .xyz

Giữ nguyên email xyz_abuse@gen.xyz, form ticket và provider xyz_registry_abuse. Check Domain vẫn có hướng dẫn form và email. Quick Report vẫn có form và contact mail trong kết quả hiện có. Domain Worker vẫn nhận email registry khi đạt điều kiện nguồn; registrar form-only không chặn email .xyz.

Không đổi các TLD cùng họ XYZ chỉ để minh họa tính năng; dùng chúng làm kiểm tra hồi quy route/provider hiện có.

## 5. Luồng thực hiện và phạm vi file

### Giai đoạn A — helper và catalog

- Thêm helper chuẩn hóa form/email gần các bảng trong phishing_toolkit.py.
- Giữ match registrar đầu tiên theo logic hiện tại; lặp form trong mục đã chọn. Giữ suffix lookup hiện tại, không đổi WHOIS/PSL trong việc này.
- Cập nhật hai recipient resolver để hiểu nhiều email, giữ registrar form-only và registry form+mail.
- Thêm hai form .hk.com với label/draft phù hợp.

### Giai đoạn B — API và React

- quick_report_routes.py trả đủ form, id và metadata tối thiểu.
- api/quick_reports.py tạo draft đúng từng form và giữ provider cũ.
- report_guidance.py dùng danh sách registry form và email độc lập. Registrar không form có nhiều email không bị cắt âm thầm bởi giới hạn recipient_candidates[:3] hiện tại; thống nhất cách hiển thị từng recipient/draft.
- Quick Report schema, ProviderActions và DraftPanel nhận ID; nút/selector/copy không đụng key khi trùng label.
- DomainCheck API/Page nhận ID cho từng hướng dẫn form/email; giữ bố cục hiện tại.
- Không gọi extension autofill cho URL mới khi chưa có adapter/allowlist tương ứng. Link mở thủ công không cần thêm host permission extension.

### Giai đoạn C — mail và hồi quy

- Tái sử dụng valid_recipients, preview và dispatcher hiện tại.
- Rà email_examples và formatter registry: bản draft/preview cho địa chỉ A phải có To A, không tái dùng chuỗi toàn bộ email cho mỗi địa chỉ.
- Tạo các delivery theo recipient bằng cơ chế hiện có; không thay khóa ledger hoặc chính sách resend để thực hiện mở rộng contact.
- Không có email phù hợp vẫn excluded_no_email; nếu registrar chỉ có form nhưng registry có mail thì registry vẫn có thể đi tiếp theo quy tắc evidence/review.
- Bổ sung test chứng minh form mới không tự gửi, không thay đổi sent/unknown hoặc retry sent-copy.

## 6. Cache, lịch sử và triển khai

- Thêm form thường tác động danh sách dựng từ catalog lúc đọc; mail trong check/precheck là snapshot. Không giả định thêm form đồng nghĩa preview cũ đã thay đổi.
- Nếu nhiều email hoặc chuẩn hóa thay đổi recipient của check mới, tạo contact/cache policy mới tại consumer liên quan; không đổi version đã bind dữ liệu cũ. Không đổi engine version chỉ vì sửa cấu trúc dữ liệu.
- Không sửa recipient/body của preview đã duyệt, batch đang chạy hoặc delivery lịch sử. Không tự enqueue check/send sau sửa catalog.
- Ghi rõ người dùng phải check/precheck lại và tạo preview mới để dùng email mới. Lịch sử giữ nguyên; danh sách form hướng dẫn có thể phản ánh catalog mới như hành vi hiện tại.
- Dự kiến không cần migration nếu giữ catalog source, thêm DTO và result JSON; xác nhận theo implementation cuối. Không cấp quyền mới hoặc seed lại catalog quyền.
- Cần build FE; nạp API/worker theo quy trình triển khai sau khi job hiện tại hoàn tất. Việc viết/sửa source không cho phép tự deploy hoặc restart worker thật.
- Cập nhật technical guide và skill nếu workflow thay đổi; README khi đổi cách sử dụng, CLAUDE/AGENTS nếu cần ghi rõ invariant registrar/registry. Không tài liệu hóa sai rằng registry có form sẽ loại mail.

## 7. Ma trận kiểm thử bắt buộc khi triển khai

| Trường hợp | Kết quả cần đạt |
| --- | --- |
| Registrar form-only có hai form và WHOIS/RDAP/static mail | Hai form, không có recipient/email draft registrar |
| Registrar không form có nhiều email abuse | Email hợp lệ đủ, không trùng; không âm thầm cắt còn ba ở trang hướng dẫn |
| Registry có hai form và hai email | Đủ form và mail; draft đúng nhà cung cấp/recipient |
| Registrar có form, registry .xyz có form+mail | Registrar không mail; .xyz vẫn giữ mail và form |
| Registry có form, registrar không form có mail | Mail registrar vẫn còn |
| .hk.com | Đúng hai lựa chọn, URL/label/draft riêng, không tạo email chưa cấu hình |
| .hk.org/.ltd.hk/.inc.hk | Không tự xuất hiện Host Hong Kong |
| .xyz và họ XYZ hiện tại | Không mất email, form hoặc adapter autofill |
| Hai form cùng label / URL khác query chức năng | ID khác, chọn đúng draft/URL, không dedupe sai |
| Email sai, trùng, privacy, blocked | Lọc đúng; không lấy admin/registrant làm abuse |
| Form-only không còn email khác | Không tự trở thành SMTP-ready |
| Một mail sent, mail khác failed/unknown | Giữ resend/checkpoint hiện có, không gửi lại sent/unknown |
| Preview/batch lịch sử và cache | Không sửa plan cũ; check mới dùng policy/cache phù hợp |
| Owner/account/day và quyền | Không mở rộng scope; giữ API RBAC/CSRF |

Dùng mock/fixture, không SMTP/IMAP/provider submission hoặc probe URL nghi vấn thật. Chạy test trực tiếp và toàn bộ backend với PostgreSQL/Redis cô lập; FE test/build/lint; compileall và pip check theo quy định. Chỉ cần fresh-wheel kiến trúc nếu triển khai phát sinh refactor kiến trúc, không mặc định biến hạng mục này thành refactor toàn hệ thống.

## 8. Nguồn và giới hạn xác minh

Các trang đã đọc ở lượt phân tích trước:

- UDR Report Abuse: https://www.udr.hk.com/report-abuse — xác nhận trang dành cho namespace .hk.com/.hk.org/.ltd.hk/.inc.hk.
- Host Hong Kong: https://www.hosthongkong.net/billing/submitticket.php?step=2&deptid=3 — mở trang Open Ticket. Nội dung trích xuất chưa đủ xác nhận deptid=3 là abuse hoặc xử lý toàn namespace .hk.com.
- UDR registrar listing: https://www.udr.hk.com/registrars/accredited-registrars — chưa đủ bằng chứng văn bản về quan hệ Host Hong Kong cho mọi domain.

Các kết luận về .xyz ở đây là hành vi/catalog source đang có, không phải kiểm chứng lại chính sách tiếp nhận hiện thời của nhà cung cấp. Lượt viết lại kế hoạch chỉ đọc source và tài liệu local, không truy cập lại provider.

## 9. Trạng thái và tiêu chí nghiệm thu

Chỉ thay tài liệu kế hoạch này, chưa sửa code sản phẩm, runtime hoặc secret. Lượt phân tích đầu đã chạy 19/19 test routing/contact hiện trạng đạt, không skip; đó không phải kiểm thử tính năng nhiều contact. Lượt này không chạy full backend/FE vì chỉ cập nhật kế hoạch.

Nghiệm thu khi triển khai: thêm được nhiều form/email qua catalog source; .hk.com hiện hai lựa chọn riêng; registrar form-only tiếp tục không mail; registry .xyz tiếp tục cả form lẫn mail; draft/preview đúng endpoint/recipient; không thay đổi lịch sử hoặc gây gửi lại. Không cần thêm trang quản trị, channel mới, autofill mới hay thiết kế lại SMTP để đáp ứng phạm vi này.
