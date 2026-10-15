from __future__ import annotations

import hashlib
import secrets


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    if not token:
        raise ValueError("Session token must not be empty")
    return hashlib.sha256(token.encode("utf-8")).digest()

