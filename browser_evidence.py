"""Browser evidence shared by takedown workflows.

The default capture remains passive: it does not click, type, submit forms, or
claim that a DOM control navigated anywhere.  The opt-in DOM-destination mode
reads a visible HTTP Register/Login-like link, captures the source page, then
opens that observed URL in a separate tab in the same isolated browser context.
It records the resulting URL and writes source/destination screenshots plus a
hash manifest.  Both capture modes redact credentials and reject terminal
browser/provider pages as content evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests


EVIDENCE_VERSION = 1
DEFAULT_PROFILE = "desktop_direct"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024
MAX_MANUAL_SCREENSHOTS = 3
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
        # Fragments may contain one-time tokens and are not sent to the server.
        # They are unnecessary in persisted evidence, so always discard them.
        return urlunsplit((parsed.scheme, host, parsed.path, query, ""))
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


def _sanitize_http_result(value) -> dict:
    """Keep only redacted HTTP probe fields that are safe to persist."""
    if not isinstance(value, dict):
        return {
            "success": False, "requested_url": "", "final_url": "",
            "final_status": None, "redirect_chain": [], "error": "",
        }
    redirects = []
    for hop in value.get("redirect_chain") or []:
        if not isinstance(hop, dict):
            continue
        status = hop.get("status")
        try:
            status = int(status) if status is not None else None
        except (TypeError, ValueError):
            status = None
        redirects.append({
            "status": status,
            "url": redact_url(hop.get("url", "")),
            "location": redact_url(hop.get("location", "")),
        })
    final_status = value.get("final_status")
    try:
        final_status = int(final_status) if final_status is not None else None
    except (TypeError, ValueError):
        final_status = None
    return {
        "success": bool(value.get("success")),
        "requested_url": redact_url(value.get("requested_url", "")),
        "final_url": redact_url(value.get("final_url", "")),
        "final_status": final_status,
        "redirect_chain": redirects,
        "error": redact_text(value.get("error", "")),
    }


def _page_url(page, fallback: str = "") -> str:
    """Read a Playwright page URL while tolerating small test doubles."""
    try:
        value = getattr(page, "url", "")
        if callable(value):
            value = value()
    except Exception:
        value = ""
    return redact_url(value or fallback)


def _page_title(page) -> str:
    """Read a page title without allowing a title probe to break capture."""
    try:
        value = page.title()
    except Exception:
        value = ""
    return redact_text(value)[:500]


def _safe_http_destination(value: str) -> bool:
    """Return True only for an absolute HTTP(S) navigation target."""
    try:
        parsed = urlsplit(str(value or "").strip())
    except (TypeError, ValueError):
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)


def _sanitize_page_signals(value) -> dict:
    """Keep only aggregate field counts; never persist entered field values."""
    value = value if isinstance(value, dict) else {}
    result = {}
    for key in (
        "formCount", "visibleInputCount", "passwordInputs",
        "otpInputs", "paymentInputs", "identityInputs",
    ):
        try:
            result[key] = max(0, min(int(value.get(key) or 0), 10_000))
        except (TypeError, ValueError):
            result[key] = 0
    return result


def _urls_differ(left: str, right: str) -> bool:
    """Compare URLs without treating an empty/hash-only change as a redirect."""
    try:
        left_parts = urlsplit(str(left or ""))
        right_parts = urlsplit(str(right or ""))
        left_key = (left_parts.scheme.lower(), left_parts.netloc.lower(), left_parts.path, left_parts.query)
        right_key = (right_parts.scheme.lower(), right_parts.netloc.lower(), right_parts.path, right_parts.query)
        return bool(left_key[0] and right_key[0]) and left_key != right_key
    except (TypeError, ValueError):
        return bool(left and right and left != right)


def evidence_url_matches(left: str, right: str) -> bool:
    """Compare a current report URL with a redacted manifest requested URL."""
    try:
        left_parts = urlsplit(redact_url(left))
        right_parts = urlsplit(redact_url(right))
        left_key = (
            left_parts.scheme.lower(), left_parts.netloc.lower(),
            left_parts.path or "/", left_parts.query,
        )
        right_key = (
            right_parts.scheme.lower(), right_parts.netloc.lower(),
            right_parts.path or "/", right_parts.query,
        )
        return bool(left_key[0] and right_key[0]) and left_key == right_key
    except (TypeError, ValueError):
        return False


def _format_navigation_redirect_chain(redirects: list[dict], final_url: str) -> str:
    """Format browser redirects using the same redacted representation as HTTP."""
    if not redirects:
        return f"No browser redirect observed; final URL: {redact_url(final_url)}"
    parts = []
    for hop in redirects:
        parts.append(
            f"{redact_url(hop.get('url', ''))} --HTTP {hop.get('status')}--> "
            f"{redact_url(hop.get('location', ''))}"
        )
    parts.append(f"Final URL: {redact_url(final_url)}")
    return " | ".join(parts)


def _record_response_redirect(response, redirects: list[dict]) -> None:
    """Record one redacted document redirect, tolerating Playwright doubles."""
    try:
        request = getattr(response, "request", None)
        if callable(request):
            request = request()
        resource_type = getattr(request, "resource_type", None)
        if callable(resource_type):
            resource_type = resource_type()
        if resource_type and resource_type != "document":
            return
        status = int(getattr(response, "status", 0))
        if not 300 <= status < 400:
            return
        response_url = redact_url(getattr(response, "url", ""))
        headers = getattr(response, "headers", {}) or {}
        location = headers.get("location", "") if hasattr(headers, "get") else ""
        if location:
            location = redact_url(urljoin(response_url, str(location)))
        redirects.append({
            "status": status,
            "url": response_url,
            "location": location,
        })
    except Exception:
        # A telemetry listener must never make the navigation fail.
        return


def _attach_response_redirect_listener(page, redirects: list[dict]) -> None:
    """Collect 3xx document responses emitted by one page, best effort."""
    def on_response(response):
        _record_response_redirect(response, redirects)

    try:
        page.on("response", on_response)
    except Exception:
        return


def _page_state(page) -> dict:
    """Return title/body text for terminal-page detection after navigation."""
    try:
        state = page.evaluate(
            """() => ({
                title: document.title || '',
                visibleText: (document.body?.innerText || '').slice(0, 50000)
            })""",
        )
        if isinstance(state, dict):
            return {
                "title": redact_text(state.get("title", ""))[:500],
                "visibleText": redact_text(state.get("visibleText", ""))[:50_000],
            }
    except Exception:
        pass
    return {"title": _page_title(page), "visibleText": ""}


def _remove_evidence_panel(page) -> None:
    """Remove our screenshot-only overlay from a page."""
    try:
        page.evaluate(
            """() => {
                document.getElementById('__browser_evidence_panel')?.remove();
                document.body?.style.removeProperty('width');
                document.body?.style.removeProperty('overflow-x');
            }""",
        )
    except Exception:
        return


def _add_navigation_panel(page, details: dict, *, after_click: bool) -> None:
    """Render URL/redirect metadata into the screenshot, not just the manifest."""
    try:
        page.evaluate(
            """
            ({details, afterClick}) => {
                const esc = value => String(value || '')
                    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;');
                document.getElementById('__browser_evidence_panel')?.remove();
                const panel = document.createElement('section');
                panel.id = '__browser_evidence_panel';
                const title = afterClick
                    ? 'DOM destination — opened in separate tab'
                    : 'Source page — DOM destination observed';
                const subtitle = afterClick
                    ? 'The observed DOM URL was opened directly; no click or form submission'
                    : 'The highlighted control exposes the destination URL shown below';
                const redirectTitle = afterClick
                    ? 'Browser redirect chain after opening destination'
                    : 'Server-side HTTP redirect chain';
                panel.innerHTML = `
                  <div style="font:700 16px Arial;color:#fff;margin-bottom:4px">${esc(title)}</div>
                  <div style="font:13px Arial;color:#aab4bf;margin-bottom:14px">${esc(subtitle)}</div>
                  <div style="font:700 13px Arial;color:#ffd54f">Reported URL</div>
                  <div style="font:12px Consolas;color:#fff;word-break:break-all;margin-bottom:9px">${esc(details.requestedUrl)}</div>
                  <div style="font:700 13px Arial;color:#ffd54f">Source page URL</div>
                  <div style="font:12px Consolas;color:#fff;word-break:break-all;margin-bottom:9px">${esc(details.beforeUrl)}</div>
                  <div style="font:700 13px Arial;color:#ffd54f">Observed DOM control</div>
                  <div style="font:14px Arial;color:#fff;margin-bottom:9px">${esc(details.controlLabel)}</div>
                  <div style="font:700 13px Arial;color:#ffd54f">DOM href</div>
                  <div style="font:12px Consolas;color:#7ee787;word-break:break-all;margin-bottom:9px">${esc(details.domHref)}</div>
                  <div style="font:700 13px Arial;color:#ffd54f">${esc(redirectTitle)}</div>
                  <div style="font:11px Consolas;color:#fff;word-break:break-all;margin-bottom:9px">${esc(details.redirectChain)}</div>
                  ${afterClick ? `<div style="font:700 13px Arial;color:#ffd54f">Final URL after opening DOM destination</div><div style="font:12px Consolas;color:#7ee787;word-break:break-all;margin-bottom:9px">${esc(details.finalUrl)}</div>` : ''}
                  <div style="font:700 13px Arial;color:#ffd54f">Page title</div>
                  <div style="font:12px Arial;color:#fff;word-break:break-all;margin-bottom:9px">${esc(details.pageTitle)}</div>
                  <div style="font:700 13px Arial;color:#ffd54f">Captured UTC</div>
                  <div style="font:12px Consolas;color:#fff;margin-bottom:9px">${esc(details.observedAt)}</div>
                  ${details.domElement ? `<div style="font:700 13px Arial;color:#ffd54f">DOM element</div><pre style="white-space:pre-wrap;word-break:break-all;font:11px Consolas;color:#fff;background:#252c35;padding:10px">${esc(details.domElement)}</pre>` : ''}`;
                panel.style.cssText = 'position:fixed;z-index:2147483647;right:0;top:0;width:42vw;height:100vh;box-sizing:border-box;padding:24px;background:#151a20;border-left:5px solid #ff1f1f;overflow:auto;text-align:left;pointer-events:none';
                document.documentElement.appendChild(panel);
                document.body?.style.setProperty('width', '58vw', 'important');
                document.body?.style.setProperty('overflow-x', 'hidden', 'important');
            }
            """,
            {"details": details, "afterClick": bool(after_click)},
        )
    except Exception:
        return


def _persist_page_screenshot(page, path: str) -> dict:
    """Write one PNG and return its manifest item."""
    try:
        page.screenshot(path=path, full_page=False)
        size = os.path.getsize(path)
        if size <= 0 or size > MAX_SCREENSHOT_BYTES:
            raise ValueError("Screenshot rỗng hoặc vượt quá 10 MB")
        return {"path": path, "size": size, "sha256": _sha256(path)}
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise


def _inspect_navigation_control(page) -> dict:
    """Find the first visible control exposing a safe HTTP(S) destination."""
    snapshot = page.evaluate(
        r"""
        () => {
            const wanted = /(đăng\s*k[ýy]|đăng\s*nhập|register|sign\s*up|login|log\s*in)/i;
            const nodes = [...document.querySelectorAll('a, button, [role="button"]')];
            const choices = nodes.map((el, index) => {
                const label = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim();
                const rawHref = el.getAttribute('href') || el.getAttribute('data-href') || '';
                let resolvedHref = '';
                try {
                    resolvedHref = new URL(rawHref, document.baseURI).href;
                } catch (_) {
                    resolvedHref = '';
                }
                const tagName = String(el.tagName || '').toUpperCase();
                const type = String(el.getAttribute('type') || '').toLowerCase();
                const inForm = Boolean(el.closest('form'));
                const disabled = Boolean(el.disabled)
                    || el.getAttribute('aria-disabled') === 'true'
                    || el.hasAttribute('download');
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                const visible = rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
                const httpHref = /^https?:\/\//i.test(String(resolvedHref || '').trim());
                const anchor = tagName === 'A';
                const dataHrefButton = tagName === 'BUTTON' && Boolean(el.getAttribute('data-href'))
                    && type !== 'submit' && !inForm;
                const safeNavigation = visible && !disabled && httpHref && (anchor || dataHrefButton);
                return {
                    index, label, rawHref, resolvedHref, tagName, type, inForm, disabled,
                    visible, safeNavigation, domElement: el.outerHTML || ''
                };
            }).filter(item => item.visible && wanted.test(item.label));
            choices.sort((a, b) => Number(b.safeNavigation) - Number(a.safeNavigation) || a.index - b.index);
            const picked = choices[0] || null;
            const inputs = [...document.querySelectorAll('input, textarea, select')].filter(el => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden';
            });
            const inputDescriptor = el => [
                el.getAttribute('type'), el.getAttribute('name'), el.getAttribute('id'),
                el.getAttribute('placeholder'), el.getAttribute('autocomplete'),
                el.getAttribute('aria-label')
            ].filter(Boolean).join(' ').toLowerCase();
            const descriptors = inputs.map(inputDescriptor);
            const passwordInputs = inputs.filter(el => String(el.getAttribute('type') || '').toLowerCase() === 'password').length;
            const otpInputs = descriptors.filter(value => /\b(otp|one[-_ ]?time|verification[-_ ]?code|mã\s*(otp|xác\s*minh))\b/i.test(value)).length;
            const paymentInputs = descriptors.filter(value => /\b(card|credit|debit|cvv|cvc|payment|bank|account[-_ ]?number|thanh\s*toán|ngân\s*hàng)\b/i.test(value)).length;
            const identityInputs = descriptors.filter(value => /\b(email|phone|tel|username|user[-_ ]?name|full[-_ ]?name|họ\s*tên|số\s*điện\s*thoại)\b/i.test(value)).length;
            if (picked) {
                const el = nodes[picked.index];
                el.scrollIntoView({block: 'center', inline: 'center'});
                el.style.setProperty('outline', '5px solid #ff1f1f', 'important');
                el.style.setProperty('outline-offset', '5px', 'important');
                el.style.setProperty('box-shadow', '0 0 0 8px rgba(255,255,0,.8)', 'important');
            }
            return {
                title: document.title || '',
                visibleText: (document.body?.innerText || '').slice(0, 50000),
                controlFound: Boolean(picked),
                safeNavigation: Boolean(picked?.safeNavigation),
                candidateIndex: picked ? picked.index : -1,
                controlLabel: picked?.label || '',
                rawHref: picked?.rawHref || '',
                resolvedHref: picked?.resolvedHref || '',
                tagName: picked?.tagName || '',
                type: picked?.type || '',
                domElement: picked?.domElement || ''
                ,pageSignals: {
                    formCount: document.querySelectorAll('form').length,
                    visibleInputCount: inputs.length,
                    passwordInputs, otpInputs, paymentInputs, identityInputs
                }
            };
        }
        """,
    )
    return snapshot if isinstance(snapshot, dict) else {
        "title": "", "visibleText": "", "controlFound": False,
        "safeNavigation": False, "candidateIndex": -1, "controlLabel": "",
        "rawHref": "", "resolvedHref": "", "tagName": "", "type": "",
        "domElement": "", "pageSignals": {},
    }


def capture_dom_destination_evidence(
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
    """Capture a source page and the HTTP(S) destination exposed by its DOM.

    This mode never clicks the page. It reads a visible Register/Login anchor
    (or a non-submit button exposing ``data-href``), captures the source, and
    opens the resolved URL in a new tab in the same browser context with the
    source URL as referrer. No typing, submission, or download is performed.
    """
    requested_url = _ensure_http_url(target_url)
    os.makedirs(evidence_root, exist_ok=True)
    observed_at = _now()
    try:
        http_result = _sanitize_http_result(
            http_probe(requested_url, user_agent=user_agent),
        )
    except Exception as exc:
        http_result = {
            "success": False, "requested_url": redact_url(requested_url),
            "final_url": "", "final_status": None, "redirect_chain": [],
            "error": redact_text(exc),
        }
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "success": False, "terminal": False, "error": "Chưa cài Playwright",
            "evidence_type": "", "navigation_verified": False,
            "requested_url": redact_url(requested_url), "screenshot_path": "",
            "screenshot_paths": [], "manifest_path": "", "http": http_result,
        }

    browser = None
    context = None
    written: list[str] = []
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
            before_url = _page_url(page, requested_url)
            before_state = _inspect_navigation_control(page)
            if _terminal_page(before_state.get("title", ""), before_state.get("visibleText", "")):
                return {
                    "success": False, "terminal": True,
                    "terminal_stage": "source",
                    "error": "Trang terminal/browser/provider không được dùng làm evidence nội dung",
                    "evidence_type": "", "navigation_verified": False,
                    "requested_url": redact_url(requested_url),
                    "landing_url": before_url, "screenshot_path": "",
                    "screenshot_paths": [], "manifest_path": "", "http": http_result,
                }
            if not before_state.get("controlFound"):
                return {
                    "success": False, "terminal": False,
                    "error": "Không tìm thấy control Register/Login có URL trong DOM",
                    "evidence_type": "", "navigation_verified": False,
                    "requested_url": redact_url(requested_url),
                    "landing_url": before_url, "screenshot_path": "",
                    "screenshot_paths": [], "manifest_path": "", "http": http_result,
                }
            if not before_state.get("safeNavigation"):
                return {
                    "success": False, "terminal": False,
                    "error": "Control không cung cấp anchor HTTP(S) hoặc data-href an toàn để mở",
                    "evidence_type": "", "navigation_verified": False,
                    "requested_url": redact_url(requested_url),
                    "landing_url": before_url, "control_found": True,
                    "control_label": before_state.get("controlLabel", ""),
                    "resolved_destination": redact_url(before_state.get("resolvedHref", "")),
                    "screenshot_path": "", "screenshot_paths": [],
                    "manifest_path": "", "http": http_result,
                }
            destination_url = str(before_state.get("resolvedHref") or "").strip()
            if not _urls_differ(before_url, destination_url):
                return {
                    "success": False, "terminal": False,
                    "error": "URL trong DOM không dẫn tới một địa chỉ HTTP(S) khác trang nguồn",
                    "evidence_type": "", "navigation_verified": False,
                    "requested_url": redact_url(requested_url), "landing_url": before_url,
                    "screenshot_path": "", "screenshot_paths": [],
                    "manifest_path": "", "http": http_result,
                }

            browser_redirects: list[dict] = []

            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            safe_profile = _safe_component(profile_name, DEFAULT_PROFILE)
            base = f"{_safe_filename(requested_url)}_{safe_profile}_{stamp}"
            source_path = os.path.abspath(os.path.join(evidence_root, base + "_source_before.png"))
            destination_path = os.path.abspath(os.path.join(evidence_root, base + "_destination_after.png"))
            manifest_path = os.path.abspath(os.path.join(evidence_root, base + "_dom_destination.json"))

            pre_panel_details = {
                "requestedUrl": redact_url(requested_url),
                "beforeUrl": before_url,
                "controlLabel": before_state.get("controlLabel", ""),
                "domHref": redact_url(before_state.get("resolvedHref", "")),
                "redirectChain": format_http_redirect_chain(http_result),
                "finalUrl": "",
                "pageTitle": before_state.get("title", ""),
                "observedAt": observed_at,
                "domElement": redact_text(before_state.get("domElement", ""))[:10_000],
            }
            _add_navigation_panel(page, pre_panel_details, after_click=False)
            source_item = _persist_page_screenshot(page, source_path)
            written.append(source_path)
            _remove_evidence_panel(page)

            opened_at = _now()
            destination_page = context.new_page()
            _attach_response_redirect_listener(destination_page, browser_redirects)
            raw_source_url = getattr(page, "url", "") or requested_url
            if callable(raw_source_url):
                raw_source_url = raw_source_url()
            destination_page.goto(
                destination_url,
                referer=str(raw_source_url),
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            try:
                destination_page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 8_000))
            except Exception:
                pass
            try:
                destination_page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8_000))
            except Exception:
                pass
            destination_page.wait_for_timeout(750)
            final_url = _page_url(destination_page, "")
            navigation_mode = "new_tab_direct"
            if not final_url or not _safe_http_destination(final_url):
                raise ValueError("URL lấy từ DOM không mở được thành trang HTTP(S) hợp lệ")
            destination_state = _page_state(destination_page)
            if _terminal_page(destination_state.get("title", ""), destination_state.get("visibleText", "")):
                for path in written:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                written.clear()
                return {
                    "success": False, "terminal": True,
                    "terminal_stage": "destination",
                    "error": "Trang đích là terminal/browser/provider page, không dùng làm evidence nội dung",
                    "evidence_type": "", "navigation_verified": False,
                    "requested_url": redact_url(requested_url), "landing_url": before_url,
                    "final_url": final_url, "screenshot_path": "", "screenshot_paths": [],
                    "manifest_path": "", "http": http_result,
                }
            navigation_chain = _format_navigation_redirect_chain(browser_redirects, final_url)
            after_panel_details = {
                **pre_panel_details,
                "redirectChain": navigation_chain,
                "finalUrl": final_url,
                "pageTitle": destination_state.get("title", ""),
                "observedAt": opened_at,
            }
            _add_navigation_panel(destination_page, after_panel_details, after_click=True)
            destination_item = _persist_page_screenshot(destination_page, destination_path)
            written.append(destination_path)
            screenshots = [
                {"role": "source_before_open", **source_item},
                {"role": "destination_after_open", **destination_item},
            ]
            manifest = {
                "version": EVIDENCE_VERSION,
                "evidence_type": "dom_destination_opened",
                "navigation_verified": False,
                "destination_opened": True,
                "observed_at": observed_at,
                "profile": {"name": profile_name, "user_agent": user_agent},
                "requested_url": redact_url(requested_url),
                "landing_url": before_url,
                "final_url": final_url,
                "http": http_result,
                "navigation": {
                    "mode": navigation_mode,
                    "opened_at": opened_at,
                    "source_url": before_url,
                    "requested_destination": redact_url(destination_url),
                    "destination_url": final_url,
                    "redirect_chain": browser_redirects,
                    "referrer_sent": True,
                },
                "page": {"title": before_state.get("title", "")},
                "page_signals": _sanitize_page_signals(before_state.get("pageSignals")),
                "destination_page": {"title": destination_state.get("title", "")},
                "control": {
                    "found": True,
                    "label": before_state.get("controlLabel", ""),
                    "tag_name": before_state.get("tagName", ""),
                    "raw_href": redact_url(before_state.get("rawHref", "")),
                    "resolved_destination": redact_url(before_state.get("resolvedHref", "")),
                    "dom_element": redact_text(before_state.get("domElement", ""))[:10_000],
                },
                "screenshots": screenshots,
            }
            _atomic_json(manifest_path, manifest)
            written.append(manifest_path)
            return {
                "success": True, "terminal": False, "error": "",
                "evidence_type": "dom_destination_opened", "navigation_verified": False,
                "destination_opened": True,
                "requested_url": manifest["requested_url"],
                "landing_url": manifest["landing_url"], "final_url": manifest["final_url"],
                "resolved_destination": manifest["control"]["resolved_destination"],
                "control_found": True, "control_label": manifest["control"]["label"],
                "screenshot_path": source_path, "screenshot_paths": [source_path, destination_path],
                "manifest_path": manifest_path, "http": http_result,
                "navigation": manifest["navigation"],
                "control": manifest["control"],
                "page_signals": manifest["page_signals"],
            }
    except Exception as exc:
        for path in written:
            try:
                os.remove(path)
            except OSError:
                pass
        return {
            "success": False, "terminal": False, "terminal_stage": "",
            "error": redact_text(exc),
            "evidence_type": "", "navigation_verified": False,
            "requested_url": redact_url(requested_url), "screenshot_path": "",
            "screenshot_paths": [], "manifest_path": "", "http": http_result,
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


def capture_verified_navigation_evidence(*args, **kwargs) -> dict:
    """Compatibility alias; capture is now a no-click DOM destination open."""
    return capture_dom_destination_evidence(*args, **kwargs)


def validate_evidence_artifacts(result: dict) -> dict:
    """Validate one automatic or 1-3 manual screenshots and their manifest."""
    raw_paths = (result or {}).get("screenshot_paths") or []
    if not raw_paths and (result or {}).get("screenshot_path"):
        raw_paths = [(result or {}).get("screenshot_path")]
    screenshot_paths = [os.path.abspath(str(path or "")) for path in raw_paths]
    manifest_path = os.path.abspath(str((result or {}).get("manifest_path") or ""))
    errors = []
    if not 1 <= len(screenshot_paths) <= MAX_MANUAL_SCREENSHOTS:
        errors.append("Evidence phải có từ 1 đến 3 ảnh")
    for screenshot_path in screenshot_paths:
        if not os.path.isfile(screenshot_path):
            errors.append("Không tìm thấy screenshot")
            continue
        size = os.path.getsize(screenshot_path)
        with open(screenshot_path, "rb") as handle:
            header = handle.read(12)
        if size <= 0 or size > MAX_SCREENSHOT_BYTES:
            errors.append("Screenshot rỗng hoặc vượt quá 10 MB")
        if not (header.startswith(b"\x89PNG\r\n\x1a\n") or header.startswith(b"\xff\xd8\xff")):
            errors.append("Screenshot không phải PNG/JPEG hợp lệ")
    manifest = {}
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        if not isinstance(manifest, dict) or manifest.get("version") != EVIDENCE_VERSION:
            errors.append("Manifest evidence không đúng phiên bản")
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.append("Không đọc được manifest evidence")
    manifest_screenshots = manifest.get("screenshots") or []
    if not manifest_screenshots and manifest.get("screenshot"):
        manifest_screenshots = [manifest.get("screenshot")]
    if len(manifest_screenshots) != len(screenshot_paths):
        errors.append("Số ảnh không khớp manifest")
    elif not errors:
        for path, item in zip(screenshot_paths, manifest_screenshots):
            if item.get("sha256") != _sha256(path):
                errors.append("Hash screenshot không khớp manifest")
                break
    if manifest.get("evidence_type") == "verified_navigation":
        if not manifest.get("navigation_verified"):
            errors.append("Verified navigation phải có navigation_verified=true")
        if len(screenshot_paths) != 2:
            errors.append("Verified navigation phải có đúng 2 ảnh trước và sau click")
        if not str(manifest.get("final_url") or "").strip():
            errors.append("Verified navigation thiếu URL cuối sau click")
        control = manifest.get("control") or {}
        if not control.get("found"):
            errors.append("Verified navigation thiếu control đã click")
    elif manifest.get("evidence_type") == "dom_destination_opened":
        if not manifest.get("destination_opened"):
            errors.append("DOM destination evidence thiếu destination_opened=true")
        if manifest.get("navigation_verified"):
            errors.append("DOM destination evidence không được tuyên bố đã click")
        if len(screenshot_paths) != 2:
            errors.append("DOM destination evidence phải có đúng 2 ảnh nguồn và đích")
        if not str(manifest.get("final_url") or "").strip():
            errors.append("DOM destination evidence thiếu URL đích cuối")
        control = manifest.get("control") or {}
        if not control.get("found") or not control.get("resolved_destination"):
            errors.append("DOM destination evidence thiếu URL đọc từ control")
    return {"valid": not errors, "errors": errors, "manifest": manifest}


def evidence_attachment_paths(result: dict | None) -> list[str]:
    """Return validated screenshots followed by their manifest in stable order."""
    if not isinstance(result, dict) or not result.get("success"):
        return []
    validation = validate_evidence_artifacts(result)
    if not validation["valid"]:
        return []
    paths = result.get("screenshot_paths") or [result.get("screenshot_path")]
    return [os.path.abspath(path) for path in paths if path] + [os.path.abspath(result["manifest_path"])]


def classify_evidence_case(manifest: dict | None) -> str:
    """Classify evidence for provider-facing narrative selection."""
    manifest = manifest if isinstance(manifest, dict) else {}
    if manifest.get("evidence_type") == "manual_upload":
        return "manual_page_evidence"
    control = manifest.get("control") or {}
    if control.get("found") and _safe_http_destination(control.get("resolved_destination", "")):
        return "control_with_destination"
    if control.get("found"):
        return "control_without_destination"
    return "page_without_auth_control"


def _format_collection_indicators(manifest: dict) -> list[str]:
    """Describe only aggregate form indicators actually observed in the DOM."""
    signals = _sanitize_page_signals(manifest.get("page_signals"))
    lines = []
    if signals["passwordInputs"]:
        lines.append(
            f'- {signals["passwordInputs"]} visible password field(s), consistent with an '
            "authentication interface capable of collecting login credentials."
        )
    if signals["otpInputs"]:
        lines.append(
            f'- {signals["otpInputs"]} visible OTP/verification-code field(s), capable of '
            "collecting one-time authentication codes."
        )
    if signals["paymentInputs"]:
        lines.append(
            f'- {signals["paymentInputs"]} visible payment-related field(s), capable of '
            "collecting payment or financial information."
        )
    if signals["identityInputs"]:
        lines.append(
            f'- {signals["identityInputs"]} visible identity/contact field(s) requesting '
            "information such as a username, email address, name, or phone number."
        )
    return lines


def format_email_evidence_block(result: dict | None) -> str:
    """Format factual English evidence for passive, DOM-open, or legacy captures."""
    validation = validate_evidence_artifacts(result or {})
    if not validation["valid"]:
        return ""
    manifest = validation["manifest"]
    control = manifest.get("control") or {}
    http = manifest.get("http") or {}
    manual = manifest.get("evidence_type") == "manual_upload"
    verified = (
        manifest.get("evidence_type") == "verified_navigation"
        and bool(manifest.get("navigation_verified"))
    )
    dom_opened = (
        manifest.get("evidence_type") == "dom_destination_opened"
        and bool(manifest.get("destination_opened"))
    )
    if dom_opened:
        navigation = manifest.get("navigation") or {}
        source_url = navigation.get("source_url") or manifest.get("landing_url", "")
        dom_destination = control.get("resolved_destination", "")
        final_url = manifest.get("final_url", "")
        lines = [
            "--- Observed Phishing Behavior and Supporting Evidence ---",
            f'The reported website contains a visible "{control.get("label", "Register/Login")}" '
            "control that links visitors to the following destination:",
            "",
            dom_destination,
            "",
            "The destination is exposed directly in the page markup as:",
            "",
            f'href="{dom_destination}"',
            "",
            "For evidence capture, that disclosed URL was opened in a separate isolated "
            "browser tab using the source page as the referrer.",
            f"Final destination observed: {final_url}",
            "",
            "Steps to reproduce:",
            f"1. Visit {source_url}",
            f'2. Locate the "{control.get("label", "Register/Login")}" control.',
            f"3. Inspect or follow its link target: {dom_destination}",
            f"4. Observe the resulting destination: {final_url}",
        ]
        if navigation.get("redirect_chain"):
            lines.append(
                "Observed redirect chain after opening the disclosed destination: "
                + _format_navigation_redirect_chain(
                    navigation.get("redirect_chain") or [], manifest.get("final_url", ""),
                )
            )
        collection_indicators = _format_collection_indicators(manifest)
        if collection_indicators:
            lines.extend(["", "Observed data-collection indicators:", *collection_indicators])
        lines.extend([
            "",
            "The attached screenshots show the reported page, the identified control, "
            "and the destination page. The attached manifest preserves the URLs and file hashes.",
            "Please investigate the reported URL, the disclosed destination, and their "
            "relationship, and take appropriate action under your phishing and abuse policies.",
            "Capture scope: the destination URL was opened directly from the observed markup; "
            "no credentials were entered and no form was submitted.",
            "--- End of Supporting Evidence ---",
        ])
        return "\n".join(lines)
    if verified:
        navigation = manifest.get("navigation") or {}
        lines = [
            "--- Technical Evidence: Verified Browser Navigation ---",
            "Evidence type: Automated isolated-browser navigation",
            f"Captured at (UTC): {manifest.get('observed_at', '')}",
            f"Requested URL: {manifest.get('requested_url', '')}",
            f"URL before click: {navigation.get('source_url') or manifest.get('landing_url', '')}",
            f"Clicked control: {control.get('label', '')}",
            f"DOM href: {control.get('resolved_destination', '')}",
            f"Final URL after click: {manifest.get('final_url', '')}",
        ]
        if navigation.get("redirect_chain"):
            lines.append(
                "Browser redirect chain after click: "
                + _format_navigation_redirect_chain(
                    navigation.get("redirect_chain") or [], manifest.get("final_url", ""),
                )
            )
        if http.get("success"):
            lines.append(f"Initial server-side redirect chain: {format_http_redirect_chain(http)}")
        lines.extend([
            f"Source page title: {(manifest.get('page') or {}).get('title', '')}",
            f"Destination page title: {(manifest.get('destination_page') or {}).get('title', '')}",
            f"Browser profile: {(manifest.get('profile') or {}).get('name', '')}",
            "No credentials, typing, or form submission was performed.",
            "The attached before/after screenshots and JSON manifest document this observation.",
            "--- End of Verified Browser Evidence ---",
        ])
        return "\n".join(lines)
    lines = ["--- Observed Phishing Behavior and Supporting Evidence ---"]
    evidence_case = classify_evidence_case(manifest)
    if evidence_case == "control_with_destination":
        lines.extend([
            f'At the reported URL, the page presents a visible "{control.get("label", "Register/Login")}" '
            "control that points to the following destination:",
            "",
            control.get("resolved_destination", ""),
            "",
            "The destination is exposed directly in the page markup as:",
            "",
            f'href="{control.get("resolved_destination", "")}"',
            "",
            "Steps to verify the disclosed link:",
            f"1. Visit {manifest.get('requested_url', '')}",
            f'2. Locate the "{control.get("label", "Register/Login")}" control.',
            "3. Inspect the control's link target in the page markup.",
            f"4. Confirm that it resolves to {control.get('resolved_destination', '')}.",
        ])
    elif evidence_case == "control_without_destination":
        lines.extend([
            f'The reported page presents a visible "{control.get("label", "Register/Login")}" '
            "control associated with a registration or authentication flow.",
            "",
            "The control does not expose a usable HTTP(S) destination in its static markup. "
            "It may rely on JavaScript or runtime navigation, so no destination URL is asserted here.",
            "",
            "Steps to reproduce:",
            f"1. Visit {manifest.get('requested_url', '')}",
            f'2. Locate the "{control.get("label", "Register/Login")}" control.',
            "3. Observe the registration/authentication interface and inspect its runtime behavior.",
        ])
    else:
        lines.append(
            "Operator-supplied browser screenshot(s) document the reported page and "
            "the suspected phishing or unauthorized brand-impersonation content."
            if manual else
            "The attached browser screenshot documents the content displayed at the reported URL."
        )
        if not manual:
            lines.extend([
                "",
                "No visible Register/Login control was identified during this capture. The page is "
                "still being reported for suspected phishing and unauthorized brand impersonation "
                "based on the displayed content documented in the attachment.",
                "",
                "Steps to review:",
                f"1. Visit {manifest.get('requested_url', '')}",
                "2. Compare the displayed branding and interface with the legitimate service.",
                "3. Review the page and its runtime behavior for deceptive data-collection flows.",
            ])
    collection_indicators = _format_collection_indicators(manifest)
    if collection_indicators:
        lines.extend(["", "Observed data-collection indicators:", *collection_indicators])
    lines.extend([
        "",
        "The attached screenshot(s) show the reported page and supporting visual evidence. "
        "The attached manifest preserves the observed URL and file hashes.",
        "Please investigate the reported URL and any disclosed destination and take "
        "appropriate action under your phishing and abuse policies.",
    ])
    if evidence_case == "control_with_destination":
        lines.append(
            "Verification scope: the destination above was extracted from the page markup; "
            "this capture did not claim that a click or form submission was performed."
        )
    elif evidence_case == "control_without_destination":
        lines.append(
            "Verification scope: the visible control was documented, but no static HTTP(S) "
            "destination was available and no destination is claimed."
        )
    else:
        lines.append(
            "Verification scope: the screenshot documents visible page content; no click, "
            "credential entry, or form submission was performed during capture."
        )
    lines.append("--- End of Supporting Evidence ---")
    return "\n".join(lines)


def create_manual_browser_evidence(
    target_url: str, images: list[tuple[str, bytes]], evidence_root: str,
    *, profile_name: str = "manual_upload",
) -> dict:
    """Atomically persist 1-3 operator screenshots with a hash manifest."""
    requested_url = _ensure_http_url(target_url)
    if not 1 <= len(images or []) <= MAX_MANUAL_SCREENSHOTS:
        raise ValueError("Hãy tải lên từ 1 đến 3 ảnh")
    validated = []
    for original_name, content in images:
        data = bytes(content or b"")
        if not data or len(data) > MAX_SCREENSHOT_BYTES:
            raise ValueError("Mỗi ảnh phải có dữ liệu và không vượt quá 10 MB")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            extension = ".png"
        elif data.startswith(b"\xff\xd8\xff"):
            extension = ".jpg"
        else:
            raise ValueError(f"{original_name}: chỉ chấp nhận PNG hoặc JPEG hợp lệ")
        validated.append((original_name, data, extension))

    os.makedirs(evidence_root, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    base = f"{_safe_filename(requested_url)}_{_safe_component(profile_name, 'manual')}_{stamp}"
    written = []
    manifest_path = os.path.abspath(os.path.join(evidence_root, base + ".json"))
    try:
        screenshot_items = []
        screenshot_paths = []
        for index, (original_name, data, extension) in enumerate(validated, start=1):
            path = os.path.abspath(os.path.join(evidence_root, f"{base}_{index}{extension}"))
            temp_path = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            with open(temp_path, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            written.append(path)
            screenshot_paths.append(path)
            screenshot_items.append({
                "path": path, "original_name": os.path.basename(str(original_name)),
                "size": len(data), "sha256": _sha256(path),
            })
        manifest = {
            "version": EVIDENCE_VERSION, "evidence_type": "manual_upload",
            "navigation_verified": False, "observed_at": _now(),
            "profile": {"name": profile_name, "user_agent": "operator supplied"},
            "requested_url": redact_url(requested_url), "landing_url": "",
            "http": {}, "page": {"title": ""}, "control": {"found": False},
            "screenshots": screenshot_items,
        }
        _atomic_json(manifest_path, manifest)
        written.append(manifest_path)
        return {
            "success": True, "terminal": False, "error": "",
            "evidence_type": "manual_upload", "navigation_verified": False,
            "requested_url": manifest["requested_url"], "landing_url": "",
            "resolved_destination": "", "control_found": False,
            "control_label": "", "screenshot_path": screenshot_paths[0],
            "screenshot_paths": screenshot_paths, "manifest_path": manifest_path,
            "http": {},
        }
    except Exception:
        for path in written:
            try:
                os.remove(path)
            except OSError:
                pass
        raise


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
                    const inputs = [...document.querySelectorAll('input, textarea, select')].filter(el => {
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0
                            && style.display !== 'none' && style.visibility !== 'hidden';
                    });
                    const describe = el => [
                        el.getAttribute('type'), el.getAttribute('name'), el.getAttribute('id'),
                        el.getAttribute('placeholder'), el.getAttribute('autocomplete'),
                        el.getAttribute('aria-label')
                    ].filter(Boolean).join(' ').toLowerCase();
                    const descriptions = inputs.map(describe);
                    const pageSignals = {
                        formCount: document.querySelectorAll('form').length,
                        visibleInputCount: inputs.length,
                        passwordInputs: inputs.filter(el => String(el.getAttribute('type') || '').toLowerCase() === 'password').length,
                        otpInputs: descriptions.filter(value => /\\b(otp|one[-_ ]?time|verification[-_ ]?code|mã\\s*(otp|xác\\s*minh))\\b/i.test(value)).length,
                        paymentInputs: descriptions.filter(value => /\\b(card|credit|debit|cvv|cvc|payment|bank|account[-_ ]?number|thanh\\s*toán|ngân\\s*hàng)\\b/i.test(value)).length,
                        identityInputs: descriptions.filter(value => /\\b(email|phone|tel|username|user[-_ ]?name|full[-_ ]?name|họ\\s*tên|số\\s*điện\\s*thoại)\\b/i.test(value)).length
                    };
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
                        domElement: picked?.el?.outerHTML || '', pageSignals
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
                    "terminal_stage": "source",
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
                "page_signals": _sanitize_page_signals(snapshot.get("pageSignals")),
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
                "http": http_result, "control": manifest["control"],
                "page_signals": manifest["page_signals"],
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
            "success": False, "terminal": False, "terminal_stage": "",
            "error": redact_text(exc),
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
