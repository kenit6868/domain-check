"""Cloudflare phishing-form preparation, durable ledger, and visible-browser automation.

The module never stores browser profiles, cookies, CAPTCHA data, or page HTML.  It
only persists sanitized per-URL workflow metadata needed for resume and daily
deduplication.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
import time
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import phishing_toolkit as pt


FORM_URL = "https://abuse.cloudflare.com/phishing"
SCHEMA_VERSION = 1
LEDGER_PATH = Path(pt._runtime_path("cloudflare_form_worker.json"))
TERMINAL_STATES = {"SUBMITTED"}
RETRYABLE_STATES = {"READY", "FAILED", "NEEDS_MANUAL", "FILLED"}
_FILE_LOCK = threading.RLock()


def current_day() -> str:
    return datetime.now().astimezone().date().isoformat()


def extension_directory() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "chrome_extension" / "cloudflare-profile-worker"


def open_in_installed_chrome(url: str) -> bool:
    """Route a URL to the already-running installed Chrome, never Playwright."""
    import subprocess
    candidates: list[Path] = []
    if os.name == "nt":
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                        candidates.append(Path(winreg.QueryValue(key, None)))
                except OSError:
                    pass
        except ImportError:
            pass
        for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if root:
                candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for executable in candidates:
        if executable.is_file():
            subprocess.Popen([str(executable), url], close_fds=True)
            return True
    return False


def normalize_target(value: str) -> str:
    value = str(value or "").strip()
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or "." not in host:
        raise ValueError("URL/domain không hợp lệ")
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("Chỉ hỗ trợ URL HTTP(S)")
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{scheme}://{host}{port}{path}{query}"


def record_id(target_url: str, day: str | None = None) -> str:
    raw = f"{day or current_day()}\0{normalize_target(target_url)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _empty_ledger() -> dict:
    return {"version": SCHEMA_VERSION, "records": []}


def load_ledger(path: Path | str = LEDGER_PATH) -> dict:
    path = Path(path)
    with _FILE_LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return _empty_ledger()
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        return _empty_ledger()
    return {"version": SCHEMA_VERSION, "records": data["records"]}


def save_ledger(data: dict, path: Path | str = LEDGER_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": SCHEMA_VERSION, "records": data.get("records", [])}
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    with _FILE_LOCK:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)


def today_records(path: Path | str = LEDGER_PATH) -> list[dict]:
    day = current_day()
    return [dict(item) for item in load_ledger(path)["records"] if item.get("day") == day]


def _clean_error(value: object) -> str:
    text = " ".join(str(value or "").split())
    # Avoid writing query-string tokens accidentally returned by a browser error.
    text = re.sub(r"([?&](?:token|key|captcha|cf-turnstile-response)=)[^&\s]+", r"\1[REDACTED]", text, flags=re.I)
    return text[:500]


def update_record(item_id: str, path: Path | str = LEDGER_PATH, **changes) -> dict | None:
    data = load_ledger(path)
    found = None
    for item in data["records"]:
        if item.get("id") == item_id:
            allowed = {"state", "attempts", "last_error", "updated_at", "mode", "result"}
            for key, value in changes.items():
                if key in allowed:
                    item[key] = _clean_error(value) if key == "last_error" else value
            item["updated_at"] = datetime.now().astimezone().isoformat()
            found = dict(item)
            break
    if found:
        save_ledger(data, path)
    return found


def prepare_urls(
    values: list[str], cfg: dict, *, checker: Callable[[str, dict | None], dict] = pt.run_cdn_check,
    path: Path | str = LEDGER_PATH,
) -> list[dict]:
    """Check URLs with the same CDN logic as Quick Report and upsert today's ledger."""
    data = load_ledger(path)
    existing = {item.get("id"): item for item in data["records"]}
    output: list[dict] = []
    seen: set[str] = set()
    now = datetime.now().astimezone().isoformat()
    for raw in values:
        try:
            target_url = normalize_target(raw)
        except ValueError as exc:
            output.append({"target_url": str(raw), "state": "INVALID", "last_error": str(exc)})
            continue
        item_id = record_id(target_url)
        if item_id in seen:
            continue
        seen.add(item_id)
        prior = existing.get(item_id)
        if prior and prior.get("state") == "SUBMITTED":
            output.append(dict(prior))
            continue
        try:
            checked = checker(target_url, cfg)
            is_cf = bool(checked.get("cloudflare"))
            error = "" if is_cf else "Không phát hiện nameserver Cloudflare"
        except Exception as exc:
            is_cf = False
            error = _clean_error(exc)
        domain = pt.normalize_domain(target_url)
        draft = pt.generate_cloudflare_report_text(domain, cfg, target_url) if is_cf else ""
        item = prior or {
            "id": item_id, "day": current_day(), "target_url": target_url,
            "domain": domain, "created_at": now, "attempts": 0,
        }
        item.update({
            "cloudflare": is_cf,
            "draft": draft,
            "state": item.get("state") if is_cf and item.get("state") in RETRYABLE_STATES else ("READY" if is_cf else "NOT_CLOUDFLARE"),
            "last_error": error,
            "updated_at": now,
        })
        existing[item_id] = item
        output.append(dict(item))
    data["records"] = list(existing.values())
    save_ledger(data, path)
    return output


class CloudflareBrowserWorker:
    """One visible ephemeral Chrome session controlled by a background thread."""

    def __init__(self, *, ledger_path: Path | str = LEDGER_PATH):
        self.ledger_path = Path(ledger_path)
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._status = "Sẵn sàng"
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True, name="cloudflare-form-worker").start()

    def snapshot(self) -> dict:
        with self._lock:
            return {"busy": self._busy, "status": self._status}

    def start(self, records: list[dict], *, mode: str, delay_seconds: int, confirmed: bool) -> dict:
        if mode not in {"fill_only", "submit"}:
            return {"error": "Chế độ không hợp lệ"}
        if mode == "submit" and not confirmed:
            return {"error": "Bạn phải xác nhận trước khi cho phép submit."}
        with self._lock:
            if self._busy:
                return {"error": "Cloudflare Form Worker đang chạy."}
            self._busy = True
            self._status = f"Đã nhận {len(records)} URL"
        self._queue.put((records, mode, max(0, int(delay_seconds))))
        return {"status": "started", "count": len(records)}

    def _run(self) -> None:
        while True:
            records, mode, delay = self._queue.get()
            try:
                self._process(records, mode, delay)
            except Exception as exc:
                with self._lock:
                    self._status = f"Lỗi worker: {_clean_error(exc)}"
            finally:
                with self._lock:
                    self._busy = False

    def _process(self, records: list[dict], mode: str, delay: int) -> None:
        from urllib.parse import urlencode
        from cloudflare_profile_bridge import profile_bridge

        bridge = profile_bridge()
        for index, record in enumerate(records):
            item_id = str(record.get("id") or "")
            if not item_id or record.get("state") == "SUBMITTED":
                continue
            with self._lock:
                self._status = f"Đang mở trên Chrome profile hiện tại: {record.get('target_url')}"
            update_record(item_id, self.ledger_path, state="OPENING_PROFILE", mode=mode,
                          attempts=int(record.get("attempts") or 0) + 1, last_error="")
            payload = dict(record)
            payload["mode"] = mode
            completed = threading.Event()

            def checkpoint(result: dict, current_id=item_id, current_done=completed):
                state = result.get("state")
                if state not in {"FILLED", "SUBMITTED", "NEEDS_MANUAL", "FAILED"}:
                    state = "FAILED"
                update_record(current_id, self.ledger_path, state=state,
                              result=result.get("result", ""), last_error="")
                current_done.set()

            token = bridge.register(payload, checkpoint)
            fragment = urlencode({"ptask": token, "port": bridge.port})
            if not open_in_installed_chrome(f"{FORM_URL}#{fragment}"):
                update_record(item_id, self.ledger_path, state="FAILED",
                              last_error="Không tìm thấy Google Chrome đã cài đặt", result="")
            else:
                wait_seconds = 180 if mode == "submit" else 60
                with self._lock:
                    self._status = f"Đang chờ extension hoàn tất: {record.get('target_url')}"
                if not completed.wait(wait_seconds):
                    current = next((x for x in today_records(self.ledger_path) if x.get("id") == item_id), None)
                    if current and current.get("state") == "OPENING_PROFILE":
                        update_record(
                            item_id, self.ledger_path, state="FAILED",
                            last_error=f"Extension không hoàn tất task trong {wait_seconds} giây. Hãy xem nhãn PhishingTool trên tab và Reload extension v1.0.3.",
                            result="",
                        )
            if index + 1 < len(records) and delay:
                time.sleep(delay)
        with self._lock:
            self._status = "Đã mở danh sách trên Chrome profile hiện tại; extension sẽ điền và checkpoint kết quả."

_BROWSER_WORKER: CloudflareBrowserWorker | None = None
_BROWSER_LOCK = threading.Lock()
_QUICK_TASKS: dict[str, dict] = {}


def browser_worker() -> CloudflareBrowserWorker:
    global _BROWSER_WORKER
    with _BROWSER_LOCK:
        if _BROWSER_WORKER is None:
            _BROWSER_WORKER = CloudflareBrowserWorker()
        return _BROWSER_WORKER


def open_quick_report_form(target_url: str, draft: str, cfg: dict) -> dict:
    """Open one fill-only Cloudflare task in the current Chrome profile.

    Quick Report deliberately has no submit mode, ledger, retry, or batch state.
    """
    from urllib.parse import urlencode
    from cloudflare_profile_bridge import profile_bridge

    bridge = profile_bridge()
    task_status = {"state": "OPENING_PROFILE", "result": ""}

    def checkpoint(result: dict) -> None:
        task_status.update(result)

    payload = {
        "target_url": normalize_target(target_url),
        "draft": str(draft or ""),
        "mode": "fill_only",
        "_contact_name": cfg.get("contact_name", ""),
        "_contact_email": cfg.get("contact_email", ""),
        "_brand_name": cfg.get("brand_name", ""),
    }
    token = bridge.register(payload, checkpoint)
    _QUICK_TASKS[token] = task_status
    fragment = urlencode({"ptask": token, "port": bridge.port})
    if not open_in_installed_chrome(f"{FORM_URL}#{fragment}"):
        _QUICK_TASKS.pop(token, None)
        return {"error": "Không tìm thấy Google Chrome đã cài đặt."}
    return {"status": "opened", "token": token}
