"""HTTP status checker used by the Streamlit link-list page."""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

import requests


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
    )
}

_HOLD_FLAGS = {"clienthold", "serverhold"}


def check_domain_hold_status(domain: str) -> dict:
    """Tra WHOIS để kiểm tra domain có bị clientHold / serverHold không.

    Trả về dict:
        {
            "hold": bool,           # True nếu có ít nhất 1 hold flag
            "flags": list[str],     # Danh sách flag tìm thấy (vd ["serverHold"])
            "raw_status": list[str] # Toàn bộ status WHOIS trả về
            "error": str            # Mô tả lỗi nếu WHOIS thất bại
        }
    """
    try:
        import whois as _whois
        # Chỉ lấy domain gốc (bỏ subdomain và path)
        parts = domain.lower().split("/")[0].split("?")[0].split("#")[0]
        labels = parts.split(".")
        # Dùng 2 nhãn cuối làm registerable domain để tra WHOIS
        query_domain = ".".join(labels[-2:]) if len(labels) >= 2 else parts
        data = _whois.whois(query_domain)
        raw = data.status or []
        if isinstance(raw, str):
            raw = [raw]
        raw_lower = [s.lower().split(" ")[0] for s in raw]  # bỏ phần URL sau khoảng trắng
        found = [orig for orig, low in zip(raw, raw_lower) if low in _HOLD_FLAGS]
        return {
            "hold": bool(found),
            "flags": found,
            "raw_status": list(raw),
            "error": "",
        }
    except Exception as exc:
        return {"hold": False, "flags": [], "raw_status": [], "error": str(exc)}


def _candidate_urls(target: str) -> list[str]:
    target = target.strip()
    if urlsplit(target).scheme.lower() in {"http", "https"}:
        return [target]
    return [f"https://{target}", f"http://{target}"]


def _classify_response(response, body_preview: str = "") -> tuple[str, str, str]:
    """Return conservative status, reason and detected protection provider."""
    code = response.status_code
    headers = response.headers if isinstance(response.headers, Mapping) else {}
    server = str(headers.get("Server", "")).lower()
    provider = "Cloudflare" if headers.get("CF-Ray") or "cloudflare" in server else ""
    normalized_body = body_preview.lower()

    # Detect trang cảnh báo Cloudflare (phishing hoặc malware) — áp dụng với mọi status code
    cf_warning = provider == "Cloudflare" and any(
        kw in normalized_body for kw in ("suspected phishing", "suspected malware", "deceptive site", "reported for potential phishing", "reported for potential malware")
    )
    if cf_warning:
        if "malware" in normalized_body:
            warn_type = "Suspected Malware"
        elif "deceptive" in normalized_body:
            warn_type = "Deceptive Site"
        else:
            warn_type = "Suspected Phishing"
        return "BLOCKED", f"Cloudflare đang hiển thị trang cảnh báo: {warn_type}", "Cloudflare"

    # Detect geo-block / IP-block — site vẫn live nhưng chặn vùng/IP này
    _GEO_BODY_KW = (
        "not available in your country", "not available in your region",
        "unavailable in your country", "unavailable in your region",
        "not available in your area", "geo-restricted", "geo restricted",
        "access denied based on your location", "blocked in your country",
        "blocked in your region", "country is not supported",
        "service is not available in your location",
        "content is not available in your region",
    )
    _GEO_HEADER_KW = ("x-geo-block", "x-country-block", "x-region-block")
    geo_in_body = any(kw in normalized_body for kw in _GEO_BODY_KW)
    geo_in_headers = any(h in {k.lower() for k in headers} for h in _GEO_HEADER_KW)
    # 451 = Unavailable For Legal Reasons (thường dùng cho block vùng)
    if code == 451 or geo_in_body or geo_in_headers:
        return "GEO-BLOCK", f"HTTP {code}: site chặn theo vùng/IP — cần kiểm tra qua proxy/VPN để xác nhận còn live không", provider

    if 200 <= code < 400:
        return "LIVE", f"HTTP {code}: URL phản hồi thành công", provider
    if code in {401, 403, 407, 418, 423, 429}:
        # 403 từ Cloudflare (không phải warning page) = CF chặn bot checker, site vẫn LIVE với browser thật
        protection = f" ({provider}/WAF)" if provider else ""
        return "BLOCKED", f"HTTP {code}: máy chủ còn phản hồi nhưng đang chặn hoặc giới hạn{protection}", provider
    if code in {404, 410}:
        return "DIE", f"HTTP {code}: trang không tồn tại hoặc đã bị gỡ", provider
    if code in {408, 425} or 500 <= code < 600:
        return "TEMP ERROR", f"HTTP {code}: lỗi tạm thời từ máy chủ, chưa đủ kết luận die", provider
    return "UNKNOWN", f"HTTP {code}: có phản hồi nhưng chưa đủ cơ sở phân loại", provider


def check_link(target: str, timeout: float = 10.0, timeout_retries: int = 3) -> dict:
    """Check one URL, following redirects while retaining every redirect hop."""
    last_error = ""
    timeout_attempts = 0
    connection_attempts = 0
    dns_error = False
    total_timeout_slots = 0
    for request_url in _candidate_urls(target):
        total_timeout_slots += timeout_retries
        for _attempt in range(timeout_retries):
            try:
                response = requests.get(
                    request_url,
                    allow_redirects=True,
                    timeout=timeout,
                    headers=DEFAULT_HEADERS,
                    verify=False,
                    stream=True,
                )
                history = [
                    {
                        "status": hop.status_code,
                        "url": hop.url,
                        "location": hop.headers.get("Location", ""),
                    }
                    for hop in response.history
                ]
                status_code = response.status_code
                body_preview = ""
                content_type = str(response.headers.get("Content-Type", "")).lower()
                has_cf = bool(response.headers.get("CF-Ray"))
                # Đọc body khi: content-type html, HOẶC có CF-Ray header (để detect CF warning page)
                if "html" in content_type or has_cf:
                    try:
                        preview = bytearray()
                        for chunk in response.iter_content(chunk_size=8192):
                            preview.extend(chunk)
                            if len(preview) >= 262_144:
                                break
                        body_preview = bytes(preview[:262_144]).decode(
                            response.encoding or "utf-8", errors="ignore"
                        )
                    except (OSError, TypeError, requests.RequestException):
                        body_preview = ""
                status, reason, provider = _classify_response(response, body_preview)
                return {
                    "input": target,
                    "request_url": request_url,
                    "status": status,
                    "status_code": status_code,
                    "redirect_codes": " → ".join(str(hop["status"]) for hop in history),
                    "redirect_count": len(history),
                    "final_url": response.url,
                    "redirect_chain": history,
                    "provider": provider,
                    "reason": reason,
                    "error": "",
                }
            except requests.Timeout as exc:
                timeout_attempts += 1
                last_error = f"Timeout: {exc}"
                continue
            except requests.exceptions.SSLError as exc:
                last_error = f"SSL error: {exc}"
                break
            except requests.ConnectionError as exc:
                connection_attempts += 1
                last_error = f"Connection error: {exc}"
                error_text = str(exc).lower()
                dns_error = dns_error or any(
                    marker in error_text
                    for marker in ("nameresolutionerror", "failed to resolve", "getaddrinfo")
                )
                continue
            except requests.RequestException as exc:
                last_error = str(exc)
                break

    failed_attempts = timeout_attempts + connection_attempts
    unreachable = failed_attempts > 0 and failed_attempts == total_timeout_slots
    failure_type = (
        "DNS ERROR"
        if dns_error
        else "TIMEOUT"
        if timeout_attempts == failed_attempts
        else "CONNECTION ERROR"
    )

    # Khi DNS ERROR → tra WHOIS kiểm tra clientHold / serverHold
    hold_info: dict = {"hold": False, "flags": [], "raw_status": [], "error": ""}
    if dns_error and unreachable:
        hold_info = check_domain_hold_status(target)

    hold_note = ""
    if hold_info.get("hold"):
        flags_str = ", ".join(hold_info["flags"])
        hold_note = f" | WHOIS HOLD: {flags_str}"

    return {
        "input": target,
        "request_url": _candidate_urls(target)[-1],
        "status": "UNREACHABLE" if unreachable else "UNKNOWN",
        "status_code": None,
        "redirect_codes": "",
        "redirect_count": 0,
        "final_url": "",
        "redirect_chain": [],
        "provider": "",
        "reason": (
            f"{failure_type} {failed_attempts}/{total_timeout_slots}: "
            "đã thử lại nhưng link không thể truy cập"
            if unreachable
            else "Không kết nối được từ IP/mạng hiện tại; có thể do chặn mạng, DNS hoặc máy chủ tạm lỗi"
        ),
        "failure_type": failure_type,
        "failure_attempts": failed_attempts,
        "failure_total": total_timeout_slots,
        "error": last_error,
        "hold_info": hold_info,
        "hold_note": hold_note,
    }


def check_links(targets: list[str], timeout: float = 10.0, max_workers: int = 10) -> list[dict]:
    """Check a list concurrently and return results in the original order."""
    if not targets:
        return []
    worker_count = min(max(1, max_workers), len(targets))
    results: list[dict | None] = [None] * len(targets)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(check_link, target, timeout): index
            for index, target in enumerate(targets)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return [result for result in results if result is not None]


def counts_as_reported_down(result: dict) -> bool:
    """Whether a result belongs in the user's reported/down export."""
    return result.get("status") == "DIE" or (
        result.get("status") == "BLOCKED"
        and result.get("provider") == "Cloudflare"
    )


def should_show_result(result: dict) -> bool:
    """Show only dead/blocked links or links involving a 301/302 redirect."""
    redirect_codes = {
        hop.get("status") for hop in result.get("redirect_chain", [])
    }
    return (
        result.get("status") in {"DIE", "BLOCKED", "UNREACHABLE", "GEO-BLOCK"}
        or bool(redirect_codes.intersection({301, 302}))
    )


def format_result_note(result: dict) -> str:
    """Format status and the complete HTTP chain on one concise line."""
    status = result.get("status", "UNKNOWN")
    provider = result.get("provider", "")
    redirect_codes = [
        str(hop.get("status")) for hop in result.get("redirect_chain", [])
    ]
    final_code = result.get("status_code")
    http_chain = redirect_codes + ([str(final_code)] if final_code is not None else [])

    if status == "DIE":
        label = "DIE"
    elif status == "BLOCKED":
        label = "BLOCKED" + (f" {provider}" if provider else "")
    elif status == "GEO-BLOCK":
        label = "GEO-BLOCK" + (f" ({provider})" if provider else "")
    elif status == "UNREACHABLE":
        attempts = result.get("failure_attempts", 0)
        total = result.get("failure_total", attempts)
        failure_type = result.get("failure_type", "CONNECTION ERROR")
        hold_note = result.get("hold_note", "")
        label = f"UNREACHABLE | {failure_type} {attempts}/{total}{hold_note}"
    else:
        label = "REDIRECT"

    note = f"{label} | HTTP {' → '.join(http_chain)}" if http_chain else label
    history = result.get("redirect_chain", [])
    if history:
        # Mỗi hop tiếp theo và final_url là các URL đích của chuỗi redirect.
        redirect_urls = [
            str(hop.get("url"))
            for hop in history[1:]
            if hop.get("url")
        ]
        final_url = result.get("final_url")
        if final_url and (not redirect_urls or redirect_urls[-1] != final_url):
            redirect_urls.append(str(final_url))
        if redirect_urls:
            note += f" | REDIRECT URL: {' → '.join(redirect_urls)}"
    return note
