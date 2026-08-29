"""Defensive multi-profile cloaking detection and evidence helpers.

The HTTP detector compares the same URL across desktop/mobile user agents,
direct/Google-referrer visits, and an optional ``/vi-vn/`` path variant.  It is
deliberately passive: no forms are submitted and no page controls are clicked.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests


DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36"
)
GOOGLE_REFERRER = "https://www.google.com/"
KNOWN_CLOAKING_ASSETS = ("best-traffic.pages.dev/traffic_dr.js",)
SENSITIVE_TERMS = (
    "casino", "sportsbook", "betting", "đặt cược", "cược", "tài xỉu",
    "tài-xỉu", "nổ hũ", "no hu", "nạp tiền", "nap tien", "rút tiền",
    "rut tien", "khuyến mãi 100%", "khuyen mai 100%", "xóc đĩa",
    "baccarat", "slot game", "jackpot",
)
FAKE_NOT_FOUND_TERMS = (
    "404 not found", "page not found", "the page you requested could not be found",
    "không tìm thấy trang", "trang không tồn tại",
)
SAFE_HEADER_NAMES = {
    "content-type", "content-length", "server", "location", "cf-ray",
    "x-powered-by", "x-cache", "x-vercel-id",
}
MAX_VISIBLE_TEXT = 50_000
EMAIL_PROFILE_LABELS = {
    "desktop_direct": "Desktop, direct visit",
    "mobile_direct": "Mobile, direct visit",
    "desktop_google": "Desktop, Google referrer",
    "mobile_google": "Mobile, Google referrer",
    "desktop_direct_vi_vn": "Desktop, direct visit to /vi-vn/",
    "mobile_google_vi_vn": "Mobile, Google referrer visit to /vi-vn/",
}


class _HTMLSignalsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.scripts: list[str] = []
        self.iframes: list[str] = []
        self.forms = 0
        self.password_inputs = 0
        self.meta_refresh = ""
        self._in_title = False
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = {str(key).lower(): str(value or "") for key, value in attrs}
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "template"}:
            self._ignored_depth += 1
        if tag == "title":
            self._in_title = True
        elif tag == "script" and attrs_dict.get("src"):
            self.scripts.append(attrs_dict["src"])
        elif tag == "iframe" and attrs_dict.get("src"):
            self.iframes.append(attrs_dict["src"])
        elif tag == "form":
            self.forms += 1
        elif tag == "input" and attrs_dict.get("type", "").lower() == "password":
            self.password_inputs += 1
        elif tag == "meta" and attrs_dict.get("http-equiv", "").lower() == "refresh":
            self.meta_refresh = attrs_dict.get("content", "")[:500]

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "template"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if not self._ignored_depth:
            self.text_parts.append(data)


def _ensure_url(target: str) -> str:
    value = str(target or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"}:
        value = f"https://{value}"
        parsed = urlsplit(value)
    if not parsed.hostname:
        return ""
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))


def _vi_vn_url(target_url: str) -> str:
    parsed = urlsplit(target_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/vi-vn/", "", ""))


def _profile_specs(target_url: str, include_path_variant: bool) -> list[dict]:
    profiles = [
        {"name": "desktop_direct", "label": "Desktop trực tiếp", "url": target_url, "user_agent": DESKTOP_UA, "referrer": ""},
        {"name": "mobile_direct", "label": "Mobile trực tiếp", "url": target_url, "user_agent": MOBILE_UA, "referrer": ""},
        {"name": "desktop_google", "label": "Desktop từ Google", "url": target_url, "user_agent": DESKTOP_UA, "referrer": GOOGLE_REFERRER},
        {"name": "mobile_google", "label": "Mobile từ Google", "url": target_url, "user_agent": MOBILE_UA, "referrer": GOOGLE_REFERRER},
    ]
    parsed = urlsplit(target_url)
    if include_path_variant and (parsed.path or "/") in {"", "/"}:
        variant = _vi_vn_url(target_url)
        profiles.extend([
            {"name": "desktop_direct_vi_vn", "label": "Desktop /vi-vn/", "url": variant, "user_agent": DESKTOP_UA, "referrer": ""},
            {"name": "mobile_google_vi_vn", "label": "Mobile Google /vi-vn/", "url": variant, "user_agent": MOBILE_UA, "referrer": GOOGLE_REFERRER},
        ])
    return profiles


def _safe_headers(headers) -> dict[str, str]:
    safe = {}
    for key, value in dict(headers or {}).items():
        lower = str(key).lower()
        if lower in SAFE_HEADER_NAMES or "mirror-document" in lower:
            safe[str(key)] = str(value)[:1000]
    return safe


def _read_limited(response, max_bytes: int) -> bytes:
    body = bytearray()
    for chunk in response.iter_content(chunk_size=8192):
        if not chunk:
            continue
        remaining = max_bytes - len(body)
        if remaining <= 0:
            break
        body.extend(chunk[:remaining])
        if len(body) >= max_bytes:
            break
    return bytes(body)


def _parse_html(text: str, base_url: str) -> dict:
    parser = _HTMLSignalsParser()
    try:
        parser.feed(text)
    except Exception:
        pass
    visible = re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()[:MAX_VISIBLE_TEXT]
    normalized = visible.casefold()
    scripts = [urljoin(base_url, src) for src in parser.scripts[:100]]
    iframes = [urljoin(base_url, src) for src in parser.iframes[:50]]
    keyword_hits = sorted({term for term in SENSITIVE_TERMS if term.casefold() in normalized})
    fake_404 = any(term.casefold() in normalized for term in FAKE_NOT_FOUND_TERMS)
    title = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()[:300]
    return {
        "title": title,
        "visible_text": visible,
        "text_preview": visible[:500],
        "keyword_hits": keyword_hits,
        "fake_404": fake_404,
        "forms": parser.forms,
        "password_inputs": parser.password_inputs,
        "meta_refresh": parser.meta_refresh,
        "scripts": scripts,
        "iframes": iframes,
    }


def _fetch_profile(profile: dict, timeout: float, max_bytes: int) -> dict:
    headers = {
        "User-Agent": profile["user_agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.7,en;q=0.6",
        "Cache-Control": "no-cache",
    }
    if profile.get("referrer"):
        headers["Referer"] = profile["referrer"]
    started = datetime.now(timezone.utc)
    session = requests.Session()
    try:
        response = session.get(
            profile["url"], headers=headers, timeout=timeout, allow_redirects=True,
            verify=False, stream=True,
        )
        try:
            raw = _read_limited(response, max_bytes)
            encoding = response.encoding or "utf-8"
            text = raw.decode(encoding, errors="replace")
            parsed = _parse_html(text, response.url)
            safe_headers = _safe_headers(response.headers)
            header_blob = " ".join(f"{key}:{value}" for key, value in safe_headers.items()).casefold()
            resource_blob = " ".join(parsed["scripts"] + parsed["iframes"] + [text[:100_000]]).casefold()
            iocs = sorted({asset for asset in KNOWN_CLOAKING_ASSETS if asset.casefold() in resource_blob})
            mirror_header = "mirror-document" in header_blob
            history = [
                {"status": hop.status_code, "url": hop.url, "location": hop.headers.get("Location", "")}
                for hop in response.history
            ]
            return {
                "name": profile["name"], "label": profile["label"],
                "requested_url": profile["url"], "referrer": profile.get("referrer", ""),
                "status_code": response.status_code, "final_url": response.url,
                "redirect_chain": history, "headers": safe_headers,
                "body_bytes": len(raw), "body_sha256": hashlib.sha256(raw).hexdigest(),
                "truncated": len(raw) >= max_bytes, "error": "",
                "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
                "mirror_document_header": mirror_header, "known_iocs": iocs,
                **parsed,
            }
        finally:
            response.close()
    except requests.RequestException as exc:
        return {
            "name": profile["name"], "label": profile["label"],
            "requested_url": profile["url"], "referrer": profile.get("referrer", ""),
            "status_code": None, "final_url": "", "redirect_chain": [], "headers": {},
            "body_bytes": 0, "body_sha256": "", "truncated": False,
            "title": "", "visible_text": "", "text_preview": "", "keyword_hits": [],
            "fake_404": False, "forms": 0, "password_inputs": 0, "meta_refresh": "",
            "scripts": [], "iframes": [], "mirror_document_header": False, "known_iocs": [],
            "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "error": str(exc),
        }
    finally:
        session.close()


def _size_ratio(a: int, b: int) -> float:
    smaller = max(1, min(a, b))
    return round(max(a, b) / smaller, 2)


def _add_signal(signals: list[dict], kind: str, weight: int, profiles: list[str], detail: str) -> None:
    key = (kind, tuple(profiles), detail)
    if any((item["kind"], tuple(item["profiles"]), item["detail"]) == key for item in signals):
        return
    signals.append({"kind": kind, "weight": weight, "profiles": profiles, "detail": detail})


def _compare_profiles(left: dict, right: dict, signals: list[dict]) -> dict:
    if left.get("error") or right.get("error"):
        return {"profiles": [left["name"], right["name"]], "available": False}
    left_text = left.get("visible_text", "")
    right_text = right.get("visible_text", "")
    similarity = round(SequenceMatcher(None, left_text, right_text).ratio(), 3)
    ratio = _size_ratio(int(left.get("body_bytes", 0)), int(right.get("body_bytes", 0)))
    pair = [left["name"], right["name"]]
    left_keywords = set(left.get("keyword_hits", []))
    right_keywords = set(right.get("keyword_hits", []))
    keyword_delta = sorted(left_keywords.symmetric_difference(right_keywords))

    if keyword_delta:
        _add_signal(signals, "keyword_exposure", 30, pair, f"Từ khóa nhạy cảm chỉ xuất hiện ở một profile: {', '.join(keyword_delta)}")
    if left.get("fake_404") != right.get("fake_404") and (left_keywords or right_keywords):
        _add_signal(signals, "fake_404_vs_sensitive", 35, pair, "Một profile hiện fake 404 trong khi profile còn lại có nội dung nhạy cảm")
    if left.get("final_url") != right.get("final_url"):
        _add_signal(signals, "redirect_difference", 25, pair, f"URL cuối khác nhau: {left.get('final_url')} <> {right.get('final_url')}")
    if left.get("password_inputs") != right.get("password_inputs"):
        _add_signal(signals, "form_difference", 20, pair, "Password input chỉ xuất hiện ở một profile")
    if similarity < 0.35 and ratio >= 1.5:
        _add_signal(signals, "content_difference", 25, pair, f"Visible text similarity={similarity}, size ratio={ratio}x")
    elif similarity < 0.6 and left.get("title") != right.get("title"):
        _add_signal(signals, "title_difference", 10, pair, f"Title khác nhau, similarity={similarity}")
    elif ratio >= 1.8:
        _add_signal(signals, "size_difference", 8, pair, f"Kích thước response chênh lệch {ratio}x")
    return {
        "profiles": pair, "available": True, "text_similarity": similarity,
        "size_ratio": ratio, "keyword_delta": keyword_delta,
        "final_url_changed": left.get("final_url") != right.get("final_url"),
    }


def analyze_profiles(profiles: dict[str, dict], target_url: str) -> dict:
    """Classify already-fetched profiles. Kept pure for deterministic tests."""
    signals: list[dict] = []
    for profile in profiles.values():
        if profile.get("mirror_document_header"):
            _add_signal(signals, "mirror_document_header", 60, [profile["name"]], "Response tự khai báo mirror-document theo thiết bị")
        for ioc in profile.get("known_iocs", []):
            _add_signal(signals, "known_cloaking_ioc", 70, [profile["name"]], f"Phát hiện asset cloaking đã biết: {ioc}")

    comparisons = []
    pairs = [
        ("desktop_direct", "mobile_direct"),
        ("desktop_direct", "desktop_google"),
        ("mobile_direct", "mobile_google"),
        ("desktop_direct", "mobile_google"),
        ("desktop_direct", "desktop_direct_vi_vn"),
        ("mobile_google", "mobile_google_vi_vn"),
    ]
    for left_name, right_name in pairs:
        if left_name in profiles and right_name in profiles:
            comparisons.append(_compare_profiles(profiles[left_name], profiles[right_name], signals))

    available = sum(not item.get("error") for item in profiles.values())
    failures = len(profiles) - available
    score = min(100, sum(item["weight"] for item in signals))
    kinds = {item["kind"] for item in signals}
    exact_ioc = bool(kinds.intersection({"mirror_document_header", "known_cloaking_ioc"}))
    independent_kinds = kinds.intersection({
        "keyword_exposure", "fake_404_vs_sensitive", "redirect_difference",
        "form_difference", "content_difference", "title_difference", "size_difference",
    })
    if exact_ioc or (score >= 50 and len(independent_kinds) >= 2):
        verdict = "LIKELY"
    elif score >= 20:
        verdict = "POSSIBLE"
    elif available < 2 or failures >= max(2, len(profiles) // 2):
        verdict = "INCONCLUSIVE"
    else:
        verdict = "NO_SIGNAL"
    return {
        "version": 1, "engine": "http", "target_url": target_url,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict, "score": score, "signals": signals,
        "profiles": profiles, "comparisons": comparisons,
        "profiles_available": available, "profiles_failed": failures,
        "manual_review_required": verdict in {"POSSIBLE", "INCONCLUSIVE"},
        "evidence_path": "", "playwright": {},
    }


def save_evidence_manifest(result: dict, evidence_root: str) -> str:
    """Persist a redacted JSON manifest atomically and return its path."""
    parsed = urlsplit(result.get("target_url", ""))
    safe_domain = re.sub(r"[^a-zA-Z0-9._-]+", "_", parsed.hostname or "unknown")
    target_dir = os.path.join(evidence_root, safe_domain)
    os.makedirs(target_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    engine = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(result.get("engine") or "http"))
    path = os.path.join(target_dir, f"{timestamp}_{engine}_cloaking.json")
    serializable = deepcopy(result)
    for profile in serializable.get("profiles", {}).values():
        profile.pop("visible_text", None)
    fd, temp_path = tempfile.mkstemp(prefix=".cloaking_", suffix=".tmp", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(serializable, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
    return path


def probe_http_cloaking(
    target: str,
    *,
    timeout: float = 8.0,
    max_bytes: int = 524_288,
    include_path_variant: bool = True,
    max_workers: int = 4,
    evidence_root: str | None = None,
) -> dict:
    """Run passive HTTP probes and return a JSON-serializable verdict."""
    target_url = _ensure_url(target)
    if not target_url:
        return {
            "version": 1, "engine": "http", "target_url": str(target or ""),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "verdict": "INCONCLUSIVE", "score": 0,
            "signals": [], "profiles": {}, "comparisons": [],
            "profiles_available": 0, "profiles_failed": 0,
            "manual_review_required": True, "evidence_path": "",
            "playwright": {}, "error": "URL không hợp lệ",
        }
    specs = _profile_specs(target_url, include_path_variant)
    profiles: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(specs))) as executor:
        futures = {executor.submit(_fetch_profile, spec, timeout, max_bytes): spec for spec in specs}
        for future in as_completed(futures):
            spec = futures[future]
            try:
                profiles[spec["name"]] = future.result()
            except Exception as exc:
                profiles[spec["name"]] = {
                    "name": spec["name"], "label": spec["label"], "requested_url": spec["url"],
                    "referrer": spec.get("referrer", ""), "error": str(exc), "status_code": None,
                    "final_url": "", "body_bytes": 0, "body_sha256": "", "title": "",
                    "visible_text": "", "text_preview": "", "keyword_hits": [],
                    "fake_404": False, "forms": 0, "password_inputs": 0,
                    "known_iocs": [], "mirror_document_header": False,
                }
    ordered = {spec["name"]: profiles[spec["name"]] for spec in specs}
    result = analyze_profiles(ordered, target_url)
    if evidence_root and result["verdict"] != "NO_SIGNAL":
        try:
            result["evidence_path"] = save_evidence_manifest(result, evidence_root)
        except OSError as exc:
            result["evidence_error"] = str(exc)
    # Full visible text is needed only while comparing profiles. Do not retain it
    # in Streamlit session state or expose it to the browser payload.
    for profile in result.get("profiles", {}).values():
        profile.pop("visible_text", None)
    return result


def _browser_profile_specs(target_url: str) -> list[dict]:
    return [
        {
            "name": "desktop_direct", "label": "Playwright desktop trực tiếp",
            "url": target_url, "user_agent": DESKTOP_UA, "referrer": "",
            "viewport": {"width": 1440, "height": 1000}, "is_mobile": False,
        },
        {
            "name": "mobile_google", "label": "Playwright mobile từ Google",
            "url": target_url, "user_agent": MOBILE_UA, "referrer": GOOGLE_REFERRER,
            "viewport": {"width": 412, "height": 915}, "is_mobile": True,
        },
    ]


def _capture_browser_profile(browser, profile: dict, target_dir: str, timeout_ms: int) -> dict:
    started = datetime.now(timezone.utc)
    context = browser.new_context(
        user_agent=profile["user_agent"], viewport=profile["viewport"],
        is_mobile=profile["is_mobile"], locale="vi-VN",
        accept_downloads=False, service_workers="block",
    )
    page = context.new_page()
    screenshot_path = os.path.join(target_dir, f"{profile['name']}.png")
    try:
        response = page.goto(
            profile["url"], wait_until="domcontentloaded", timeout=timeout_ms,
            referer=profile.get("referrer") or None,
        )
        page.wait_for_timeout(min(2500, max(0, timeout_ms // 4)))
        html = page.content()
        try:
            visible_text = page.locator("body").inner_text(timeout=3000)
        except Exception:
            visible_text = ""
        parsed = _parse_html(html, page.url)
        parsed["visible_text"] = re.sub(r"\s+", " ", visible_text).strip()[:MAX_VISIBLE_TEXT]
        parsed["text_preview"] = parsed["visible_text"][:500]
        normalized = parsed["visible_text"].casefold()
        parsed["keyword_hits"] = sorted({term for term in SENSITIVE_TERMS if term.casefold() in normalized})
        parsed["fake_404"] = any(term.casefold() in normalized for term in FAKE_NOT_FOUND_TERMS)
        try:
            resources = page.evaluate(
                "() => performance.getEntriesByType('resource').map(entry => entry.name).slice(0, 500)"
            ) or []
        except Exception:
            resources = []
        resource_blob = " ".join([html[:100_000], *map(str, resources)]).casefold()
        known_iocs = sorted({asset for asset in KNOWN_CLOAKING_ASSETS if asset.casefold() in resource_blob})
        page.screenshot(path=screenshot_path, full_page=True)
        raw = html.encode("utf-8", errors="replace")
        return {
            "name": profile["name"], "label": profile["label"],
            "requested_url": profile["url"], "referrer": profile.get("referrer", ""),
            "status_code": response.status if response else None, "final_url": page.url,
            "redirect_chain": [], "headers": {}, "body_bytes": len(raw),
            "body_sha256": hashlib.sha256(raw).hexdigest(), "truncated": False,
            "error": "", "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "mirror_document_header": False, "known_iocs": known_iocs,
            "resources": [str(item)[:2000] for item in resources],
            "screenshot_path": screenshot_path, **parsed,
        }
    except Exception as exc:
        return {
            "name": profile["name"], "label": profile["label"],
            "requested_url": profile["url"], "referrer": profile.get("referrer", ""),
            "status_code": None, "final_url": "", "redirect_chain": [], "headers": {},
            "body_bytes": 0, "body_sha256": "", "truncated": False,
            "title": "", "visible_text": "", "text_preview": "", "keyword_hits": [],
            "fake_404": False, "forms": 0, "password_inputs": 0, "meta_refresh": "",
            "scripts": [], "iframes": [], "mirror_document_header": False, "known_iocs": [],
            "resources": [], "screenshot_path": "",
            "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "error": str(exc),
        }
    finally:
        context.close()


def probe_playwright_cloaking(
    target: str, *, timeout_ms: int = 15_000, evidence_root: str | None = None,
    _playwright_factory=None,
) -> dict:
    """Passively render two browser profiles; never click, type, or submit forms."""
    target_url = _ensure_url(target)
    if not target_url:
        return {
            "version": 1, "engine": "playwright", "target_url": str(target or ""),
            "observed_at": datetime.now(timezone.utc).isoformat(), "verdict": "INCONCLUSIVE",
            "score": 0, "signals": [], "profiles": {}, "comparisons": [],
            "profiles_available": 0, "profiles_failed": 0, "manual_review_required": True,
            "screenshots": [], "evidence_path": "", "available": False,
            "error": "URL không hợp lệ",
        }
    try:
        if _playwright_factory is None:
            from playwright.sync_api import sync_playwright
            _playwright_factory = sync_playwright
    except ImportError as exc:
        return {
            "version": 1, "engine": "playwright", "target_url": target_url,
            "observed_at": datetime.now(timezone.utc).isoformat(), "verdict": "INCONCLUSIVE",
            "score": 0, "signals": [], "profiles": {}, "comparisons": [],
            "profiles_available": 0, "profiles_failed": 2, "manual_review_required": True,
            "screenshots": [], "evidence_path": "", "available": False, "error": str(exc),
        }

    parsed = urlsplit(target_url)
    safe_domain = re.sub(r"[^a-zA-Z0-9._-]+", "_", parsed.hostname or "unknown")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    root = evidence_root or tempfile.gettempdir()
    target_dir = os.path.join(root, safe_domain, f"{timestamp}_playwright")
    os.makedirs(target_dir, exist_ok=True)
    profiles = {}
    try:
        with _playwright_factory() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for spec in _browser_profile_specs(target_url):
                    profiles[spec["name"]] = _capture_browser_profile(browser, spec, target_dir, timeout_ms)
            finally:
                browser.close()
    except Exception as exc:
        return {
            "version": 1, "engine": "playwright", "target_url": target_url,
            "observed_at": datetime.now(timezone.utc).isoformat(), "verdict": "INCONCLUSIVE",
            "score": 0, "signals": [], "profiles": profiles, "comparisons": [],
            "profiles_available": 0, "profiles_failed": 2, "manual_review_required": True,
            "screenshots": [], "evidence_path": "", "available": False, "error": str(exc),
        }
    result = analyze_profiles(profiles, target_url)
    result.update({"engine": "playwright", "available": True})
    result["screenshots"] = [
        {"label": profile.get("label"), "path": profile.get("screenshot_path")}
        for profile in profiles.values() if profile.get("screenshot_path")
    ]
    if evidence_root:
        try:
            result["evidence_path"] = save_evidence_manifest(result, evidence_root)
        except OSError as exc:
            result["evidence_error"] = str(exc)
    for profile in result.get("profiles", {}).values():
        profile.pop("visible_text", None)
    return result


def merge_playwright_result(http_result: dict, browser_result: dict, evidence_root: str | None = None) -> dict:
    """Combine browser verification without downgrading HTTP evidence."""
    merged = deepcopy(http_result or {})
    merged["playwright"] = deepcopy(browser_result or {})
    browser_verdict = (browser_result or {}).get("verdict")
    if browser_verdict == "LIKELY":
        merged["verdict"] = "LIKELY"
        merged["score"] = max(int(merged.get("score", 0)), int(browser_result.get("score", 0)))
        merged["manual_review_required"] = False
        merged["signals"] = list(merged.get("signals") or []) + [
            {**signal, "source": "playwright"} for signal in browser_result.get("signals") or []
        ]
    elif browser_verdict == "POSSIBLE" and merged.get("verdict") == "INCONCLUSIVE":
        merged["verdict"] = "POSSIBLE"
        merged["score"] = max(int(merged.get("score", 0)), int(browser_result.get("score", 0)))
        merged["manual_review_required"] = True
    merged["screenshots"] = list((browser_result or {}).get("screenshots") or [])
    if evidence_root and merged.get("verdict") != "NO_SIGNAL":
        try:
            merged["engine"] = "http_playwright"
            merged["evidence_path"] = save_evidence_manifest(merged, evidence_root)
        except OSError as exc:
            merged["evidence_error"] = str(exc)
    return merged


def _email_profile_label(profile: dict) -> str:
    name = str(profile.get("name") or "")
    return EMAIL_PROFILE_LABELS.get(name, name.replace("_", " ").title() or "Unknown profile")


def _email_signal_detail(signal: dict, result: dict) -> str:
    """Return provider-facing English prose without reusing localized UI text."""
    kind = signal.get("kind")
    profile_names = [str(name) for name in signal.get("profiles") or []]
    profile_label = " vs ".join(
        EMAIL_PROFILE_LABELS.get(name, name.replace("_", " ").title())
        for name in profile_names
    )
    comparison = next(
        (
            item for item in result.get("comparisons") or []
            if item.get("profiles") == profile_names
        ),
        {},
    )
    suffix = f" ({profile_label})" if profile_label else ""
    if kind == "keyword_exposure":
        return f"Sensitive terms were exposed to only one tested profile{suffix}."
    if kind == "fake_404_vs_sensitive":
        return f"One profile displayed a fake 404 page while the other exposed sensitive content{suffix}."
    if kind == "redirect_difference":
        return f"The profiles resolved to different final URLs{suffix}."
    if kind == "form_difference":
        return f"A password input was present in only one profile{suffix}."
    if kind == "content_difference":
        return (
            "Rendered text differed substantially"
            f" (similarity={comparison.get('text_similarity', 'N/A')}; "
            f"response size ratio={comparison.get('size_ratio', 'N/A')}x){suffix}."
        )
    if kind == "title_difference":
        return (
            f"Page titles differed between profiles; text similarity="
            f"{comparison.get('text_similarity', 'N/A')}{suffix}."
        )
    if kind == "size_difference":
        return f"Response sizes differed by {comparison.get('size_ratio', 'N/A')}x{suffix}."
    if kind == "mirror_document_header":
        return f"The response declared a device-specific mirror-document route{suffix}."
    if kind == "known_cloaking_ioc":
        iocs = []
        for name in profile_names:
            iocs.extend((result.get("profiles") or {}).get(name, {}).get("known_iocs") or [])
        ioc_text = ", ".join(dict.fromkeys(iocs)) or "a known cloaking asset"
        return f"A known cloaking indicator was detected: {ioc_text}{suffix}."
    return f"An additional profile-specific response difference was detected{suffix}."


def format_evidence_block(result: dict) -> str:
    """Render factual, provider-facing cloaking evidence for report drafts."""
    if not result or result.get("verdict") not in {"LIKELY", "POSSIBLE"}:
        return ""
    lines = [
        "--- Technical Evidence: Multi-profile Cloaking Check ---",
        f"Assessment: {result.get('verdict')} (score: {result.get('score', 0)}/100)",
        f"Observed at: {result.get('observed_at', '')}",
        f"Tested URL: {result.get('target_url', '')}",
        "",
    ]
    for profile in result.get("profiles", {}).values():
        label = _email_profile_label(profile)
        if profile.get("error"):
            lines.append(f"- {label}: unavailable (request failed)")
            continue
        keywords = ", ".join(profile.get("keyword_hits", [])) or "none"
        lines.append(
            f"- {label}: HTTP {profile.get('status_code')}; "
            f"final URL={profile.get('final_url')}; title={profile.get('title') or 'N/A'}; "
            f"body={profile.get('body_bytes', 0)} bytes; SHA256={profile.get('body_sha256')}; "
            f"sensitive terms={keywords}"
        )
    if result.get("signals"):
        lines.extend(["", "Observed differences:"])
        lines.extend(f"- {_email_signal_detail(item, result)}" for item in result["signals"])
    playwright = result.get("playwright") or {}
    if playwright:
        lines.extend([
            "", "Passive browser verification:",
            f"- Playwright available: {bool(playwright.get('available'))}",
            f"- Browser assessment: {playwright.get('verdict', 'INCONCLUSIVE')} "
            f"(score: {playwright.get('score', 0)}/100)",
        ])
        for screenshot in playwright.get("screenshots") or []:
            if screenshot.get("path"):
                lines.append(f"- Screenshot attachment: {os.path.basename(screenshot['path'])}")
    if result.get("evidence_path"):
        lines.extend(["", f"Local evidence manifest: {os.path.basename(result['evidence_path'])}"])
    lines.extend([
        "",
        "Please reproduce the behavior using the user-agent and referrer profiles above. "
        "The assessment is based on observed response differences and does not rely on response size alone.",
        "--- End of Cloaking Evidence ---",
    ])
    return "\n".join(lines)
