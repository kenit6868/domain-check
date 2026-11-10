# Thống nhất cấu hình local/VPS — 10/11/2026

`frontend/.env` là vị trí cấu hình FE trong cả Vite local và Docker production. `VITE_FORM_ASSISTANT_EXTENSION_ID` không còn được yêu cầu khai báo trong `backend/.env.production.example`. Backend/hạ tầng tiếp tục dùng `backend/.env`.

Helper deploy truyền hai `--env-file` theo thứ tự backend rồi frontend vào tất cả lệnh Compose. Trước thao tác thật, helper kiểm tra cả hai file tồn tại; thiếu frontend env thì dừng trước build, start hoặc stop. Dry-run vẫn không gọi Docker. Dockerfile FE giữ nguyên cơ chế build argument và loại `.env` khỏi context/image; không sao chép file env thật.

Kiểm tra trực tiếp `test_team_deployment.py`: **6/6 đạt**, gồm failure trước start, thiếu frontend env không gọi Docker, kế hoạch deploy đúng thứ tự và Compose render JSON bằng fixture không cần daemon. Render xác nhận ID chỉ là FE build arg, không xuất hiện trong environment API/worker với bố cục chuẩn. Không build/chạy container thực tế.

Frontend đầy đủ: **198/198 đạt**; build/typecheck/lint đạt. Compileall cho source/tests/scripts, pip check và git diff --check đạt. Dry-run in đúng kế hoạch với hai env file, không chạy process ứng dụng. Tài liệu dev/team/technical và README đã cập nhật.

Không đọc hoặc sửa `.env` thật, `config.ini`, runtime hoặc profile Chrome; không gửi report, migration, deploy hoặc restart dịch vụ. Cần commit/push source đã sửa trước khi VPS pull bản mới. Người vận hành tạo `frontend/.env` từ mẫu và tự điền ID công khai; không commit env. Nếu backend env cũ có dòng ID thì bỏ dòng trùng sau khi đã chuyển giá trị sang frontend env. Đổi ID vẫn cần build lại FE.
