from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence
from uuid import UUID


class ArtifactValidationError(ValueError):
    """An upload batch does not satisfy the private artifact policy."""


class ArtifactIntegrityError(RuntimeError):
    """Persisted bytes no longer match the durable artifact metadata."""


@dataclass(frozen=True, slots=True)
class ImageUpload:
    original_name: str
    content_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    artifact_id: UUID
    storage_key: str
    mime_type: str
    byte_size: int
    sha256: str
    width: int
    height: int
    original_name: str


@dataclass(frozen=True, slots=True)
class StoredPrivateBlob:
    artifact_id: UUID
    storage_key: str
    mime_type: str
    byte_size: int
    sha256: str


class ArtifactStore(Protocol):
    @property
    def root(self) -> Path: ...

    def store_evidence_images(
        self,
        *,
        owner_user_id: UUID,
        evidence_set_id: UUID,
        uploads: Sequence[ImageUpload],
        minimum_count: int = 1,
        maximum_count: int = 4,
    ) -> tuple[StoredArtifact, ...]: ...

    def store_preview_payload(
        self,
        *,
        owner_user_id: UUID,
        preview_id: UUID,
        data: bytes,
    ) -> StoredPrivateBlob: ...

    def store_evidence_manifest(
        self, *, owner_user_id: UUID, evidence_set_id: UUID, data: bytes,
    ) -> StoredPrivateBlob: ...

    def read_verified(
        self,
        *,
        storage_key: str,
        expected_size: int,
        expected_sha256: str,
    ) -> bytes: ...

    def delete(self, *, storage_key: str) -> None: ...
