# Extension 3.1.7 — kết nối production/local

Mặc định cho phép exact origins `https://tool-check.camellrp.com`,
`http://localhost:5173`, `http://127.0.0.1:5173` (cổng Vite hiện tại).
Tự tạo device ID ở kết nối hợp lệ đầu tiên; giữ ID và custom origin của bản cũ.
Options chỉ dùng cho một origin bổ sung. Custom origin vẫn cần cấp host permission.

Fixture chạy background script thật bằng Chrome API mock: handshake cài mới đồng
thời tạo một device ID, chặn host giả/HTTP production/cổng khác/URL trống dù payload
khai báo origin hợp lệ; legacy custom origin vẫn kết nối. Exchange lấy sender URL;
status task production vẫn gọi production sau khi local kết nối. Giữ fill_only,
không tái thêm bridge local hay auto-submit. Request API có timeout 15 giây.

Build/typecheck và lint đạt. ZIP được kiểm tra version 3.1.7 và CRC.
Một lượt full FE đạt 200/200; lượt kế tiếp có 199 đạt, một timeout 5 giây tại
Audit Log trong khi chạy nhiều kiểm tra. Lượt chạy lại cuối đạt 200/200.
Không sửa test timeout để che lỗi.
Không chạy API/worker, sửa env/profile hoặc gửi report. Chưa xác minh tương tác
trên Chrome thật; các bước kiểm thử người dùng nằm trong technical-guide.md.

Tham chiếu manifest: https://developer.chrome.com/docs/extensions/reference/manifest/externally-connectable
