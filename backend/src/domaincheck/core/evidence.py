"""Capture adapter and safe metadata projection for Check Domain evidence."""
from __future__ import annotations

import importlib
import json
import tempfile
from pathlib import Path
from typing import Protocol

from domaincheck.ports.artifacts import ImageUpload


class CaptureFailed(RuntimeError):
    def __init__(self, reason: str, *, terminal: bool = False):
        super().__init__(reason)
        self.terminal = terminal


def capture_failure_code(error) -> str:
    """Project exceptions/legacy failure details to fixed, non-secret diagnostics."""
    message = str(error).lower()
    known = {"egress_request_denied", "invalid_capture_artifacts", "capture_manifest_outside_staging",
             "capture_path_outside_staging", "capture_manifest_too_large", "terminal_page",
             "browser_runtime_missing", "browser_executable_missing", "browser_timeout", "browser_navigation_failed"}
    if message in known:
        return message
    if "playwright" in message and any(term in message for term in ("no module named", "chưa cài")):
        return "browser_runtime_missing"
    if "executable doesn't exist" in message or "playwright install" in message:
        return "browser_executable_missing"
    if "timeout" in message or "timed out" in message:
        return "browser_timeout"
    if "err_" in message or "net::" in message:
        return "browser_navigation_failed"
    return "capture_failed"


class BrowserCaptureAdapter(Protocol):
    def capture(self, target: str, *, staging_root: Path) -> tuple[list[ImageUpload], dict]: ...


class LegacyBrowserCaptureAdapter:
    """Use legacy passive/DOM logic in an isolated temporary directory."""
    def __init__(self, *, browser_proxy: dict | None = None) -> None:
        self._browser_proxy = browser_proxy

    @staticmethod
    def validate_runtime() -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Install the backend package with Playwright in the worker Python environment") from exc
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, timeout=15_000)
                browser.close()
        except Exception as exc:
            raise RuntimeError("Chromium could not start; run python -m playwright install chromium in the worker environment") from exc

    def capture(self, target: str, *, staging_root: Path) -> tuple[list[ImageUpload], dict]:
        browser = importlib.import_module("browser_evidence")
        options = {"headless": True}
        if self._browser_proxy is not None:
            options["browser_proxy"] = self._browser_proxy
        with tempfile.TemporaryDirectory(prefix="capture-", dir=staging_root) as directory:
            raw = browser.capture_normal_report_evidence(target, directory, **options)
            if not isinstance(raw, dict):
                raise CaptureFailed("capture_failed")
            if not raw.get("success"):
                raise CaptureFailed("terminal_page" if raw.get("terminal") else capture_failure_code(
                    " ".join(str(item.get("error") or "") for item in
                             (raw, raw.get("passive_attempt") or {}, raw.get("dom_destination_attempt") or {}))),
                                    terminal=bool(raw.get("terminal")))
            validation = browser.validate_evidence_artifacts(raw)
            if not validation["valid"]:
                raise CaptureFailed("invalid_capture_artifacts")
            paths = raw.get("screenshot_paths") or [raw.get("screenshot_path")]
            root = Path(directory).resolve()
            if not Path(raw.get("manifest_path") or "").resolve(strict=True).is_relative_to(root):
                raise CaptureFailed("capture_manifest_outside_staging")
            uploads = []
            for index, value in enumerate(paths):
                path = Path(value).resolve(strict=True)
                if not path.is_relative_to(root):
                    raise CaptureFailed("capture_path_outside_staging")
                data = path.read_bytes()
                content_type = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
                uploads.append(ImageUpload(f"capture-{index + 1}.png" if content_type == "image/png"
                                           else f"capture-{index + 1}.jpg", content_type, data))
            manifest = capture_manifest(raw, validation["manifest"])
            return uploads, manifest


def capture_manifest(result: dict, legacy_manifest: dict) -> dict:
    """Retain factual narrative inputs, excluding local paths, HTML and raw errors."""
    browser = importlib.import_module("browser_evidence")
    urls = ("requested_url", "landing_url", "final_url")
    manifest = {key: browser.redact_url(str(result.get(key) or legacy_manifest.get(key) or "")) for key in urls}
    manifest.update({
        "version": 1,
        "source": "browser_capture",
        "evidence_type": str(legacy_manifest.get("evidence_type") or "")[:80],
        "capture_strategy": str(result.get("capture_strategy") or "")[:80],
        "navigation_verified": bool(legacy_manifest.get("navigation_verified")),
        "destination_opened": bool(legacy_manifest.get("destination_opened")),
        "terminal": False,
    })
    control = legacy_manifest.get('control')
    if isinstance(control, dict):
        manifest['control'] = {'found': bool(control.get('found')),
            'label': browser.redact_text(str(control.get('label') or ''))[:300],
            'tag_name': str(control.get('tag_name') or '')[:20],
            **{key: browser.redact_url(str(control.get(key) or ''))[:8192]
               for key in ('raw_href', 'resolved_destination')}}
    navigation = legacy_manifest.get('navigation')
    if isinstance(navigation, dict):
        redirects = []
        for hop in (navigation.get('redirect_chain') or [])[:20]:
            if isinstance(hop, dict) and isinstance(hop.get('status'), int) and 300 <= hop['status'] < 400:
                redirects.append({'status': hop['status'], **{key: browser.redact_url(str(hop.get(key) or ''))[:8192]
                                                           for key in ('url', 'location')}})
        manifest['navigation'] = {'mode': str(navigation.get('mode') or '')[:40],
            'referrer_sent': bool(navigation.get('referrer_sent')), 'redirect_chain': redirects,
            **{key: browser.redact_url(str(navigation.get(key) or ''))[:8192]
               for key in ('source_url', 'requested_destination', 'destination_url')}}
    if 'page_signals' in legacy_manifest:
        manifest['page_signals'] = browser._sanitize_page_signals(legacy_manifest['page_signals'])
    for key in ('page', 'destination_page'):
        if isinstance(legacy_manifest.get(key), dict):
            manifest[key] = {'title': browser.redact_text(str(legacy_manifest[key].get('title') or ''))[:500]}
    if legacy_manifest.get('observed_at'):
        manifest['observed_at'] = str(legacy_manifest['observed_at'])[:80]
    if len(json.dumps(manifest).encode("utf-8")) > 16_384:
        raise CaptureFailed("capture_manifest_too_large")
    return manifest

