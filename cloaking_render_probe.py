"""Bounded JavaScript-rendered fallback for cloaking evidence collection."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit
from urllib.parse import quote_plus

from cloaking_probe import validate_public_url


MAX_RENDERED_HTML = 2_000_000


def _visible_text(html: str) -> str:
    value = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html or "")
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = re.sub(r"&(?:nbsp|amp|quot|#\d+);", " ", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip().lower()


def rendered_content_differs(first: dict, second: dict) -> bool:
    """Ignore responsive markup and require a material rendered-content contrast."""
    if first.get("error") or second.get("error"):
        return False
    if first.get("final_url") != second.get("final_url"):
        return True
    first_text, second_text = _visible_text(first.get("html", "")), _visible_text(second.get("html", ""))
    if not first_text or not second_text:
        return False
    first_tokens = set(first_text[:200_000].split()[:20_000])
    second_tokens = set(second_text[:200_000].split()[:20_000])
    union = first_tokens | second_tokens
    similarity = len(first_tokens & second_tokens) / max(len(union), 1)
    largest = max(len(first_text), len(second_text), 1)
    length_delta = abs(len(first_text) - len(second_text)) / largest
    return similarity < 0.25 or (
        similarity < 0.65 and (length_delta >= 0.20 or min(len(first_text), len(second_text)) >= 200)
    )


def _capture(
    browser, url: str, *, mobile: bool, google_referrer: bool,
    mobile_device: dict | None = None, keyword: str = "",
) -> dict:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    device = mobile_device or {}
    context_args = {
        **device,
        "locale": "vi-VN",
        "service_workers": "block",
        "accept_downloads": False,
    }
    if not mobile:
        context_args.update({"viewport": {"width": 1440, "height": 1000}})
    context = browser.new_context(**context_args)
    page = context.new_page()
    checked_hosts: dict[str, bool] = {}

    def guard(route):
        request_url = route.request.url
        scheme = urlsplit(request_url).scheme.lower()
        if scheme not in {"http", "https"}:
            route.continue_()
            return
        host = urlsplit(request_url).hostname or ""
        if host not in checked_hosts:
            try:
                validate_public_url(request_url)
                checked_hosts[host] = True
            except ValueError:
                checked_hosts[host] = False
        route.continue_() if checked_hosts[host] else route.abort()

    page.route("**/*", guard)
    try:
        if google_referrer:
            search_url = f"https://www.google.com/search?q={quote_plus(keyword)}&hl=vi"
            page.goto(search_url, wait_until="domcontentloaded", timeout=20_000)
            target_host = urlsplit(url).hostname or ""
            result_link = page.locator(f'a[href*="{target_host}"]:visible').first
            if result_link.count() == 0:
                raise RuntimeError(
                    f"Google không trả kết quả có hostname {target_host}; có thể gặp CAPTCHA hoặc khác khu vực."
                )
            with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000) as navigation:
                result_link.click()
            response = navigation.value
            if target_host not in (urlsplit(page.url).hostname or ""):
                raise RuntimeError(f"Kết quả Google không điều hướng tới hostname {target_host}.")
        else:
            response = page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        page.wait_for_timeout(5_000)
        html = page.content()[:MAX_RENDERED_HTML]
        screenshot = page.screenshot(full_page=True, type="png")
        return {
            "status": response.status if response else 0,
            "final_url": page.url,
            "redirect_chain": [],
            "headers": {},
            "size": len(html.encode("utf-8")),
            "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "html": html,
            "screenshot": screenshot,
            "title": page.title(),
            "user_agent": page.evaluate("navigator.userAgent"),
            "referrer": page.evaluate("document.referrer"),
            "truncated": len(html) >= MAX_RENDERED_HTML,
            "commands": [
                "Playwright Chromium: desktop direct" if not google_referrer else
                f"Playwright Chromium: Google search {keyword!r} -> click result for {urlsplit(url).hostname}"
            ],
        }
    except PlaywrightTimeoutError as exc:
        return {"error": f"Playwright timeout: {exc}"}
    except Exception as exc:
        return {"error": f"Playwright: {exc}"}
    finally:
        context.close()


def collect_rendered_evidence(url: str, keyword: str) -> dict:
    """Capture desktop-direct and mobile+Google rendered DOM in isolated contexts."""
    safe_url = validate_public_url(url)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"results": {}, "reportable_cloaking": False, "error": "Playwright chưa được cài."}

    with sync_playwright() as playwright:
        # A visible browser matches the operator-provided reproduction recipe and
        # avoids the most basic headless-only response branch. It is auto-closed.
        browser = playwright.chromium.launch(headless=False)
        try:
            desktop = _capture(browser, safe_url, mobile=False, google_referrer=False)
            mobile = _capture(
                browser, safe_url, mobile=True, google_referrer=True,
                mobile_device=playwright.devices["iPhone 13"], keyword=keyword,
            )
        finally:
            browser.close()

    different = rendered_content_differs(desktop, mobile)
    results = {"desktop_direct": desktop, "mobile_google": mobile}
    lines = []
    for name, item in results.items():
        if item.get("error"):
            lines.append(f"[{name}] ERROR: {item['error']}")
        else:
            lines.append(
                f"[{name}] status={item['status']}; final={item['final_url']}; bytes={item['size']}; "
                f"sha256={item['sha256']}; title={item['title']!r}; referrer={item['referrer']!r}; "
                f"user-agent={item['user_agent']}"
            )
    summary = "Playwright rendered evidence (JavaScript enabled, no interaction):\n" + "\n".join(lines)
    return {
        "results": results,
        "successful_count": sum(not item.get("error") for item in results.values()),
        "device_difference": different,
        "referrer_difference": different,
        "matched_path_difference": False,
        "reportable_cloaking": different,
        "capture_method": "rendered_google_click",
        "technical_summary": summary,
        "curl_summary": summary,
    }
