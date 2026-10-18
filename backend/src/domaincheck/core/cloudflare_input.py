"""Shared pure Cloudflare URL/annotation parser; no runtime or secret access."""
import re
from urllib.parse import urlparse

def normalize_target(value: str) -> str:
    value = str(value or "").strip()
    if re.search(r"\s", value):
        raise ValueError("URL/domain không được chứa khoảng trắng hoặc ghi chú")
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


_HTTP_TARGET_RE = re.compile(r"https?://[^\s<>\[\]{}(),;]+", re.I)
_BARE_TARGET_RE = re.compile(
    r"(?<![@\w])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}(?::\d{1,5})?(?:/[^\s<>\[\]{}(),;]*)?",
    re.I,
)


def parse_target_input(raw: str) -> tuple[list[str], list[str], list[str]]:
    """Extract normalized URL/domain tokens while ignoring labels and arrows."""
    valid: list[str] = []
    invalid: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for source_line in str(raw or "").splitlines():
        line = source_line.strip()
        if not line:
            continue
        # Arrows describe observed navigation and are separators, never URL text.
        searchable = re.sub(r"\s*(?:->|→)\s*", " ", line)
        candidates = _HTTP_TARGET_RE.findall(searchable)
        if not candidates:
            candidates = _BARE_TARGET_RE.findall(searchable)
        if not candidates:
            invalid.append(line)
            continue
        for candidate in candidates:
            try:
                normalized = normalize_target(candidate.rstrip(".!?'\""))
            except (TypeError, ValueError):
                invalid.append(candidate)
                continue
            if normalized in seen:
                duplicates.append(normalized)
                continue
            seen.add(normalized)
            valid.append(normalized)
    return valid, invalid, duplicates


