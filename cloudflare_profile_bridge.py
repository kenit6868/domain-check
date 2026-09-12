"""Local one-time task bridge for the Cloudflare Chrome profile extension."""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class ProfileBridge:
    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: dict[str, dict] = {}
        self.extension_seen = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = int(self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, daemon=True, name="cf-profile-bridge").start()

    def _handler(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _reply(self, status: int, body: dict):
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.end_headers()

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/hello":
                    bridge.extension_seen = True
                    self._reply(200, {"ok": True})
                    return
                token = parsed.path.removeprefix("/task/")
                with bridge._lock:
                    task = bridge._tasks.get(token)
                public_task = {key: value for key, value in (task or {}).items() if not key.startswith("_")}
                self._reply(200 if task else 404, public_task or {"error": "task_not_found"})

            def do_POST(self):
                token = urlparse(self.path).path.removeprefix("/result/")
                try:
                    size = min(int(self.headers.get("Content-Length", "0")), 16_384)
                    result = json.loads(self.rfile.read(size) or b"{}")
                except (ValueError, TypeError):
                    self._reply(400, {"error": "invalid_result"})
                    return
                with bridge._lock:
                    task = bridge._tasks.get(token)
                    callback = task.get("_callback") if task else None
                if not task or not callback:
                    self._reply(404, {"error": "task_not_found"})
                    return
                callback({
                    "state": str(result.get("state") or "FAILED")[:40],
                    "result": " ".join(str(result.get("result") or "").split())[:500],
                })
                self._reply(200, {"ok": True})

        return Handler

    def register(self, payload: dict, callback) -> str:
        token = secrets.token_urlsafe(24)
        provider = str(payload.get("provider") or "cloudflare")
        if provider not in {
            "cloudflare", "google_gsb", "microsoft_smartscreen",
            "chongluadao", "coccoc_safe", "godaddy_phishing",
        }:
            provider = "cloudflare"
        safe = {
            "target_url": str(payload.get("target_url") or ""),
            "draft": str(payload.get("draft") or ""),
            "contact_name": str(payload.get("_contact_name") or ""),
            "contact_email": str(payload.get("_contact_email") or ""),
            "brand_name": str(payload.get("_brand_name") or ""),
            "provider": provider,
            "threat_type": str(payload.get("threat_type") or ""),
            "threat_category": str(payload.get("threat_category") or ""),
            "language": str(payload.get("language") or ""),
            "report_type": str(payload.get("report_type") or ""),
            "mode": str(payload.get("mode") or "fill_only"),
            "_callback": callback,
        }
        with self._lock:
            self._tasks[token] = safe
        return token


_BRIDGE: ProfileBridge | None = None
_LOCK = threading.Lock()


def profile_bridge() -> ProfileBridge:
    global _BRIDGE
    with _LOCK:
        if _BRIDGE is None:
            _BRIDGE = ProfileBridge()
        return _BRIDGE
