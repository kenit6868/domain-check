"""Bounded, read-only HTTP probes for suspected cloaking evidence."""

from __future__ import annotations

import hashlib
import ipaddress
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
from urllib.parse import urljoin, urlsplit

from cloaking_report import normalize_report_url


MAX_REDIRECTS = 5
MAX_BODY_BYTES = 1_000_000
DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"
MOBILE_UA = "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 Chrome/128 Mobile Safari/537.36"
SAFE_RESPONSE_HEADERS = {
    "content-type", "content-length", "location", "server", "via", "cf-ray",
    "x-matched-path", "x-vercel-id", "cache-control",
}
CURL_PATH = shutil.which("curl.exe") or shutil.which("curl")
TRAFFIC_FINGERPRINT = "best-traffic.pages.dev/traffic_dr.js"


def validate_public_url(value: str) -> str:
    """Reject non-HTTP and private/reserved destinations before each request."""
    url = normalize_report_url(value)
    host = urlsplit(url).hostname or ""
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raise ValueError(f"Không phân giải được tên miền: {exc}") from exc
    if not addresses:
        raise ValueError("Tên miền không có địa chỉ IP.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("Tên miền trả về địa chỉ IP không hợp lệ.") from exc
        if not ip.is_global:
            raise ValueError("Từ chối truy cập IP nội bộ, loopback hoặc reserved.")
    return url


def _fetch_variant(start_url: str, *, user_agent: str, referrer: str = "") -> dict:
    if not CURL_PATH:
        raise OSError("Không tìm thấy curl/curl.exe trong PATH.")
    current = validate_public_url(start_url)
    chain = []
    commands = []
    body = b""
    response_headers = {}
    truncated = False
    for redirect_index in range(MAX_REDIRECTS + 1):
        with tempfile.TemporaryDirectory(prefix="cloaking-curl-") as temp_dir:
            header_path = Path(temp_dir) / "headers.txt"
            body_path = Path(temp_dir) / "body.bin"
            args = [
                CURL_PATH,
                "--silent", "--show-error",
                "--proto", "=http,https",
                "--connect-timeout", "5",
                "--max-time", "20",
                "--max-filesize", str(MAX_BODY_BYTES),
                "--user-agent", user_agent,
                "--header", "Accept: text/html,application/xhtml+xml",
                "--dump-header", str(header_path),
                "--output", str(body_path),
                "--write-out", "%{http_code}",
            ]
            if referrer:
                args.extend(["--referer", referrer])
            args.extend(["--url", current])
            commands.append(subprocess.list2cmdline(args))
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            # curl 63 means the response exceeded --max-filesize; retain the bounded partial evidence.
            if completed.returncode not in {0, 63}:
                detail = " ".join((completed.stderr or "curl failed").split())[:500]
                raise OSError(f"curl exit {completed.returncode}: {detail}")
            status_text = (completed.stdout or "").strip()[-3:]
            if not status_text.isdigit():
                raise OSError("curl không trả HTTP status hợp lệ.")
            status = int(status_text)
            raw_headers = header_path.read_bytes().decode("iso-8859-1", errors="replace") if header_path.exists() else ""
            blocks = []
            current_block = []
            for header_line in raw_headers.splitlines():
                if header_line.startswith("HTTP/"):
                    if current_block:
                        blocks.append(current_block)
                    current_block = [header_line]
                elif current_block and header_line:
                    current_block.append(header_line)
                elif current_block:
                    blocks.append(current_block)
                    current_block = []
            if current_block:
                blocks.append(current_block)
            final_block = blocks[-1] if blocks else []
            parsed_headers = {}
            for line in final_block[1:]:
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                parsed_headers[key.strip().lower()] = value.strip()
            response_headers = {
                key: value for key, value in parsed_headers.items()
                if key in SAFE_RESPONSE_HEADERS
            }
            body = body_path.read_bytes()[:MAX_BODY_BYTES] if body_path.exists() else b""
            truncated = completed.returncode == 63 or (body_path.exists() and body_path.stat().st_size > MAX_BODY_BYTES)

        location = parsed_headers.get("location", "")
        chain.append({"url": current, "status": status, "location": location})
        if status in {301, 302, 303, 307, 308} and location:
            if redirect_index >= MAX_REDIRECTS:
                raise ValueError(f"Vượt quá {MAX_REDIRECTS} redirect.")
            current = validate_public_url(urljoin(current, location))
            continue
        break
    html = body.decode("utf-8", errors="replace")
    return {
        "status": chain[-1]["status"],
        "final_url": current,
        "redirect_chain": chain,
        "headers": response_headers,
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "html": html,
        "truncated": truncated,
        "commands": commands,
        "traffic_fingerprint": TRAFFIC_FINGERPRINT in html,
    }


def _variant_summary(name: str, result: dict) -> str:
    if result.get("error"):
        return f"[{name}] ERROR: {result['error']}"
    chain = " -> ".join(str(item["status"]) for item in result["redirect_chain"])
    headers = "; ".join(f"{key}: {value}" for key, value in sorted(result["headers"].items())) or "(không có header chọn lọc)"
    return (
        f"[{name}] status-chain={chain}; final={result['final_url']}; "
        f"bytes={result['size']}; sha256={result['sha256']}; truncated={result['truncated']}; "
        f"traffic_dr.js={result.get('traffic_fingerprint', False)}\n"
        f"headers: {headers}\ncommands:\n" + "\n".join(result.get("commands", []))
    )


def _materially_different(first: tuple, second: tuple) -> bool:
    """Ignore small dynamic HTML changes while catching routing/content changes."""
    first_status, first_url, first_hash, first_size = first
    second_status, second_url, second_hash, second_size = second
    if first_status != second_status or first_url != second_url:
        return True
    if first_hash == second_hash:
        return False
    largest = max(first_size, second_size, 1)
    return abs(first_size - second_size) >= 500 and abs(first_size - second_size) / largest >= 0.20


def collect_cloaking_evidence(url: str) -> dict:
    """Run four isolated request profiles and return summaries plus captured HTML."""
    profiles = {
        "desktop_direct": (DESKTOP_UA, ""),
        "mobile_direct": (MOBILE_UA, ""),
        "desktop_google": (DESKTOP_UA, "https://www.google.com/"),
        "mobile_google": (MOBILE_UA, "https://www.google.com/"),
    }
    results = {}
    for name, (user_agent, referrer) in profiles.items():
        try:
            results[name] = _fetch_variant(url, user_agent=user_agent, referrer=referrer)
        except (subprocess.SubprocessError, ValueError, OSError) as exc:
            results[name] = {"error": str(exc)}

    successful = {name: item for name, item in results.items() if not item.get("error")}
    signatures = {
        name: (item["status"], item["final_url"], item["sha256"], item["size"])
        for name, item in successful.items()
    }
    device_difference = (
        _materially_different(signatures["desktop_direct"], signatures["mobile_direct"])
        if "desktop_direct" in signatures and "mobile_direct" in signatures else False
    )
    referrer_difference = any(
        _materially_different(signatures[f"{device}_direct"], signatures[f"{device}_google"])
        for device in ("desktop", "mobile")
        if f"{device}_direct" in signatures and f"{device}_google" in signatures
    )
    matched_paths = {
        name: item.get("headers", {}).get("x-matched-path", "")
        for name, item in successful.items()
    }
    matched_path_difference = len({value for value in matched_paths.values() if value}) > 1
    fingerprint_profiles = [
        name for name, item in successful.items() if item.get("traffic_fingerprint")
    ]
    reportable_cloaking = bool(device_difference or referrer_difference or matched_path_difference)
    summary = "\n\n".join(_variant_summary(name, result) for name, result in results.items())
    reproduction = (
        "Đã tự động chạy curl read-only với bốn profile: desktop/mobile, mỗi loại gồm direct và "
        "Google referrer. Không chạy JavaScript, không bấm nút và không đăng nhập. "
        f"Khác biệt thiết bị={device_difference}; khác biệt referrer={referrer_difference}."
    )
    return {
        "results": results,
        "f12_summary": summary,
        "curl_summary": "Kết quả curl thật (không phải trình duyệt):\n" + summary,
        "reproduction_notes": reproduction,
        "conditional_difference": reportable_cloaking,
        "device_difference": device_difference,
        "referrer_difference": referrer_difference,
        "matched_path_difference": matched_path_difference,
        "matched_paths": matched_paths,
        "fingerprint_profiles": fingerprint_profiles,
        "reportable_cloaking": reportable_cloaking,
        "successful_count": len(successful),
    }
