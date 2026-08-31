# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file cho PhishingTool (Streamlit app).
Build: pyinstaller PhishingTool.spec
Output: dist/PhishingTool/ (folder)
"""

import os
import sys
import importlib.metadata
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
    playwright_datas = collect_data_files("playwright")
except Exception:
    playwright_datas = []

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
    ("pages",               "pages"),
    ("config.example.ini",  "."),
]

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=[],
    datas=streamlit_datas + altair_datas + whois_datas + dns_datas + certifi_datas + playwright_datas + metadata_datas + app_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "IPython", "jupyter"],
    noarchive=False,
    optimize=0,
)

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
