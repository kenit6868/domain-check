# Extension 3.1.8 — ID chung

Public key RSA SPKI được lưu trong manifest; không lưu private key. SHA-256
public key tạo ID Chrome `aaodigjnbngpggpgkphmeaakpfhehcpb`. Không đổi key khi
nâng cấp. FE dùng ID chung khi env trống; vẫn giữ override cho bản tùy biến.
Env cũ trên VPS/local cần quản trị đổi một lần rồi build FE.

Kiểm thử tự động: 201/201 frontend đạt; build/typecheck đạt. Test kiểm tra public
key hợp lệ và ID suy ra khớp FE; giữ các test origin/task của 3.1.7.
ZIP build mới có manifest 3.1.8 và cùng key source. Chưa thử cài trên hai Chrome
thật; cần Load unpacked trên hai profile/thư mục và so ID, rồi handshake/form.
Không sửa env/profile, đồng bộ bản extension đang cài hoặc deploy lên VPS.

Người dùng không nhập ID/origin ở domain mặc định. Cài unpacked lần đầu vẫn cần
Developer mode và Load unpacked; tải ZIP không tự cài. Khi chuyển từ ID cũ cần
gỡ bản cũ nếu Chrome giữ ID cũ, nạp lại gói mới, dùng task mới.

Nguồn: [Chrome manifest key](https://developer.chrome.com/docs/extensions/reference/manifest/key).
