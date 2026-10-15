from __future__ import annotations

import hashlib
import io
import os
import shutil
import warnings
from pathlib import Path, PurePosixPath
from typing import Sequence
from uuid import UUID, uuid4

from PIL import Image

from domaincheck.ports.artifacts import (
    ArtifactIntegrityError,
    ArtifactValidationError,
    ImageUpload,
    StoredArtifact,
    StoredPrivateBlob,
)


_FORMAT_POLICY = {
    "PNG": ("image/png", ".png", b"\x89PNG\r\n\x1a\n"),
    "JPEG": ("image/jpeg", ".jpg", b"\xff\xd8\xff"),
}


class FilesystemArtifactStore:
    """Private immutable artifact storage rooted outside static serving.

    Paths are generated only from server UUIDs. A batch is fully decoded and
    validated before any final object is created. Staged and already-moved files
    are removed if a filesystem commit fails.
    """

    def __init__(
        self,
        root: Path,
        *,
        max_file_bytes: int = 10 * 1024 * 1024,
        max_dimension: int = 16_384,
        max_pixels: int = 40_000_000,
    ) -> None:
        if max_file_bytes < 1 or max_dimension < 1 or max_pixels < 1:
            raise ValueError("Artifact limits must be positive")
        self._root = root.expanduser().resolve()
        self._max_file_bytes = max_file_bytes
        self._max_dimension = max_dimension
        self._max_pixels = max_pixels
        self._root.mkdir(parents=True, exist_ok=True)
        self._staging_root = self._root / ".staging"
        self._staging_root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def store_evidence_images(
        self,
        *,
        owner_user_id: UUID,
        evidence_set_id: UUID,
        uploads: Sequence[ImageUpload],
        minimum_count: int = 1,
        maximum_count: int = 4,
    ) -> tuple[StoredArtifact, ...]:
        if minimum_count < 1 or maximum_count < minimum_count:
            raise ValueError("Invalid batch count policy")
        if not minimum_count <= len(uploads) <= maximum_count:
            raise ArtifactValidationError(
                f"Image batch must contain between {minimum_count} and {maximum_count} files"
            )

        validated = [self._validate_image(upload) for upload in uploads]
        batch_id = uuid4()
        staging_dir = self._staging_root / str(batch_id)
        staging_dir.mkdir(parents=False, exist_ok=False)
        records: list[StoredArtifact] = []
        staged_paths: list[Path] = []
        final_paths: list[Path] = []
        try:
            for upload, (mime_type, extension, width, height, digest) in zip(uploads, validated):
                artifact_id = uuid4()
                storage_key = (
                    f"users/{owner_user_id}/evidence/{evidence_set_id}/"
                    f"{artifact_id}{extension}"
                )
                staged_path = staging_dir / f"{artifact_id}{extension}"
                with staged_path.open("xb") as handle:
                    handle.write(upload.data)
                    handle.flush()
                    os.fsync(handle.fileno())
                staged_paths.append(staged_path)
                records.append(
                    StoredArtifact(
                        artifact_id=artifact_id,
                        storage_key=storage_key,
                        mime_type=mime_type,
                        byte_size=len(upload.data),
                        sha256=digest,
                        width=width,
                        height=height,
                        original_name=upload.original_name,
                    )
                )

            for staged_path, record in zip(staged_paths, records):
                final_path = self._resolve_key(record.storage_key, require_exists=False)
                final_path.parent.mkdir(parents=True, exist_ok=True)
                if final_path.exists():
                    raise FileExistsError("Generated artifact already exists")
                os.replace(staged_path, final_path)
                final_paths.append(final_path)
            return tuple(records)
        except Exception:
            for path in final_paths:
                path.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def read_verified(
        self,
        *,
        storage_key: str,
        expected_size: int,
        expected_sha256: str,
    ) -> bytes:
        path = self._resolve_key(storage_key, require_exists=True)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != expected_size or digest != expected_sha256:
            raise ArtifactIntegrityError("Artifact bytes do not match durable metadata")
        return data

    def store_preview_payload(
        self,
        *,
        owner_user_id: UUID,
        preview_id: UUID,
        data: bytes,
    ) -> StoredPrivateBlob:
        if not data or len(data) > 5 * 1024 * 1024:
            raise ArtifactValidationError("Preview payload is empty or exceeds 5 MiB")
        artifact_id = uuid4()
        storage_key = f"users/{owner_user_id}/previews/{preview_id}/{artifact_id}.json"
        staging_dir = self._staging_root / str(uuid4())
        staging_dir.mkdir(parents=False, exist_ok=False)
        staged_path = staging_dir / f"{artifact_id}.json"
        final_path: Path | None = None
        try:
            with staged_path.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            final_path = self._resolve_key(storage_key, require_exists=False)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if final_path.exists():
                raise FileExistsError("Generated artifact already exists")
            os.replace(staged_path, final_path)
            return StoredPrivateBlob(
                artifact_id=artifact_id,
                storage_key=storage_key,
                mime_type="application/vnd.domaincheck.preview+json",
                byte_size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        except Exception:
            if final_path is not None:
                final_path.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def delete(self, *, storage_key: str) -> None:
        path = self._resolve_key(storage_key, require_exists=False)
        path.unlink(missing_ok=True)

    def _validate_image(self, upload: ImageUpload) -> tuple[str, str, int, int, str]:
        self._validate_original_name(upload.original_name)
        if upload.content_type not in {"image/png", "image/jpeg"}:
            raise ArtifactValidationError("Only PNG and JPEG images are accepted")
        if not upload.data or len(upload.data) > self._max_file_bytes:
            raise ArtifactValidationError("Image is empty or exceeds the configured byte limit")

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(upload.data)) as image:
                    image_format = image.format
                    width, height = image.size
                    image.verify()
                with Image.open(io.BytesIO(upload.data)) as decoded:
                    decoded.load()
        except (OSError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
            raise ArtifactValidationError("Image bytes are invalid or unsafe") from exc

        policy = _FORMAT_POLICY.get(image_format or "")
        if policy is None:
            raise ArtifactValidationError("Only PNG and JPEG image bytes are accepted")
        mime_type, extension, signature = policy
        if upload.content_type != mime_type or not upload.data.startswith(signature):
            raise ArtifactValidationError("Declared MIME type does not match image bytes")
        if (
            width < 1
            or height < 1
            or width > self._max_dimension
            or height > self._max_dimension
            or width * height > self._max_pixels
        ):
            raise ArtifactValidationError("Image dimensions exceed the configured limit")
        return mime_type, extension, width, height, hashlib.sha256(upload.data).hexdigest()

    @staticmethod
    def _validate_original_name(name: str) -> None:
        if not name or len(name) > 500 or "\x00" in name or "/" in name or "\\" in name:
            raise ArtifactValidationError("Original filename is invalid")

    def _resolve_key(self, storage_key: str, *, require_exists: bool) -> Path:
        pure = PurePosixPath(storage_key)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            raise ArtifactIntegrityError("Artifact storage key is invalid")
        candidate = self._root.joinpath(*pure.parts)
        resolved = candidate.resolve(strict=require_exists)
        if not resolved.is_relative_to(self._root) or resolved == self._root:
            raise ArtifactIntegrityError("Artifact storage key escapes the private root")
        return resolved
