from __future__ import annotations

import re


def missing_report_profile(profile) -> list[str]:
    """Only the public identity required by Quick Report; no mail credentials."""
    missing = [field for field in ("brand_name", "contact_name")
        if not str(getattr(profile, field, "") or "").strip()]
    email = str(getattr(profile, "contact_email", "") or "").strip()
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email):
        missing.append("contact_email")
    return missing
