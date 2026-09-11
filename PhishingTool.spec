# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file cho PhishingTool (Streamlit app).
Build: pyinstaller PhishingTool.spec
Output: dist/PhishingTool/ (folder)
"""

import os
import sys
import importlib.metadata
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

streamlit_datas = collect_data_files("streamlit", include_py_files=True)

try:
    altair_datas = collect_data_files("altair")
except Exception:
    altair_datas = []

# Bundle data files cho các package có file tĩnh (không phải chỉ .py)
try:
    whois_datas = collect_data_files("whois")
except Exception:
    whois_datas = []

try:
    dns_datas = collect_data_files("dns")
except Exception:
    dns_datas = []

try:
    certifi_datas = collect_data_files("certifi")
except Exception:
    certifi_datas = []

try:
    import playwright
    _playwright_root = Path(playwright.__file__).resolve().parent
    # `PLAYWRIGHT_BROWSERS_PATH=0` installs into the Node driver package, but a
    # frozen Playwright runtime resolves its hermetic directory as the sibling
    # `driver/package.local-browsers` (without `/package/`).  Copy every browser
    # file explicitly to that runtime destination instead of relying on the
    # generic package-data collector.
    _local_browsers = _playwright_root / "driver" / "package" / ".local-browsers"
    _has_chromium = any(_local_browsers.glob("chromium-*/chrome-win*/chrome.exe"))
    _has_headless_shell = any(
        _local_browsers.glob(
            "chromium_headless_shell-*/chrome-headless-shell-win*/chrome-headless-shell.exe"
        )
    )
    if not (_has_chromium and _has_headless_shell):
        raise SystemExit(
            "Playwright Chromium is missing from package.local-browsers. "
            "Run build_app.bat, or set PLAYWRIGHT_BROWSERS_PATH=0 then run "
            "python -m playwright install chromium chromium-headless-shell before PyInstaller."
        )
    # `collect_data_files()` still picks up hidden `.local-browsers` on Windows
    # even when an exclude glob is supplied. Filter its resolved tuples so the
    # 700 MB source tree is not bundled a second time at `package/.local-browsers`.
    playwright_datas = [
        (source, destination)
        for source, destination in collect_data_files("playwright")
        if ".local-browsers" not in Path(source).parts
        and ".local-browsers" not in Path(destination).parts
    ]
    playwright_browser_datas = [
        (
            str(source),
            str(
                Path("playwright") / "driver" / "package.local-browsers"
                / source.relative_to(_local_browsers).parent
            ),
        )
        for source in _local_browsers.rglob("*")
        if source.is_file()
    ]
except SystemExit:
    raise
except Exception:
    playwright_datas = []
    playwright_browser_datas = []

# Bundle .dist-info metadata - Streamlit doc importlib.metadata lay version khi runtime
def _distinfo_data(pkg_name):
    try:
        d = importlib.metadata.distribution(pkg_name)
        src = str(d._path)
        dirname = os.path.basename(src)
        return (src, dirname)
    except Exception:
        return None

_metadata_pkgs = [
    "streamlit", "altair", "pandas", "numpy", "pyarrow", "click",
    "pydeck", "pillow", "requests", "protobuf", "packaging", "tenacity",
    "blinker", "cachetools", "watchdog",
]
metadata_datas = [d for d in (_distinfo_data(p) for p in _metadata_pkgs) if d is not None]

hidden_imports = [
    "streamlit", "streamlit.web", "streamlit.web.cli", "streamlit.runtime",
    "streamlit.runtime.scriptrunner", "streamlit.runtime.caching",
    "streamlit.components.v1", "streamlit.elements",
    "altair", "pandas", "numpy", "pyarrow", "pydeck", "PIL", "requests",
    "dns", "dns.resolver", "dns.rdatatype",
    "cryptography", "cryptography.x509",
    "whois", "ipwhois", "click", "toml",
    "smtplib", "imaplib", "email", "email.mime", "email.mime.text", "email.mime.multipart",
    "playwright", "playwright.sync_api", "playwright._impl._driver",
]

app_datas = [
    ("streamlit_app.py",    "."),
    ("streamlit_home.py",   "."),
    ("phishing_toolkit.py", "."),
    ("cloaking_detector.py", "."),
    ("cloaking_ui.py",      "."),
    ("cloaking_review_queue.py", "."),
    ("cloaking_review_sender.py", "."),
    ("community_report_ui.py", "."),
    ("email_send_ui.py",    "."),
    ("domain_worker.py",    "."),
    ("domain_utils.py",     "."),
    ("link_status.py",      "."),
    ("domain_check.py",     "."),
    ("provider_replies.py", "."),
    ("mail_statistics.py", "."),
    ("report_statistics.py", "."),
    ("sent_mail_evidence.py", "."),
    ("general_statistics.py", "."),
    ("cloudflare_form_worker.py", "."),
    ("cloudflare_profile_bridge.py", "."),
    ("chrome_extension", "chrome_extension"),
    ("pages",               "pages"),
    ("config.example.ini",  "."),
]

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=[],
    datas=streamlit_datas + altair_datas + whois_datas + dns_datas + certifi_datas + playwright_datas + playwright_browser_datas + metadata_datas + app_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "IPython", "jupyter"],
    noarchive=False,
    optimize=0,
)

# PyInstaller 6 may add and reclassify Playwright's package data while Analysis runs.
# Keep only the explicit frozen-runtime copy at driver/package.local-browsers;
# otherwise COLLECT creates a second hard-linked browser tree. Hard links save
# local disk blocks, but ZIP tools store both paths and add about 700 MB.
def _without_source_playwright_browsers(toc):
    return type(toc)(
        entry
        for entry in toc
        if ".local-browsers" not in Path(entry[0]).parts
    )


a.datas = _without_source_playwright_browsers(a.datas)
a.binaries = _without_source_playwright_browsers(a.binaries)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PhishingTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False, upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[],
    name="PhishingTool",
)
