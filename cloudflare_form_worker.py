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
SCHEMA_VERSION = 4
LEDGER_PATH = Path(pt._runtime_path("cloudflare_form_worker.json"))
INPUT_CACHE_PATH = Path(pt._runtime_path("cloudflare_form_input.json"))
JOB_STATUS_PATH = Path(pt._runtime_path("cloudflare_form_job.json"))
TERMINAL_STATES = {"SUBMITTED"}
RETRYABLE_STATES = {"READY", "FAILED", "NEEDS_MANUAL", "FILLED"}
SESSION_BATCH_STATES = {
    "QUEUED", "SUBMITTING", "SUBMITTED", "ALREADY_SUBMITTED", "UNKNOWN", "WAITING_FOR_SESSION",
    "PAUSED", "FAILED", "NEEDS_REVIEW",
}
MAX_BATCH_DELAY_SECONDS = 300
_FILE_LOCK = threading.RLock()
_API_SEND_LOCK = threading.Lock()
_REPLACE_ATTEMPTS = 10


def _replace_file_with_retry(source: Path, destination: Path) -> None:
    """Retry transient Windows sharing violations, then surface persistent failures."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt + 1 >= _REPLACE_ATTEMPTS:
                raise
            time.sleep(0.02 * (attempt + 1))


def current_day() -> str:
    return datetime.now().astimezone().date().isoformat()


def extension_directory() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "chrome_extension" / "cloudflare-profile-worker"


def open_in_installed_chrome(url: str) -> bool:
    """Route a URL to the already-running installed Chrome, never Playwright."""
    import subprocess
    import shutil
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
    elif sys.platform == "darwin":
        candidates.extend([
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ])
    else:
        for name in ("google-chrome", "google-chrome-stable"):
            executable = shutil.which(name)
            if executable:
                candidates.append(Path(executable))
    for executable in candidates:
        if executable.is_file():
            try:
                subprocess.Popen([str(executable), url], close_fds=True)
                return True
            except OSError:
                continue
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
        _replace_file_with_retry(temp, path)


def _atomic_json(path: Path | str, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    with _FILE_LOCK:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _replace_file_with_retry(temp, path)


def load_daily_input(path: Path | str = INPUT_CACHE_PATH) -> str:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    return str(data.get("value") or "") if data.get("day") == current_day() else ""


def save_daily_input(value: str, path: Path | str = INPUT_CACHE_PATH) -> None:
    _atomic_json(path, {
        "day": current_day(), "value": str(value or ""),
        "updated_at": datetime.now().astimezone().isoformat(),
    })


def load_job_status(path: Path | str = JOB_STATUS_PATH) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) and data.get("day") == current_day() else {}


def recover_job_record_ids(
    *, ledger_path: Path | str = LEDGER_PATH,
    job_status_path: Path | str = JOB_STATUS_PATH,
) -> list[str]:
    """Recover membership for jobs created by the pre-record_ids runtime."""
    status = load_job_status(job_status_path)
    existing = [str(value) for value in (status.get("record_ids") or []) if value]
    if existing:
        return existing
    job_id = str(status.get("job_id") or "")
    total = int(status.get("total") or 0)
    if not job_id or total <= 0:
        return []
    try:
        started = datetime.strptime(job_id, "%Y%m%dT%H%M%S%z")
    except ValueError:
        return []
    candidates = []
    for item in today_records(ledger_path):
        try:
            updated = datetime.fromisoformat(str(item.get("updated_at") or ""))
        except ValueError:
            continue
        jobish = (
            item.get("state") in {"QUEUED", "SUBMITTING", "WAITING_FOR_SESSION", "PAUSED"}
            or item.get("channel") == "dashboard_session"
        )
        if jobish and updated >= started:
            candidates.append(item)
    candidates.sort(key=lambda item: str(item.get("updated_at") or ""))
    recovered = [str(item.get("id") or "") for item in candidates[:total] if item.get("id")]
    if not recovered:
        return []
    status["record_ids"] = recovered
    _atomic_json(job_status_path, status)
    for item_id in recovered:
        update_record(item_id, ledger_path, job_id=job_id)
    return recovered


def clear_daily_cache(
    *, ledger_path: Path | str = LEDGER_PATH,
    input_path: Path | str = INPUT_CACHE_PATH,
) -> int:
    """Clear today's editable/check cache while preserving delivery truth."""
    data = load_ledger(ledger_path)
    protected = {"SUBMITTED", "ALREADY_SUBMITTED", "UNKNOWN", "SUBMITTING", "WAITING_FOR_SESSION"}
    before = len(data["records"])
    data["records"] = [
        item for item in data["records"]
        if item.get("day") != current_day() or item.get("state") in protected
    ]
    save_ledger(data, ledger_path)
    save_daily_input("", input_path)
    return before - len(data["records"])


def today_records(path: Path | str = LEDGER_PATH) -> list[dict]:
    day = current_day()
    return [dict(item) for item in load_ledger(path)["records"] if item.get("day") == day]


def _clean_error(value: object) -> str:
    text = " ".join(str(value or "").split())
    # Avoid writing query-string tokens accidentally returned by a browser error.
    text = re.sub(r"([?&](?:token|key|captcha|cf-turnstile-response)=)[^&\s]+", r"\1[REDACTED]", text, flags=re.I)
    text = re.sub(r"(?i)((?:x-atok|cookie)\s*[:=]\s*)[^\r\n]+", r"\1[REDACTED]", text)
    return text[:500]


def report_version(draft: str) -> str:
    return hashlib.sha256(str(draft or "").encode("utf-8")).hexdigest()[:16]


def idempotency_key(target_url: str, draft: str) -> str:
    raw = f"{normalize_target(target_url)}\0{report_version(draft)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def update_record(item_id: str, path: Path | str = LEDGER_PATH, **changes) -> dict | None:
    data = load_ledger(path)
    found = None
    for item in data["records"]:
        if item.get("id") == item_id:
            allowed = {
                "state", "attempts", "last_error", "updated_at", "mode", "result",
                "channel", "report_id", "http_status", "submitted_at",
                "job_id",
            }
            for key, value in changes.items():
                if key in allowed:
                    item[key] = _clean_error(value) if key == "last_error" else value
            item["updated_at"] = datetime.now().astimezone().isoformat()
            found = dict(item)
            break
    if found:
        save_ledger(data, path)
    return found


def recover_interrupted_dashboard_records(path: Path | str = LEDGER_PATH) -> int:
    """Fail closed after process restart: an in-flight POST has unknown outcome."""
    data = load_ledger(path)
    changed = 0
    now = datetime.now().astimezone().isoformat()
    for item in data["records"]:
        if item.get("channel") == "dashboard_session" and item.get("state") == "SUBMITTING":
            item["state"] = "UNKNOWN"
            item["last_error"] = (
                "Worker khởi động lại khi request đang gửi; cần đối chiếu Cloudflare trước khi gửi lại."
            )
            item["updated_at"] = now
            changed += 1
    if changed:
        save_ledger(data, path)
    return changed


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
        if prior:
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
            "report_version": report_version(draft) if draft else "",
            "idempotency_key": idempotency_key(target_url, draft) if draft else "",
            "state": (
                item.get("state")
                if is_cf and item.get("state") in (RETRYABLE_STATES | SESSION_BATCH_STATES)
                else ("READY" if is_cf and draft.strip() else ("NEEDS_REVIEW" if is_cf else "NOT_CLOUDFLARE"))
            ),
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


class CloudflareSessionBatchWorker:
    """Sequential Dashboard-session batch with pause/resume/stop controls.

    The cookie value only lives on this object while a batch is active. It is
    never placed in the work queue, ledger, snapshot, exception text, or result.
    """

    def __init__(
        self, *, ledger_path: Path | str = LEDGER_PATH,
        job_status_path: Path | str = JOB_STATUS_PATH, submitter=None,
    ):
        self.ledger_path = Path(ledger_path)
        requested_status_path = Path(job_status_path)
        self.job_status_path = (
            self.ledger_path.with_name("cloudflare_form_job.json")
            if requested_status_path == JOB_STATUS_PATH and self.ledger_path != LEDGER_PATH
            else requested_status_path
        )
        recover_interrupted_dashboard_records(self.ledger_path)
        self._submitter = submitter
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop_requested = False
        self._thread: threading.Thread | None = None
        self._cookie_value = ""
        self._records: list[dict] = []
        self._cfg: dict = {}
        self._delay = 0
        self._status = "Sẵn sàng"
        self._state = "IDLE"
        self._current_id = ""
        self._processed = 0
        self._total = 0
        self._job_id = ""
        self._job_record_ids: list[str] = []
        interrupted = load_job_status(self.job_status_path)
        if interrupted.get("state") in {"RUNNING", "STOPPING"}:
            interrupted.update({
                "state": "INTERRUPTED",
                "status": "Ứng dụng đã khởi động lại; các URL chưa gửi có thể chạy lại.",
                "updated_at": datetime.now().astimezone().isoformat(),
            })
            _atomic_json(self.job_status_path, interrupted)

    def _persist_status(self) -> None:
        _atomic_json(self.job_status_path, {
            "version": 1,
            "day": current_day(),
            "job_id": self._job_id,
            "record_ids": list(self._job_record_ids),
            "state": self._state,
            "status": self._status,
            "current_id": self._current_id,
            "processed": self._processed,
            "total": self._total,
            "delay_seconds": self._delay,
            "updated_at": datetime.now().astimezone().isoformat(),
        })

    def snapshot(self) -> dict:
        with self._lock:
            live = {
                "busy": bool(self._thread and self._thread.is_alive()),
                "job_id": self._job_id,
                "state": self._state,
                "status": self._status,
                "current_id": self._current_id,
                "processed": self._processed,
                "total": self._total,
                "delay_seconds": self._delay,
                "record_ids": list(self._job_record_ids),
            }
            if live["busy"] or self._job_id:
                return live
        saved = load_job_status(self.job_status_path)
        if saved:
            saved["busy"] = False
            return saved
        return live

    def start(
        self,
        records: list[dict],
        cfg: dict,
        *,
        cookie_value: str,
        delay_seconds: int,
        confirmed: bool,
    ) -> dict:
        cookie_value = str(cookie_value or "").strip()
        if not confirmed:
            return {"error": "Bạn phải xác nhận preview trước khi gửi."}
        if not cookie_value:
            return {"error": "Chưa nhập Cookie của phiên Cloudflare."}
        try:
            delay = int(delay_seconds)
        except (TypeError, ValueError):
            return {"error": "Giãn cách phải là số nguyên."}
        if delay < 0 or delay > MAX_BATCH_DELAY_SECONDS:
            return {"error": f"Giãn cách phải từ 0 đến {MAX_BATCH_DELAY_SECONDS} giây."}

        latest = {item.get("id"): item for item in today_records(self.ledger_path)}
        prepared = []
        submitted_keys = {
            item.get("idempotency_key") for item in load_ledger(self.ledger_path)["records"]
            if item.get("state") == "SUBMITTED" and item.get("idempotency_key")
        }
        for source in records:
            item = latest.get(source.get("id"), source)
            if not item.get("id") or item.get("state") in {"SUBMITTED", "ALREADY_SUBMITTED", "UNKNOWN"}:
                continue
            if not str(item.get("draft") or "").strip():
                update_record(item["id"], self.ledger_path, state="NEEDS_REVIEW",
                              last_error="Thiếu nội dung report đã sinh.")
                continue
            if item.get("idempotency_key") in submitted_keys:
                update_record(item["id"], self.ledger_path, state="SUBMITTED",
                              result="already_submitted", channel="dashboard_session")
                continue
            prepared.append(dict(item))
        if not prepared:
            return {"error": "Không có report đủ điều kiện để gửi."}

        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"error": "Đang có một batch Cloudflare dùng phiên thủ công."}
            self._records = prepared
            self._cfg = {
                key: cfg.get(key, "") for key in (
                    "cloudflare_account_id", "cloudflare_reported_country",
                    "contact_email", "contact_name", "brand_name",
                )
            }
            self._delay = delay
            self._cookie_value = cookie_value
            self._stop_requested = False
            self._processed = 0
            self._total = len(prepared)
            self._current_id = ""
            self._job_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
            prepared_ids = [str(item.get("id") or "") for item in prepared]
            previous_ids = [
                str(value) for value in (load_job_status(self.job_status_path).get("record_ids") or [])
                if value
            ]
            # A retry/resume remains the same visible job, like Domain Worker:
            # successful rows stay in the progress table while failed/pending rows rerun.
            if set(prepared_ids) & set(previous_ids):
                self._job_record_ids = list(dict.fromkeys([*previous_ids, *prepared_ids]))
            else:
                self._job_record_ids = prepared_ids
            self._state = "RUNNING"
            self._status = f"Đã nhận {len(prepared)} report"
            for item in prepared:
                update_record(
                    item["id"], self.ledger_path, state="QUEUED", last_error="",
                    job_id=self._job_id,
                )
            self._persist_status()
            self._wake.set()
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="cloudflare-session-batch"
            )
            self._thread.start()
        return {"status": "started", "count": len(prepared)}

    def pause(self) -> dict:
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                return {"error": "Không có batch đang chạy."}
            self._state = "PAUSED"
            self._status = "Đã tạm dừng; không bắt đầu submit mới."
            self._wake.clear()
            self._persist_status()
        return {"status": "paused"}

    def resume(self, *, cookie_value: str = "") -> dict:
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                return {"error": "Không có batch để tiếp tục."}
            if cookie_value:
                self._cookie_value = str(cookie_value).strip()
            if self._state == "WAITING_FOR_SESSION" and not self._cookie_value:
                return {"error": "Hãy nhập Cookie Cloudflare mới trước khi tiếp tục."}
            self._state = "RUNNING"
            self._status = "Đang tiếp tục batch."
            self._wake.set()
            self._persist_status()
        return {"status": "running"}

    def stop(self) -> dict:
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                return {"error": "Không có batch đang chạy."}
            self._stop_requested = True
            self._state = "STOPPING"
            self._status = "Đang dừng sau thao tác hiện tại."
            self._wake.set()
            self._persist_status()
        return {"status": "stopping"}

    def _wait_until_runnable(self) -> bool:
        while True:
            with self._lock:
                if self._stop_requested:
                    return False
                runnable = self._state == "RUNNING"
            if runnable:
                return True
            self._wake.wait(0.25)

    def _interruptible_delay(self) -> bool:
        deadline = time.monotonic() + self._delay
        while time.monotonic() < deadline:
            with self._lock:
                if self._stop_requested:
                    return False
            if not self._wait_until_runnable():
                return False
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
        return True

    def _run(self) -> None:
        from cloudflare_dashboard_session import (
            CloudflareDashboardError,
            CloudflareDashboardDuplicateError,
            CloudflareDashboardRateLimitError,
            CloudflareDashboardSessionError,
            CloudflareDashboardUnknownError,
            submit_dashboard_report,
        )

        submitter = self._submitter or submit_dashboard_report
        try:
            for index, source in enumerate(self._records):
                if not self._wait_until_runnable():
                    break
                latest = next(
                    (item for item in today_records(self.ledger_path)
                     if item.get("id") == source.get("id")), source,
                )
                if latest.get("state") in {"SUBMITTED", "ALREADY_SUBMITTED", "UNKNOWN"}:
                    with self._lock:
                        self._processed += 1
                    continue
                item_id = str(latest.get("id") or "")
                with self._lock:
                    self._current_id = item_id
                    self._status = f"Đang gửi {latest.get('target_url')}"
                    cookie_value = self._cookie_value
                    self._persist_status()
                update_record(
                    item_id, self.ledger_path, state="SUBMITTING",
                    mode="dashboard_session", channel="dashboard_session",
                    attempts=int(latest.get("attempts") or 0) + 1, last_error="",
                )
                should_count = True
                try:
                    sent = submitter(
                        latest["target_url"], latest.get("draft") or "",
                        self._cfg, cookie_value,
                    )
                    update_record(
                        item_id, self.ledger_path, state="SUBMITTED",
                        mode="dashboard_session", channel="dashboard_session",
                        result=sent.get("result") or "success",
                        report_id=sent.get("report_id") or "",
                        http_status=sent.get("http_status") or 200,
                        submitted_at=datetime.now().astimezone().isoformat(),
                        last_error="",
                    )
                except CloudflareDashboardSessionError as exc:
                    message = _clean_error(exc)
                    if cookie_value:
                        message = message.replace(cookie_value, "[REDACTED]")
                    update_record(item_id, self.ledger_path, state="WAITING_FOR_SESSION",
                                  last_error=message, result="")
                    with self._lock:
                        self._cookie_value = ""
                        self._state = "WAITING_FOR_SESSION"
                        self._status = message
                        self._wake.clear()
                        self._persist_status()
                    should_count = False
                    if not self._wait_until_runnable():
                        break
                    update_record(item_id, self.ledger_path, state="QUEUED", last_error="")
                    source["state"] = "QUEUED"
                    # Retry this same item only after the operator supplied a new session.
                    self._records.insert(index + 1, source)
                except CloudflareDashboardDuplicateError as exc:
                    message = _clean_error(exc)
                    update_record(
                        item_id, self.ledger_path, state="ALREADY_SUBMITTED",
                        result="dedupe", last_error=message, http_status=200,
                    )
                except CloudflareDashboardRateLimitError as exc:
                    message = _clean_error(exc)
                    update_record(item_id, self.ledger_path, state="PAUSED", last_error=message)
                    with self._lock:
                        self._state = "PAUSED"
                        self._status = message
                        self._wake.clear()
                        self._persist_status()
                    should_count = False
                    if not self._wait_until_runnable():
                        break
                    update_record(item_id, self.ledger_path, state="QUEUED", last_error="")
                    source["state"] = "QUEUED"
                    self._records.insert(index + 1, source)
                except CloudflareDashboardUnknownError as exc:
                    message = _clean_error(exc)
                    update_record(item_id, self.ledger_path, state="UNKNOWN", last_error=message)
                    with self._lock:
                        self._state = "PAUSED"
                        self._status = message
                        self._wake.clear()
                        self._persist_status()
                    if not self._wait_until_runnable():
                        break
                except CloudflareDashboardError as exc:
                    message = _clean_error(exc)
                    if cookie_value:
                        message = message.replace(cookie_value, "[REDACTED]")
                    update_record(item_id, self.ledger_path, state="FAILED",
                                  last_error=message, result="")
                except Exception as exc:
                    message = _clean_error(exc)
                    if cookie_value:
                        message = message.replace(cookie_value, "[REDACTED]")
                    update_record(item_id, self.ledger_path, state="UNKNOWN",
                                  last_error=message or "Không xác định được kết quả gửi.")
                    with self._lock:
                        self._state = "PAUSED"
                        self._status = "Không xác định được kết quả; cần đối chiếu trước khi tiếp tục."
                        self._wake.clear()
                        self._persist_status()
                    if not self._wait_until_runnable():
                        break
                if should_count:
                    with self._lock:
                        self._processed += 1
                        self._persist_status()
                if index + 1 < len(self._records) and self._delay:
                    with self._lock:
                        self._status = f"Chờ {self._delay} giây trước report tiếp theo."
                    if not self._interruptible_delay():
                        break
        finally:
            with self._lock:
                stopped = self._stop_requested
                self._cookie_value = ""
                self._cfg = {}
                self._records = []
                self._current_id = ""
                self._state = "STOPPED" if stopped else "COMPLETED"
                self._status = "Đã dừng batch." if stopped else "Batch đã kết thúc."
                self._wake.set()
                self._persist_status()


_SESSION_BATCH_WORKER: CloudflareSessionBatchWorker | None = None
_SESSION_BATCH_LOCK = threading.Lock()


def session_batch_worker() -> CloudflareSessionBatchWorker:
    global _SESSION_BATCH_WORKER
    with _SESSION_BATCH_LOCK:
        if _SESSION_BATCH_WORKER is None:
            _SESSION_BATCH_WORKER = CloudflareSessionBatchWorker()
        return _SESSION_BATCH_WORKER


def open_quick_report_form(target_url: str, draft: str, cfg: dict) -> dict:
    """Open one fill-only Cloudflare task in the current Chrome profile.

    Quick Report deliberately has no submit mode, ledger, retry, or batch state.
    """
    return open_profile_form("cloudflare", FORM_URL, target_url, draft, cfg)


def _submit_api_records_unlocked(records: list[dict], cfg: dict, *,
                                 path: Path | str = LEDGER_PATH,
                                 submitter=None) -> list[dict]:
    """Submit selected Cloudflare records via API and checkpoint every result."""
    from cloudflare_abuse_api import CloudflareAbuseApiError, submit_phishing_report

    submitter = submitter or submit_phishing_report
    results = []
    for record in records:
        item_id = str(record.get("id") or "")
        if not item_id or record.get("state") == "SUBMITTED":
            continue
        attempts = int(record.get("attempts") or 0) + 1
        update_record(
            item_id, path, state="API_SENDING", mode="api", channel="api",
            attempts=attempts, last_error="",
        )
        try:
            sent = submitter(record["target_url"], record.get("draft") or "", cfg)
            update_record(
                item_id, path, state="SUBMITTED", mode="api", channel="api",
                result=sent.get("result") or "success",
                report_id=sent.get("report_id") or "",
                http_status=sent.get("http_status") or 200,
                last_error="",
            )
            results.append({"id": item_id, "ok": True, **sent})
        except CloudflareAbuseApiError as exc:
            message = _clean_error(exc)
            secret = str(cfg.get("cloudflare_api_token") or "")
            if secret:
                message = message.replace(secret, "[REDACTED]")
            update_record(
                item_id, path, state="FAILED", mode="api", channel="api",
                last_error=message, result="",
            )
            results.append({"id": item_id, "ok": False, "error": message})
        except Exception as exc:
            message = _clean_error(exc)
            secret = str(cfg.get("cloudflare_api_token") or "")
            if secret:
                message = message.replace(secret, "[REDACTED]")
            update_record(
                item_id, path, state="FAILED", mode="api", channel="api",
                last_error=message, result="",
            )
            results.append({"id": item_id, "ok": False, "error": message})
    return results


def submit_api_records(records: list[dict], cfg: dict, *,
                       path: Path | str = LEDGER_PATH,
                       submitter=None) -> list[dict]:
    """Serialize API delivery so two local Streamlit sessions cannot double-send."""
    with _API_SEND_LOCK:
        latest = {item.get("id"): item for item in today_records(path)}
        pending = [
            latest.get(item.get("id"), item)
            for item in records
            if latest.get(item.get("id"), item).get("state") != "SUBMITTED"
        ]
        return _submit_api_records_unlocked(
            pending, cfg, path=path, submitter=submitter
        )


def open_profile_form(provider: str, form_url: str, target_url: str, draft: str,
                      cfg: dict, **provider_fields) -> dict:
    """Open one fill-only provider task in the current Chrome profile."""
    from urllib.parse import urlencode
    from cloudflare_profile_bridge import profile_bridge

    if provider not in {
        "cloudflare", "google_gsb", "microsoft_smartscreen",
        "chongluadao", "coccoc_safe", "godaddy_phishing",
        "registry_co_phishing", "xyz_registry_abuse",
    }:
        return {"error": "Provider form không được hỗ trợ."}

    bridge = profile_bridge()
    task_status = {"state": "OPENING_PROFILE", "result": ""}

    def checkpoint(result: dict) -> None:
        task_status.update(result)

    payload = {
        "target_url": normalize_target(target_url),
        "draft": str(draft or ""),
        "provider": provider,
        "mode": "fill_only",
        "_contact_name": cfg.get("contact_name", ""),
        "_contact_email": cfg.get("contact_email", ""),
        "_brand_name": cfg.get("brand_name", ""),
    }
    payload.update({key: str(value or "") for key, value in provider_fields.items()
                    if key in {"threat_type", "threat_category", "language", "report_type"}})
    token = bridge.register(payload, checkpoint)
    _QUICK_TASKS[token] = task_status
    fragment = urlencode({"ptask": token, "port": bridge.port})
    if not open_in_installed_chrome(f"{form_url}#{fragment}"):
        _QUICK_TASKS.pop(token, None)
        return {"error": "Không tìm thấy Google Chrome đã cài đặt."}
    return {"status": "opened", "token": token}
