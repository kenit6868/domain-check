"""Pure helpers shared by the Domain Worker UI and background process."""

import re


def extract_domains_from_text(raw: str) -> list[str]:
    """Extract unique URLs from notes, preserving scheme, path and query."""
    candidates = re.findall(
        r"""(?ix)
        https?://[^\s<>"']+
        |
        (?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+
        [a-z]{2,63}(?:/[^\s<>"']*)?
        """,
        raw or "",
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
