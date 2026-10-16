"""Legacy entry point; implementation lives in backend/compat."""
from pathlib import Path as _CompatPath
import sys as _compat_sys
_compat_root = _CompatPath(__file__).resolve().parent
_compat_src = str(_compat_root / "backend" / "src")
if _compat_src not in _compat_sys.path:
    _compat_sys.path.insert(0, _compat_src)
_compat_code = _compat_root / "backend" / "compat" / "cloaking_detector.py"
exec(compile(_compat_code.read_bytes(), str(_compat_code), "exec"), globals(), globals())
