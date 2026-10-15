from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True, slots=True, repr=False)
class EncryptedValue:
    ciphertext: bytes
    nonce: bytes
    key_version: int

    def __repr__(self) -> str:
        return f"EncryptedValue(key_version={self.key_version}, ciphertext=<redacted>)"


class SecretEnvelope:
    """Authenticated encryption with explicit key versions.

    Keys come from process configuration or a secret manager and are never
    persisted next to the ciphertext. Associated data binds a secret to its
    owner and field so copied ciphertext cannot be silently reused elsewhere.
    """

    def __init__(self, keys: Mapping[int, bytes], active_version: int):
        if active_version not in keys:
            raise ValueError("Active secret key version is unavailable")
        invalid = [version for version, key in keys.items() if len(key) != 32]
        if invalid:
            raise ValueError("AES-256 keys must be exactly 32 bytes")
        self._keys = dict(keys)
        self.active_version = active_version

    @classmethod
    def from_base64_keys(cls, keys: Mapping[int, str], active_version: int) -> "SecretEnvelope":
        decoded: dict[int, bytes] = {}
        for version, encoded in keys.items():
            try:
                decoded[version] = base64.urlsafe_b64decode(encoded)
            except Exception as exc:
                raise ValueError(f"Invalid base64 key for version {version}") from exc
        return cls(decoded, active_version)

    def encrypt_json(self, value: Mapping[str, object], *, associated_data: bytes) -> EncryptedValue:
        nonce = os.urandom(12)
        plaintext = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ciphertext = AESGCM(self._keys[self.active_version]).encrypt(
            nonce, plaintext, associated_data
        )
        return EncryptedValue(ciphertext, nonce, self.active_version)

    def decrypt_json(self, value: EncryptedValue, *, associated_data: bytes) -> dict[str, object]:
        key = self._keys.get(value.key_version)
        if key is None:
            raise ValueError("Secret key version is unavailable")
        plaintext = AESGCM(key).decrypt(value.nonce, value.ciphertext, associated_data)
        decoded = json.loads(plaintext)
        if not isinstance(decoded, dict):
            raise ValueError("Encrypted secret payload is not an object")
        return decoded

    def rotate(self, value: EncryptedValue, *, associated_data: bytes) -> EncryptedValue:
        if value.key_version == self.active_version:
            return value
        return self.encrypt_json(self.decrypt_json(value, associated_data=associated_data), associated_data=associated_data)

