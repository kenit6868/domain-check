"""Passive browser evidence shared by takedown workflows.

Phase 1 deliberately does not click, type, submit forms, or verify a navigation
caused by a DOM control.  It records the requested URL, the landing URL observed
after page load, the HTTP redirect chain, and a visible Register/Login-like DOM
control with its resolved destination.  A PNG and JSON manifest are written as
one evidence set.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests


EVIDENCE_VERSION = 1
DEFAULT_PROFILE = "desktop_direct"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024
_SENSITIVE_QUERY_KEYS = {
    "access_token", "apikey", "api_key", "auth", "authorization", "key",
    "password", "proxy_password", "secret", "token",
}
_TERMINAL_TERMS = (
    "this site can’t be reached", "this site can't be reached",
    "không thể truy cập trang web này", "server ip address could not be found",
    "dns_probe_finished", "err_name_not_resolved", "err_connection_refused",
    "suspected phishing", "reported for potential phishing",
    "suspected malware", "deceptive site",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_http_url(value: str) -> str:
    value = str(value or "").strip()
    if not value.lower().startswith(("http://", "https://")):
        raise ValueError("URL phải bắt đầu bằng http:// hoặc https://")
    parsed = urlsplit(value)
    if not parsed.hostname:
        raise ValueError("URL không có hostname hợp lệ")
    return value


def redact_url(value: str) -> str:
    """Remove credentials and common secret query values from persisted URLs."""
    value = str(value or "")
    try:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.hostname:
            return value
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port:
            host = f"{host}:{parsed.port}"
        query = urlencode([
            (key, "[REDACTED]" if key.lower() in _SENSITIVE_QUERY_KEYS else val)
            for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        ])
        return urlunsplit((parsed.scheme, host, parsed.path, query, parsed.fragment))
    except (TypeError, ValueError):
        return value


def redact_text(value: str) -> str:
    """Best-effort credential redaction for errors and compact DOM snippets."""
    text = str(value or "")
    text = re.sub(
        r"(?i)\b(https?|socks4|socks5)://[^\s/@:]+:[^\s/@]+@",
        lambda match: match.group(1) + "://[REDACTED]@",
        text,
    )
    text = re.sub(
        r"(?i)((?:[?&]|\b)(?:access_token|api_?key|auth|authorization|key|password|proxy_password|secret|token)=)[^&#\s\"']+",
        r"\1[REDACTED]",
        text,
    )
    return text


def _safe_filename(value: str) -> str:
    host = urlsplit(value).hostname or "evidence"
    return re.sub(r"[^a-zA-Z0-9._-]", "_", host)[:100] or "evidence"


def _safe_component(value: str, fallback: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", str(value or ""))[:80] or fallback


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: str, value: dict) -> None:
    temp_path = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass


def probe_http_redirect_chain(
    target_url: str, *, timeout: float = 15.0, user_agent: str = DEFAULT_USER_AGENT,
) -> dict:
    """Observe server-side redirects without downloading the complete response."""
    requested_url = _ensure_http_url(target_url)
    try:
        response = requests.get(
            requested_url, allow_redirects=True, timeout=timeout,
            headers={"User-Agent": user_agent}, verify=False, stream=True,
        )
        try:
            hops = [
                {
                    "status": int(hop.status_code),
                    "url": redact_url(hop.url),
                    "location": redact_url(hop.headers.get("Location", "")),
                }
                for hop in response.history
            ]
            return {
                "success": True,
                "requested_url": redact_url(requested_url),
                "final_url": redact_url(response.url),
                "final_status": int(response.status_code),
                "redirect_chain": hops,
                "error": "",
            }
        finally:
            response.close()
    except requests.RequestException as exc:
        return {
            "success": False, "requested_url": redact_url(requested_url),
            "final_url": "", "final_status": None, "redirect_chain": [],
            "error": redact_text(exc),
        }


def _terminal_page(title: str, visible_text: str) -> bool:
    sample = f"{title} {visible_text}".lower()[:50_000]
    return any(term in sample for term in _TERMINAL_TERMS)


def format_http_redirect_chain(http_result: dict) -> str:
    """Return a concise, redacted chain suitable for the on-image panel."""
    if not isinstance(http_result, dict) or not http_result.get("success"):
        return "Unavailable"
    parts = []
    for hop in http_result.get("redirect_chain") or []:
        source = redact_url(hop.get("url", ""))
        location = redact_url(hop.get("location", ""))
        parts.append(f"{source} --HTTP {hop.get('status')}--> {location}")
    final_url = redact_url(http_result.get("final_url", ""))
    final_status = http_result.get("final_status")
    if final_url:
        parts.append(f"{final_url} --HTTP {final_status}-->")
    return " | ".join(parts) if parts else "No server-side redirect observed"


def validate_evidence_artifacts(result: dict) -> dict:
    """Validate a PNG/JPEG screenshot and its manifest without changing files."""
    screenshot_path = os.path.abspath(str((result or {}).get("screenshot_path") or ""))
    manifest_path = os.path.abspath(str((result or {}).get("manifest_path") or ""))
    errors = []
    if not os.path.isfile(screenshot_path):
        errors.append("Không tìm thấy screenshot")
    else:
        size = os.path.getsize(screenshot_path)
        with open(screenshot_path, "rb") as handle:
            header = handle.read(12)
        if size <= 0 or size > MAX_SCREENSHOT_BYTES:
            errors.append("Screenshot rỗng hoặc vượt quá 10 MB")
        if not (
            header.startswith(b"\x89PNG\r\n\x1a\n")
            or header.startswith(b"\xff\xd8\xff")
        ):
            errors.append("Screenshot không phải PNG/JPEG hợp lệ")
    manifest = {}
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        if not isinstance(manifest, dict) or manifest.get("version") != EVIDENCE_VERSION:
            errors.append("Manifest evidence không đúng phiên bản")
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.append("Không đọc được manifest evidence")
    if not errors and manifest.get("screenshot", {}).get("sha256") != _sha256(screenshot_path):
        errors.append("Hash screenshot không khớp manifest")
    return {"valid": not errors, "errors": errors, "manifest": manifest}


def evidence_attachment_paths(result: dict | None) -> list[str]:
    """Return the validated PNG + manifest pair in stable send order."""
    if not isinstance(result, dict) or not result.get("success"):
        return []
    validation = validate_evidence_artifacts(result)
    if not validation["valid"]:
        return []
    return [os.path.abspath(result["screenshot_path"]), os.path.abspath(result["manifest_path"])]


def format_email_evidence_block(result: dict | None) -> str:
    """Format factual English evidence without claiming a DOM navigation."""
    validation = validate_evidence_artifacts(result or {})
    if not validation["valid"]:
        return ""
    manifest = validation["manifest"]
    control = manifest.get("control") or {}
    http = manifest.get("http") or {}
    lines = [
        "--- Technical Evidence: Read-only Browser Inspection ---",
        "Evidence type: DOM destination observed (no click or form submission)",
        f"Captured at (UTC): {manifest.get('observed_at', '')}",
        f"Requested URL: {manifest.get('requested_url', '')}",
        f"Landing URL: {manifest.get('landing_url', '')}",
        f"Page title: {(manifest.get('page') or {}).get('title', '')}",
        f"Browser profile: {(manifest.get('profile') or {}).get('name', '')}",
    ]
    if http.get("success"):
        lines.append(f"Server-side redirect chain: {format_http_redirect_chain(http)}")
    if control.get("found"):
        lines.extend([
            f"Observed control: {control.get('label', '')}",
            f"Resolved DOM destination: {control.get('resolved_destination', '')}",
        ])
    else:
        lines.append("Observed control: No matching registration/login control was found")
    lines.extend([
        f"Screenshot SHA-256: {(manifest.get('screenshot') or {}).get('sha256', '')}",
        "The attached screenshot and JSON manifest document this observation.",
        "--- End of Browser Evidence ---",
    ])
    return "\n".join(lines)


def capture_passive_browser_evidence(
    target_url: str,
    evidence_root: str,
    *,
    profile_name: str = DEFAULT_PROFILE,
    user_agent: str = DEFAULT_USER_AGENT,
    viewport: dict | None = None,
    timeout_ms: int = 45_000,
    headless: bool = True,
    http_probe=probe_http_redirect_chain,
) -> dict:
    """Capture read-only DOM evidence and persist a PNG + JSON manifest.

    ``evidence_type`` is always ``dom_observed`` in Phase 1.  The function does
    not claim that navigating the selected control was verified.
    """
    requested_url = _ensure_http_url(target_url)
    os.makedirs(evidence_root, exist_ok=True)
    observed_at = _now()
    http_result = http_probe(requested_url, user_agent=user_agent)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "success": False, "error": "Chưa cài Playwright", "evidence_type": "",
            "requested_url": redact_url(requested_url), "screenshot_path": "",
            "manifest_path": "", "http": http_result,
        }

    browser = None
    context = None
    screenshot_path = ""
    manifest_path = ""
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(
                viewport=viewport or {"width": 1920, "height": 1080},
                user_agent=user_agent, accept_downloads=False,
                service_workers="block", ignore_https_errors=True,
            )
            page = context.new_page()
            page.goto(requested_url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8_000))
            except Exception:
                pass
            page.wait_for_timeout(750)
            snapshot = page.evaluate(
                """
                ({requestedUrl, profileName, observedAt, httpRedirectChain}) => {
                    const wanted = /(đăng\\s*k[ýy]|đăng\\s*nhập|register|sign\\s*up|login|log\\s*in|play\\s*now|chơi\\s*ngay)/i;
                    const esc = value => String(value || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    const nodes = [...document.querySelectorAll('a, button, [role="button"]')];
                    const choices = nodes.map((el, index) => {
                        const label = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim();
                        const rawHref = el.getAttribute('href') || el.getAttribute('data-href') || '';
                        const resolvedHref = el.href || rawHref;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        const visible = rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                        return {el, index, label, rawHref, resolvedHref, visible};
                    }).filter(item => item.visible && wanted.test(item.label));
                    choices.sort((a, b) => Number(Boolean(b.resolvedHref)) - Number(Boolean(a.resolvedHref)) || a.index - b.index);
                    const picked = choices[0] || null;
                    if (picked) {
                        picked.el.scrollIntoView({block: 'center', inline: 'center'});
                        picked.el.style.setProperty('outline', '5px solid #ff1f1f', 'important');
                        picked.el.style.setProperty('outline-offset', '5px', 'important');
                        picked.el.style.setProperty('box-shadow', '0 0 0 8px rgba(255,255,0,.8)', 'important');
                    }
                    const landingUrl = location.href;
                    const panel = document.createElement('section');
                    panel.id = '__browser_evidence_panel';
                    panel.innerHTML = `
                      <div style="font:700 16px Arial;color:#fff;margin-bottom:4px">Read-only browser evidence</div>
                      <div style="font:13px Arial;color:#aab4bf;margin-bottom:14px">No click, typing, or form submission was performed</div>
                      <div style="font:700 13px Arial;color:#ffd54f">Requested URL</div><div style="font:12px Consolas;color:#fff;word-break:break-all;margin-bottom:9px">${esc(requestedUrl)}</div>
                      <div style="font:700 13px Arial;color:#ffd54f">Landing URL</div><div style="font:12px Consolas;color:#fff;word-break:break-all;margin-bottom:9px">${esc(landingUrl)}</div>
                      <div style="font:700 13px Arial;color:#ffd54f">Server-side HTTP redirect chain</div><div style="font:11px Consolas;color:#fff;word-break:break-all;margin-bottom:9px">${esc(httpRedirectChain)}</div>
                      <div style="font:700 13px Arial;color:#ffd54f">Profile / captured UTC</div><div style="font:12px Consolas;color:#fff;margin-bottom:9px">${esc(profileName)} / ${esc(observedAt)}</div>
                      <div style="font:700 13px Arial;color:#ffd54f">Selected control</div><div style="font:14px Arial;color:#fff;margin-bottom:9px">${esc(picked?.label || 'Not found')}</div>
                      <div style="font:700 13px Arial;color:#ffd54f">Resolved DOM destination (not navigation-verified)</div><div style="font:12px Consolas;color:#7ee787;word-break:break-all;margin-bottom:9px">${esc(picked?.resolvedHref || '')}</div>
                      <div style="font:700 13px Arial;color:#ffd54f">DOM element</div><pre style="white-space:pre-wrap;word-break:break-all;font:11px Consolas;color:#fff;background:#252c35;padding:10px">${esc(picked?.el?.outerHTML || '')}</pre>`;
                    panel.style.cssText = 'position:fixed;z-index:2147483647;right:0;top:0;width:42vw;height:100vh;box-sizing:border-box;padding:24px;background:#151a20;border-left:5px solid #ff1f1f;overflow:auto;text-align:left';
                    document.documentElement.appendChild(panel);
                    document.body.style.setProperty('width', '58vw', 'important');
                    document.body.style.setProperty('overflow-x', 'hidden', 'important');
                    return {
                        title: document.title || '', visibleText: (document.body?.innerText || '').slice(0, 50000),
                        landingUrl, controlFound: Boolean(picked), controlLabel: picked?.label || '',
                        rawHref: picked?.rawHref || '', resolvedHref: picked?.resolvedHref || '',
                        domElement: picked?.el?.outerHTML || ''
                    };
                }
                """,
                {
                    "requestedUrl": redact_url(requested_url),
                    "profileName": profile_name,
                    "observedAt": observed_at,
                    "httpRedirectChain": format_http_redirect_chain(http_result),
                },
            )
            if _terminal_page(snapshot.get("title", ""), snapshot.get("visibleText", "")):
                return {
                    "success": False, "terminal": True,
                    "error": "Trang terminal/browser/provider không được dùng làm evidence nội dung",
                    "evidence_type": "", "requested_url": redact_url(requested_url),
                    "landing_url": redact_url(snapshot.get("landingUrl", "")),
                    "screenshot_path": "", "manifest_path": "", "http": http_result,
                }
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            safe_profile = _safe_component(profile_name, DEFAULT_PROFILE)
            base = f"{_safe_filename(requested_url)}_{safe_profile}_{stamp}"
            screenshot_path = os.path.abspath(os.path.join(evidence_root, base + ".png"))
            manifest_path = os.path.abspath(os.path.join(evidence_root, base + ".json"))
            page.screenshot(path=screenshot_path, full_page=False)
            screenshot_size = os.path.getsize(screenshot_path)
            if screenshot_size <= 0 or screenshot_size > MAX_SCREENSHOT_BYTES:
                raise ValueError("Screenshot rỗng hoặc vượt quá 10 MB")
            manifest = {
                "version": EVIDENCE_VERSION,
                "evidence_type": "dom_observed",
                "navigation_verified": False,
                "observed_at": observed_at,
                "profile": {"name": profile_name, "user_agent": user_agent},
                "requested_url": redact_url(requested_url),
                "landing_url": redact_url(snapshot.get("landingUrl", "")),
                "http": http_result,
                "page": {"title": snapshot.get("title", "")},
                "control": {
                    "found": bool(snapshot.get("controlFound")),
                    "label": snapshot.get("controlLabel", ""),
                    "raw_href": redact_url(snapshot.get("rawHref", "")),
                    "resolved_destination": redact_url(snapshot.get("resolvedHref", "")),
                    "dom_element": redact_text(snapshot.get("domElement", ""))[:10_000],
                },
                "screenshot": {
                    "path": screenshot_path,
                    "size": screenshot_size,
                    "sha256": _sha256(screenshot_path),
                },
            }
            _atomic_json(manifest_path, manifest)
            return {
                "success": True, "terminal": False, "error": "",
                "evidence_type": "dom_observed", "navigation_verified": False,
                "requested_url": manifest["requested_url"],
                "landing_url": manifest["landing_url"],
                "resolved_destination": manifest["control"]["resolved_destination"],
                "control_found": manifest["control"]["found"],
                "control_label": manifest["control"]["label"],
                "screenshot_path": screenshot_path, "manifest_path": manifest_path,
                "http": http_result,
            }
    except Exception as exc:
        # Do not leave a half evidence set when screenshot or manifest creation
        # fails. Existing evidence sets have unique names and are untouched.
        for incomplete_path in (manifest_path, screenshot_path):
            if incomplete_path:
                try:
                    os.remove(incomplete_path)
                except FileNotFoundError:
                    pass
        return {
            "success": False, "terminal": False, "error": redact_text(exc),
            "evidence_type": "", "requested_url": redact_url(requested_url),
            "screenshot_path": "", "manifest_path": "", "http": http_result,
        }
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
