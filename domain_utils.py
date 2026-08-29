"""Pure helpers shared by the Domain Worker UI and background process."""

import re
from urllib.parse import urlsplit


def extract_domains_from_text(raw: str) -> list[str]:
    """Extract unique URLs from notes, preserving scheme, path and query."""
    # Người dùng đôi khi dán hai URL liền nhau, ví dụ
    # ``example/pathhttps://second.example``. Tạo ranh giới trước scheme sau.
    raw = re.sub(r"(?i)(?<!^)(https?://)", r"\n\1", raw or "")
    candidates = re.findall(
        r"""(?ix)
        https?://[^\s<>"']+
        |
        (?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+
        [a-z]{2,63}(?:/[^\s<>"']*)?
        """,
        raw,
    )
    targets = []
    seen = set()
    for candidate in candidates:
        target = candidate.rstrip(".,;:)]}")
        key = target.lower()
        if target and key not in seen:
            seen.add(key)
            targets.append(target)
    return targets


def domain_cache_key(target: str) -> str:
    """Return a domain + subpath key for Quick Report duplicate filtering."""
    value = (target or "").strip()
    parsed = urlsplit(value if "://" in value else f"//{value}")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        return ""
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"https://{hostname}{path}{query}"


def filter_unseen_domains(targets: list[str], seen: set[str]) -> tuple[list[str], list[str]]:
    """Split targets into new/previously-seen domains and update ``seen``.

    Only the same hostname + subpath (+ query) is considered a duplicate.
    Different paths on the same domain remain separate report targets.
    """
    fresh, duplicate = [], []
    current_batch = set()
    for target in targets:
        key = domain_cache_key(target)
        if not key:
            continue
        if key in seen or key in current_batch:
            duplicate.append(target)
            continue
        current_batch.add(key)
        fresh.append(target)
    seen.update(current_batch)
    return fresh, duplicate


def extract_branded_domains(raw: str) -> list[dict[str, str]]:
    """Extract links and associate them with the nearest preceding brand heading.

    A non-empty line without a domain is treated as a heading. This matches the
    worker paste format, for example ``QS88`` followed by one or more URLs.
    """
    items = []
    current_brand = ""
    seen = set()
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        targets = extract_domains_from_text(line)
        if not targets:
            # Phần sau " - " là ghi chú như "k có đối thủ", không thuộc tên brand.
            current_brand = re.split(r"\s+-\s+", line, maxsplit=1)[0].strip()
            continue
        for target in targets:
            key = target.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append({"brand": current_brand, "target": target})
    return items
