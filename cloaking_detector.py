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
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import requests


DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36"
)
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
)
GOOGLEBOT_SMARTPHONE_UA = (
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
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
TERMINAL_BROWSER_ERROR_TERMS = (
    "không thể truy cập trang web này", "không thể truy cập trang này",
    "this site can't be reached", "this site can’t be reached",
    "server not found", "dns_probe_finished_nxdomain", "err_name_not_resolved",
    "err_connection_refused", "err_connection_timed_out", "err_address_unreachable",
    "name resolution error", "failed to resolve", "getaddrinfo failed",
)
CLOUDFLARE_WARNING_TERMS = (
    "suspected phishing", "suspected malware", "deceptive site",
    "reported for potential phishing", "reported for potential malware",
)
SAFE_HEADER_NAMES = {
    "content-type", "content-length", "server", "location", "cf-ray",
    "x-powered-by", "x-cache", "x-vercel-id", "vary", "cf-ipcountry",
    "x-country-code", "x-middleware-rewrite", "x-matched-path",
}
MAX_VISIBLE_TEXT = 50_000
EMAIL_PROFILE_LABELS = {
    "desktop_direct": "Desktop, direct visit",
    "mobile_direct": "Mobile, direct visit",
    "desktop_google": "Desktop, Google referrer",
    "mobile_google": "Mobile, Google referrer",
    "iphone_google": "iPhone, Google referrer",
    "googlebot_smartphone": "Googlebot Smartphone",
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


def _vantage_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).casefold()).strip("_") or "remote"


def _profile_specs(
    target_url: str, include_path_variant: bool,
    vantage_points: list[dict] | None = None,
) -> list[dict]:
    profiles = [
        {"name": "desktop_direct", "label": "Desktop trực tiếp", "url": target_url, "user_agent": DESKTOP_UA, "referrer": "", "client_hints": {"Sec-CH-UA-Mobile": "?0", "Sec-CH-UA-Platform": '"Windows"'}},
        {"name": "mobile_direct", "label": "Android trực tiếp", "url": target_url, "user_agent": MOBILE_UA, "referrer": "", "client_hints": {"Sec-CH-UA-Mobile": "?1", "Sec-CH-UA-Platform": '"Android"'}},
        {"name": "desktop_google", "label": "Desktop từ Google", "url": target_url, "user_agent": DESKTOP_UA, "referrer": GOOGLE_REFERRER, "client_hints": {"Sec-CH-UA-Mobile": "?0", "Sec-CH-UA-Platform": '"Windows"'}},
        {"name": "mobile_google", "label": "Android từ Google", "url": target_url, "user_agent": MOBILE_UA, "referrer": GOOGLE_REFERRER, "client_hints": {"Sec-CH-UA-Mobile": "?1", "Sec-CH-UA-Platform": '"Android"'}},
        {"name": "iphone_google", "label": "iPhone từ Google", "url": target_url, "user_agent": IPHONE_UA, "referrer": GOOGLE_REFERRER, "client_hints": {}},
        {"name": "googlebot_smartphone", "label": "Googlebot smartphone", "url": target_url, "user_agent": GOOGLEBOT_SMARTPHONE_UA, "referrer": "", "client_hints": {"Sec-CH-UA-Mobile": "?1", "Sec-CH-UA-Platform": '"Android"'}},
    ]
    parsed = urlsplit(target_url)
    if include_path_variant and (parsed.path or "/") in {"", "/"}:
        variant = _vi_vn_url(target_url)
        profiles.extend([
            {"name": "desktop_direct_vi_vn", "label": "Desktop /vi-vn/", "url": variant, "user_agent": DESKTOP_UA, "referrer": "", "client_hints": {"Sec-CH-UA-Mobile": "?0", "Sec-CH-UA-Platform": '"Windows"'}},
            {"name": "mobile_google_vi_vn", "label": "Android Google /vi-vn/", "url": variant, "user_agent": MOBILE_UA, "referrer": GOOGLE_REFERRER, "client_hints": {"Sec-CH-UA-Mobile": "?1", "Sec-CH-UA-Platform": '"Android"'}},
        ])
    for vantage in vantage_points or []:
        name = str(vantage.get("name") or "").strip()
        proxy = str(vantage.get("proxy") or "").strip()
        if not name or not proxy:
            continue
        slug = _vantage_slug(name)
        common = {
            "url": target_url, "proxy": proxy, "vantage": name,
            "vantage_country": str(vantage.get("country") or "").strip().upper(),
        }
        profiles.extend([
            {
                **common, "name": f"vantage_{slug}_desktop_direct",
                "label": f"Desktop trực tiếp qua {name}", "user_agent": DESKTOP_UA,
                "referrer": "", "client_hints": {
                    "Sec-CH-UA-Mobile": "?0", "Sec-CH-UA-Platform": '"Windows"',
                },
            },
            {
                **common, "name": f"vantage_{slug}_mobile_google",
                "label": f"Android từ Google qua {name}", "user_agent": MOBILE_UA,
                "referrer": GOOGLE_REFERRER, "client_hints": {
                    "Sec-CH-UA-Mobile": "?1", "Sec-CH-UA-Platform": '"Android"',
                },
            },
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
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Site": "cross-site" if profile.get("referrer") else "none",
    }
    headers.update(profile.get("client_hints") or {})
    if profile.get("referrer"):
        headers["Referer"] = profile["referrer"]
    started = datetime.now(timezone.utc)
    session = requests.Session()
    try:
        response = session.get(
            profile["url"], headers=headers, timeout=timeout, allow_redirects=True,
            verify=False, stream=True,
            proxies={"http": profile["proxy"], "https": profile["proxy"]}
            if profile.get("proxy") else None,
        )
        try:
            raw = _read_limited(response, max_bytes)
            encoding = response.encoding or "utf-8"
            text = raw.decode(encoding, errors="replace")
            parsed = _parse_html(text, response.url)
            safe_headers = _safe_headers(response.headers)
            history = [
                {"status": hop.status_code, "url": hop.url, "location": hop.headers.get("Location", "")}
                for hop in response.history
            ]
            header_blob = " ".join(
                [*(f"{key}:{value}" for key, value in safe_headers.items()), response.url]
                + [f"{hop['url']} {hop['location']}" for hop in history]
            ).casefold()
            resource_blob = " ".join(parsed["scripts"] + parsed["iframes"] + [text[:100_000]]).casefold()
            iocs = sorted({asset for asset in KNOWN_CLOAKING_ASSETS if asset.casefold() in resource_blob})
            mirror_routes = sorted(set(re.findall(r"/mirror-document/(?:desktop|mobile)", header_blob)))
            mirror_header = bool(mirror_routes)
            return {
                "name": profile["name"], "label": profile["label"],
                "requested_url": profile["url"], "referrer": profile.get("referrer", ""),
                "vantage": profile.get("vantage", "local"),
                "vantage_country": profile.get("vantage_country", ""),
                "status_code": response.status_code, "final_url": response.url,
                "redirect_chain": history, "headers": safe_headers,
                "body_bytes": len(raw), "body_sha256": hashlib.sha256(raw).hexdigest(),
                "truncated": len(raw) >= max_bytes, "error": "",
                "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
                "mirror_document_header": mirror_header,
                "mirror_document_routes": mirror_routes, "known_iocs": iocs,
                **parsed,
            }
        finally:
            response.close()
    except requests.RequestException as exc:
        return {
            "name": profile["name"], "label": profile["label"],
            "requested_url": profile["url"], "referrer": profile.get("referrer", ""),
            "vantage": profile.get("vantage", "local"),
            "vantage_country": profile.get("vantage_country", ""),
            "status_code": None, "final_url": "", "redirect_chain": [], "headers": {},
            "body_bytes": 0, "body_sha256": "", "truncated": False,
            "title": "", "visible_text": "", "text_preview": "", "keyword_hits": [],
            "fake_404": False, "forms": 0, "password_inputs": 0, "meta_refresh": "",
            "scripts": [], "iframes": [], "mirror_document_header": False,
            "mirror_document_routes": [], "known_iocs": [],
            "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "error": (
                f"{type(exc).__name__}: proxy request failed"
                if profile.get("proxy") else str(exc)
            ),
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


def _terminal_profile_state(profile: dict) -> str:
    """Identify provider/browser terminal pages that must not become cloaking evidence."""
    headers = profile.get("headers") or {}
    header_blob = " ".join(
        f"{key}:{value}" for key, value in headers.items()
    ).casefold()
    content_blob = " ".join((
        str(profile.get("title") or ""),
        str(profile.get("text_preview") or ""),
        str(profile.get("error") or ""),
    )).casefold()
    is_cloudflare = "cloudflare" in header_blob or "cf-ray" in header_blob
    if is_cloudflare and any(term in content_blob for term in CLOUDFLARE_WARNING_TERMS):
        return "CLOUDFLARE_PHISHING_BLOCK"
    if any(term in content_blob for term in TERMINAL_BROWSER_ERROR_TERMS):
        return "UNREACHABLE_ERROR_PAGE"
    return ""


def _compare_profiles(left: dict, right: dict, signals: list[dict]) -> dict:
    terminal = [
        profile["name"] for profile in (left, right)
        if profile.get("terminal_state")
    ]
    if terminal:
        return {
            "profiles": [left["name"], right["name"]], "available": False,
            "skipped_reason": "terminal_page", "terminal_profiles": terminal,
        }
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


def _analyze_content(profiles: dict[str, dict]) -> dict:
    """Classify exposed sensitive content separately from cloaking behavior."""
    base_profiles = [
        profile for name, profile in profiles.items()
        if not name.endswith("_vi_vn")
        and not profile.get("error")
        and not profile.get("terminal_state")
    ]
    exposed = [profile for profile in base_profiles if profile.get("keyword_hits")]
    matched_terms = sorted({
        term for profile in exposed for term in profile.get("keyword_hits") or []
    })
    if len(exposed) >= 2:
        verdict = "GAMBLING_EXPOSED"
    elif len(exposed) == 1:
        verdict = "PROFILE_DEPENDENT"
    else:
        verdict = "NO_SIGNAL"
    return {
        "verdict": verdict,
        "profiles_exposed": [profile.get("name") for profile in exposed],
        "profiles_available": len(base_profiles),
        "matched_terms": matched_terms,
    }


def _analyze_path_probe(base: dict, variant: dict) -> dict:
    """Describe path discovery without treating two different URLs as cloaking."""
    item = {
        "base_profile": base.get("name"), "variant_profile": variant.get("name"),
        "base_url": base.get("requested_url"), "variant_url": variant.get("requested_url"),
        "status": "UNAVAILABLE", "contributes_to_cloaking": False,
    }
    if variant.get("error"):
        item["error"] = variant.get("error")
        return item
    status_code = variant.get("status_code")
    if status_code == 404 or variant.get("fake_404"):
        item["status"] = "NOT_FOUND"
        return item
    base_terms = set(base.get("keyword_hits") or [])
    variant_terms = set(variant.get("keyword_hits") or [])
    if variant_terms - base_terms:
        item["status"] = "SENSITIVE_CONTENT"
        item["additional_terms"] = sorted(variant_terms - base_terms)
    elif base.get("body_sha256") == variant.get("body_sha256"):
        item["status"] = "SAME_CONTENT"
    else:
        item["status"] = "DIFFERENT_CONTENT"
    return item


def analyze_profiles(profiles: dict[str, dict], target_url: str) -> dict:
    """Classify already-fetched profiles. Kept pure for deterministic tests."""
    signals: list[dict] = []
    base_profiles = {
        name: profile for name, profile in profiles.items()
        if not name.endswith("_vi_vn")
    }
    for profile in base_profiles.values():
        terminal_state = _terminal_profile_state(profile)
        if terminal_state:
            profile["terminal_state"] = terminal_state
        else:
            profile.pop("terminal_state", None)
    for profile in base_profiles.values():
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
        ("desktop_direct", "iphone_google"),
        ("desktop_direct", "googlebot_smartphone"),
    ]
    vantage_slugs = sorted({
        name.removeprefix("vantage_").removesuffix("_desktop_direct").removesuffix("_mobile_google")
        for name in profiles if name.startswith("vantage_")
        and (name.endswith("_desktop_direct") or name.endswith("_mobile_google"))
    })
    for slug in vantage_slugs:
        if f"vantage_{slug}_desktop_direct" in profiles:
            pairs.append((f"vantage_{slug}_desktop_direct", f"vantage_{slug}_mobile_google"))
        pairs.append(("mobile_google", f"vantage_{slug}_mobile_google"))
    for left_name, right_name in pairs:
        if left_name in profiles and right_name in profiles:
            comparisons.append(_compare_profiles(profiles[left_name], profiles[right_name], signals))

    path_probes = []
    for base_name, variant_name in (
        ("desktop_direct", "desktop_direct_vi_vn"),
        ("mobile_google", "mobile_google_vi_vn"),
    ):
        if base_name in profiles and variant_name in profiles:
            path_probes.append(_analyze_path_probe(profiles[base_name], profiles[variant_name]))

    terminal_profiles = {
        name: profile.get("terminal_state")
        for name, profile in base_profiles.items()
        if profile.get("terminal_state")
    }
    all_profiles_terminal = bool(base_profiles) and len(terminal_profiles) == len(base_profiles)
    available = sum(
        not item.get("error") or bool(item.get("terminal_state"))
        for item in base_profiles.values()
    )
    failures = len(base_profiles) - available
    score = min(100, sum(item["weight"] for item in signals))
    kinds = {item["kind"] for item in signals}
    exact_ioc = bool(kinds.intersection({"mirror_document_header", "known_cloaking_ioc"}))
    independent_kinds = kinds.intersection({
        "keyword_exposure", "fake_404_vs_sensitive", "redirect_difference",
        "form_difference", "content_difference", "title_difference", "size_difference",
    })
    if all_profiles_terminal:
        signals = []
        score = 0
        verdict = "NO_SIGNAL"
    elif exact_ioc or (score >= 50 and len(independent_kinds) >= 2):
        verdict = "LIKELY"
    elif score >= 20:
        verdict = "POSSIBLE"
    elif available < 2 or failures >= max(2, len(base_profiles) // 2):
        verdict = "INCONCLUSIVE"
    else:
        verdict = "NO_SIGNAL"
    vary_tokens = sorted({
        token.strip().casefold()
        for profile in base_profiles.values()
        for key, value in (profile.get("headers") or {}).items()
        if str(key).casefold() == "vary"
        for token in str(value).split(",")
        if token.strip()
    })
    geo_vary = bool({"cf-ipcountry", "x-country-code"}.intersection(vary_tokens))
    device_vary = bool(
        {"user-agent", "sec-ch-ua-mobile", "sec-ch-ua-platform"}.intersection(vary_tokens)
    )
    vantage_profiles = [
        profile for name, profile in base_profiles.items() if name.startswith("vantage_")
    ]
    vantages_attempted = sorted({
        str(profile.get("vantage")) for profile in vantage_profiles if profile.get("vantage")
    })
    vantages_available = sorted({
        str(profile.get("vantage")) for profile in vantage_profiles
        if profile.get("vantage") and not profile.get("error")
    })
    return {
        "version": 1, "engine": "http", "target_url": target_url,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict, "score": score, "signals": signals,
        "profiles": profiles, "comparisons": comparisons,
        "content": _analyze_content(profiles), "path_probes": path_probes,
        "site_state": {
            "verdict": (
                "BLOCKED_OR_UNAVAILABLE" if all_profiles_terminal
                else "PARTIAL_TERMINAL" if terminal_profiles
                else "ACTIVE_OR_UNKNOWN"
            ),
            "all_profiles_terminal": all_profiles_terminal,
            "terminal_profiles": terminal_profiles,
        },
        "coverage": {
            "vary_tokens": vary_tokens,
            "geo_dependent_declared": geo_vary,
            "device_dependent_declared": device_vary,
            "multi_vantage_recommended": bool(
                geo_vary and device_vary and not vantages_available
            ),
            "vantages_attempted": vantages_attempted,
            "vantages_available": vantages_available,
        },
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
    vantage_points: list[dict] | None = None,
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
    specs = _profile_specs(target_url, include_path_variant, vantage_points)
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
                    "referrer": spec.get("referrer", ""),
                    "error": (
                        f"{type(exc).__name__}: proxy request failed"
                        if spec.get("proxy") else str(exc)
                    ),
                    "status_code": None,
                    "vantage": spec.get("vantage", "local"),
                    "vantage_country": spec.get("vantage_country", ""),
                    "final_url": "", "body_bytes": 0, "body_sha256": "", "title": "",
                    "visible_text": "", "text_preview": "", "keyword_hits": [],
                    "fake_404": False, "forms": 0, "password_inputs": 0,
                    "known_iocs": [], "mirror_document_header": False,
                }
    ordered = {spec["name"]: profiles[spec["name"]] for spec in specs}
    result = analyze_profiles(ordered, target_url)
    if evidence_root and (
        result["verdict"] != "NO_SIGNAL"
        or result.get("coverage", {}).get("vantages_attempted")
        or result.get("coverage", {}).get("multi_vantage_recommended")
    ):
        try:
            result["evidence_path"] = save_evidence_manifest(result, evidence_root)
        except OSError as exc:
            result["evidence_error"] = str(exc)
    # Full visible text is needed only while comparing profiles. Do not retain it
    # in Streamlit session state or expose it to the browser payload.
    for profile in result.get("profiles", {}).values():
        profile.pop("visible_text", None)
    return result


def _playwright_proxy_settings(proxy_url: str) -> dict | None:
    parsed = urlsplit(str(proxy_url or "").strip())
    if not parsed.scheme or not parsed.hostname or not parsed.port:
        return None
    settings = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        settings["username"] = unquote(parsed.username)
    if parsed.password:
        settings["password"] = unquote(parsed.password)
    return settings


def _browser_profile_specs(
    target_url: str, vantage_points: list[dict] | None = None,
) -> list[dict]:
    profiles = [
        {
            "name": "desktop_direct", "label": "Playwright desktop trực tiếp",
            "url": target_url, "user_agent": DESKTOP_UA, "referrer": "",
            "viewport": {"width": 1440, "height": 1000}, "is_mobile": False,
            "has_touch": False,
        },
        {
            "name": "mobile_google", "label": "Playwright mobile từ Google",
            "url": target_url, "user_agent": MOBILE_UA, "referrer": GOOGLE_REFERRER,
            "viewport": {"width": 412, "height": 915}, "is_mobile": True,
            "has_touch": True,
        },
        {
            "name": "iphone_google", "label": "Playwright iPhone từ Google",
            "url": target_url, "user_agent": IPHONE_UA, "referrer": GOOGLE_REFERRER,
            "viewport": {"width": 390, "height": 844}, "is_mobile": True,
            "has_touch": True,
        },
    ]
    for vantage in vantage_points or []:
        if not vantage.get("browser"):
            continue
        proxy_settings = _playwright_proxy_settings(vantage.get("proxy", ""))
        if not proxy_settings:
            continue
        name = str(vantage.get("name") or "remote").strip()
        profiles.append({
            "name": f"vantage_{_vantage_slug(name)}_mobile_google",
            "label": f"Playwright Android từ Google qua {name}",
            "url": target_url, "user_agent": MOBILE_UA, "referrer": GOOGLE_REFERRER,
            "viewport": {"width": 412, "height": 915}, "is_mobile": True,
            "has_touch": True, "proxy_settings": proxy_settings,
            "vantage": name,
            "vantage_country": str(vantage.get("country") or "").strip().upper(),
        })
    return profiles


def _browser_snapshot(page, label: str) -> dict:
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
    raw = html.encode("utf-8", errors="replace")
    return {
        "stage": label, "final_url": page.url, "body_bytes": len(raw),
        "body_sha256": hashlib.sha256(raw).hexdigest(), **parsed,
    }


def _capture_browser_profile(browser, profile: dict, target_dir: str, timeout_ms: int) -> dict:
    started = datetime.now(timezone.utc)
    context_kwargs = dict(
        user_agent=profile["user_agent"], viewport=profile["viewport"],
        is_mobile=profile["is_mobile"], has_touch=profile.get("has_touch", False),
        locale="vi-VN", accept_downloads=False, service_workers="allow",
    )
    if profile.get("proxy_settings"):
        context_kwargs["proxy"] = profile["proxy_settings"]
    context = None
    screenshot_paths = []
    try:
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        response = page.goto(
            profile["url"], wait_until="domcontentloaded", timeout=timeout_ms,
            referer=profile.get("referrer") or None,
        )
        observations = []
        for stage, delay_ms in (("cold_1s", 1000), ("cold_5s", 4000)):
            page.wait_for_timeout(delay_ms)
            observations.append(_browser_snapshot(page, stage))
            screenshot_path = os.path.join(target_dir, f"{profile['name']}_{stage}.png")
            page.screenshot(path=screenshot_path, full_page=True)
            screenshot_paths.append(screenshot_path)
        try:
            warm_response = page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
            if warm_response is not None:
                response = warm_response
            page.wait_for_timeout(1000)
            observations.append(_browser_snapshot(page, "warm_reload"))
            screenshot_path = os.path.join(target_dir, f"{profile['name']}_warm_reload.png")
            page.screenshot(path=screenshot_path, full_page=True)
            screenshot_paths.append(screenshot_path)
        except Exception:
            pass
        parsed = max(
            observations,
            key=lambda item: (len(item.get("keyword_hits") or []), not item.get("fake_404"), item.get("body_bytes", 0)),
        )
        try:
            resources = page.evaluate(
                "() => performance.getEntriesByType('resource').map(entry => entry.name).slice(0, 500)"
            ) or []
        except Exception:
            resources = []
        resource_blob = " ".join([page.content()[:100_000], *map(str, resources)]).casefold()
        known_iocs = sorted({asset for asset in KNOWN_CLOAKING_ASSETS if asset.casefold() in resource_blob})
        safe_observations = []
        for observation in observations:
            safe_observation = dict(observation)
            safe_observation.pop("visible_text", None)
            safe_observations.append(safe_observation)
        return {
            "name": profile["name"], "label": profile["label"],
            "requested_url": profile["url"], "referrer": profile.get("referrer", ""),
            "vantage": profile.get("vantage", "local"),
            "vantage_country": profile.get("vantage_country", ""),
            "status_code": response.status if response else None, "final_url": parsed["final_url"],
            "redirect_chain": [], "headers": {}, "body_bytes": parsed["body_bytes"],
            "body_sha256": parsed["body_sha256"], "truncated": False,
            "error": "", "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "mirror_document_header": False, "mirror_document_routes": [],
            "known_iocs": known_iocs, "observations": safe_observations,
            "resources": [str(item)[:2000] for item in resources],
            "screenshot_path": screenshot_paths[-1] if screenshot_paths else "",
            "screenshot_paths": screenshot_paths, **{
                key: value for key, value in parsed.items()
                if key not in {"stage", "final_url", "body_bytes", "body_sha256"}
            },
        }
    except Exception as exc:
        return {
            "name": profile["name"], "label": profile["label"],
            "requested_url": profile["url"], "referrer": profile.get("referrer", ""),
            "vantage": profile.get("vantage", "local"),
            "vantage_country": profile.get("vantage_country", ""),
            "status_code": None, "final_url": "", "redirect_chain": [], "headers": {},
            "body_bytes": 0, "body_sha256": "", "truncated": False,
            "title": "", "visible_text": "", "text_preview": "", "keyword_hits": [],
            "fake_404": False, "forms": 0, "password_inputs": 0, "meta_refresh": "",
            "scripts": [], "iframes": [], "mirror_document_header": False,
            "mirror_document_routes": [], "known_iocs": [], "observations": [],
            "resources": [], "screenshot_path": "", "screenshot_paths": screenshot_paths,
            "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "error": (
                f"{type(exc).__name__}: browser proxy request failed"
                if profile.get("proxy_settings") else str(exc)
            ),
        }
    finally:
        if context is not None:
            context.close()


def probe_playwright_cloaking(
    target: str, *, timeout_ms: int = 15_000, evidence_root: str | None = None,
    vantage_points: list[dict] | None = None, _playwright_factory=None,
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
            "screenshots": [], "evidence_path": "", "available": False,
            "error": (
                f"{type(exc).__name__}: browser proxy/profile failed"
                if vantage_points else str(exc)
            ),
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
                for spec in _browser_profile_specs(target_url, vantage_points):
                    profiles[spec["name"]] = _capture_browser_profile(browser, spec, target_dir, timeout_ms)
            finally:
                browser.close()
    except Exception as exc:
        return {
            "version": 1, "engine": "playwright", "target_url": target_url,
            "observed_at": datetime.now(timezone.utc).isoformat(), "verdict": "INCONCLUSIVE",
            "score": 0, "signals": [], "profiles": profiles, "comparisons": [],
            "profiles_available": 0, "profiles_failed": 2, "manual_review_required": True,
            "screenshots": [], "evidence_path": "", "available": False,
            "error": (
                f"{type(exc).__name__}: browser proxy/profile failed"
                if vantage_points else str(exc)
            ),
        }
    result = analyze_profiles(profiles, target_url)
    result.update({"engine": "playwright", "available": True})
    result["screenshots"] = [
        {
            "label": f"{profile.get('label')} — {os.path.splitext(os.path.basename(path))[0].rsplit('_', 1)[-1]}",
            "path": path,
        }
        for profile in profiles.values()
        for path in profile.get("screenshot_paths") or []
        if path
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
    browser_terminal = bool(
        ((browser_result or {}).get("site_state") or {}).get("all_profiles_terminal")
    )
    if browser_terminal:
        merged["verdict"] = "NO_SIGNAL"
        merged["score"] = 0
        merged["signals"] = []
        merged["manual_review_required"] = False
        merged["site_state"] = deepcopy(browser_result.get("site_state") or {})
        merged.setdefault("coverage", {})["multi_vantage_recommended"] = False
        merged["coverage"]["suppressed_by_terminal_state"] = True
    elif browser_verdict == "LIKELY":
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


def _image_extension(filename: str, data: bytes) -> str:
    lower = str(filename or "").casefold()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if lower.endswith((".png", ".jpg", ".jpeg", ".webp")):
        raise ValueError("The uploaded file extension looks like an image but its signature is invalid")
    raise ValueError("Only PNG, JPEG, and WebP evidence images are supported")


def add_operator_evidence(
    result: dict,
    *,
    images: list[tuple[str, bytes]],
    evidence_root: str,
    acquisition_url: str = "",
    device: str = "",
    network: str = "",
    confirmed_difference: bool = False,
) -> dict:
    """Persist operator screenshots and mark the case for review, never auto-confirm it."""
    if len(images) > 4:
        raise ValueError("At most four operator screenshots are allowed")
    target_url = _ensure_url(result.get("target_url", ""))
    if not target_url:
        raise ValueError("A valid checked URL is required before adding operator evidence")
    parsed = urlsplit(target_url)
    safe_domain = re.sub(r"[^a-zA-Z0-9._-]+", "_", parsed.hostname or "unknown")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target_dir = os.path.join(evidence_root, safe_domain, f"{timestamp}_operator")
    os.makedirs(target_dir, exist_ok=True)
    saved = []
    for index, (filename, data) in enumerate(images, start=1):
        payload = bytes(data or b"")
        if not payload or len(payload) > 10 * 1024 * 1024:
            raise ValueError("Each evidence image must be between 1 byte and 10 MB")
        extension = _image_extension(filename, payload)
        path = os.path.join(target_dir, f"operator_{index}{extension}")
        with open(path, "wb") as evidence_file:
            evidence_file.write(payload)
        saved.append({"path": path, "original_name": os.path.basename(filename)[:200]})
    operator_evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "acquisition_url": _ensure_url(acquisition_url) if acquisition_url else "",
        "device": str(device or "").strip()[:100],
        "network": str(network or "").strip()[:100],
        "confirmed_difference": bool(confirmed_difference),
        "screenshots": saved,
    }
    merged = merge_operator_evidence(result, operator_evidence)
    merged["engine"] = "http_operator"
    merged["evidence_path"] = save_evidence_manifest(merged, evidence_root)
    merged["operator_evidence"]["manifest_path"] = merged["evidence_path"]
    return merged


def merge_operator_evidence(result: dict, operator_evidence: dict) -> dict:
    """Merge already-persisted operator evidence, for worker resume/retry."""
    merged = deepcopy(result)
    merged["operator_evidence"] = deepcopy(operator_evidence or {})
    manifest_path = str(merged["operator_evidence"].get("manifest_path") or "")
    if manifest_path:
        merged["evidence_path"] = manifest_path
    saved = merged["operator_evidence"].get("screenshots") or []
    confirmed_difference = bool(merged["operator_evidence"].get("confirmed_difference"))
    if confirmed_difference and len(saved) >= 2:
        if merged.get("verdict") == "NO_SIGNAL":
            merged["verdict"] = "POSSIBLE"
            merged["score"] = max(20, int(merged.get("score", 0)))
        merged["manual_review_required"] = True
        _add_signal(
            merged.setdefault("signals", []), "operator_reported_difference", 0, [],
            "Người vận hành cung cấp cặp ảnh cho thấy nội dung khác nhau",
        )
    return merged


def _email_profile_label(profile: dict) -> str:
    name = str(profile.get("name") or "")
    if name.startswith("vantage_"):
        kind = "Mobile, Google referrer" if name.endswith("mobile_google") else "Desktop, direct visit"
        return f"{kind}, remote vantage {profile.get('vantage') or 'unknown'}"
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
    if kind == "operator_reported_difference":
        return "An operator supplied paired screenshots showing different content for the same URL."
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
    operator = result.get("operator_evidence") or {}
    if operator:
        lines.extend([
            "", "Operator-supplied verification:",
            f"- Paired difference confirmed by operator: {bool(operator.get('confirmed_difference'))}",
        ])
        if operator.get("acquisition_url"):
            lines.append(f"- Acquisition URL: {operator['acquisition_url']}")
        if operator.get("device"):
            lines.append(f"- Device: {operator['device']}")
        if operator.get("network"):
            lines.append(f"- Network/vantage: {operator['network']}")
        for screenshot in operator.get("screenshots") or []:
            if screenshot.get("path"):
                lines.append(f"- Operator screenshot attachment: {os.path.basename(screenshot['path'])}")
    if result.get("evidence_path"):
        lines.extend(["", f"Local evidence manifest: {os.path.basename(result['evidence_path'])}"])
    lines.extend([
        "",
        "Please reproduce the behavior using the user-agent and referrer profiles above. "
        "The assessment is based on observed response differences and does not rely on response size alone.",
        "--- End of Cloaking Evidence ---",
    ])
    return "\n".join(lines)
