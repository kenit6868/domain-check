"""
Launcher cho PhishingTool — entry point khi chạy file .exe build bằng PyInstaller.
Tự động mở browser và chạy Streamlit server.
"""
import sys
import os
import threading
import webbrowser
import time


def _open_browser():
    """Chờ server khởi động xong rồi mở browser."""
    time.sleep(3)
    webbrowser.open("http://localhost:8501")


if __name__ == "__main__":
    # Khi frozen (chạy từ .exe), sys._MEIPASS là thư mục chứa tất cả file đã extract.
    # Khi chạy bình thường (python launcher.py), dùng thư mục hiện tại.
    if getattr(sys, "frozen", False):
        bundle_dir = sys._MEIPASS
        # Thư mục exe — dùng để đọc/ghi config.ini, reports/, case_log.csv
        exe_dir = os.path.dirname(sys.executable)
    else:
        bundle_dir = os.path.dirname(os.path.abspath(__file__))
        exe_dir = bundle_dir

    # Set working directory về thư mục exe để các đường dẫn tương đối (config.ini, reports/) hoạt động
    os.chdir(exe_dir)

    # Mở browser sau 3 giây (background thread)
    threading.Thread(target=_open_browser, daemon=True).start()

    # Chạy Streamlit bằng cách gọi trực tiếp CLI module (không cần lệnh `streamlit` trong PATH)
    app_path = os.path.join(bundle_dir, "streamlit_app.py")
    sys.argv = [
        "streamlit",
        "run",
        app_path,
        "--global.developmentMode=false",
        "--server.headless=true",
        "--server.port=8501",
        "--server.address=localhost",
        "--browser.gatherUsageStats=false",
        "--theme.base=light",
    ]

    from streamlit.web import cli as stcli
    sys.exit(stcli.main())
