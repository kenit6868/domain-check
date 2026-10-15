from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

from domaincheck.domain.artifacts import can_read_artifact
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.ports.artifacts import ArtifactIntegrityError, ArtifactValidationError, ImageUpload


def image_bytes(image_format: str, size: tuple[int, int] = (3, 2)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color=(25, 50, 75)).save(output, format=image_format)
    return output.getvalue()


class FilesystemArtifactStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = FilesystemArtifactStore(self.root)
        self.owner_id = uuid4()
        self.evidence_id = uuid4()

    def tearDown(self):
        self.temporary.cleanup()

    def upload(self, name: str, mime: str, data: bytes) -> ImageUpload:
        return ImageUpload(original_name=name, content_type=mime, data=data)

    def test_valid_png_and_jpeg_are_stored_under_server_generated_owner_path(self):
        png = image_bytes("PNG")
        jpeg = image_bytes("JPEG")
        records = self.store.store_evidence_images(
            owner_user_id=self.owner_id,
            evidence_set_id=self.evidence_id,
            uploads=[self.upload("first.png", "image/png", png), self.upload("second.jpg", "image/jpeg", jpeg)],
            minimum_count=2,
        )
        self.assertEqual(len(records), 2)
        self.assertEqual((records[0].width, records[0].height), (3, 2))
        self.assertEqual(records[0].sha256, hashlib.sha256(png).hexdigest())
        self.assertTrue(records[0].storage_key.startswith(f"users/{self.owner_id}/evidence/{self.evidence_id}/"))
        self.assertNotIn("first.png", records[0].storage_key)
        self.assertEqual(
            self.store.read_verified(
                storage_key=records[1].storage_key,
                expected_size=records[1].byte_size,
                expected_sha256=records[1].sha256,
            ),
            jpeg,
        )

    def test_invalid_member_rejects_entire_batch_before_final_write(self):
        with self.assertRaises(ArtifactValidationError):
            self.store.store_evidence_images(
                owner_user_id=self.owner_id,
                evidence_set_id=self.evidence_id,
                uploads=[
                    self.upload("valid.png", "image/png", image_bytes("PNG")),
                    self.upload("fake.png", "image/png", b"not-an-image"),
                ],
            )
        self.assertEqual(list((self.root / "users").rglob("*")) if (self.root / "users").exists() else [], [])

    def test_mime_filename_count_size_and_dimensions_fail_closed(self):
        png = image_bytes("PNG")
        with self.assertRaises(ArtifactValidationError):
            self.store.store_evidence_images(
                owner_user_id=self.owner_id, evidence_set_id=self.evidence_id,
                uploads=[self.upload("wrong.jpg", "image/jpeg", png)],
            )
        with self.assertRaises(ArtifactValidationError):
            self.store.store_evidence_images(
                owner_user_id=self.owner_id, evidence_set_id=self.evidence_id,
                uploads=[self.upload("../escape.png", "image/png", png)],
            )
        with self.assertRaises(ArtifactValidationError):
            self.store.store_evidence_images(
                owner_user_id=self.owner_id, evidence_set_id=self.evidence_id,
                uploads=[],
            )
        small_store = FilesystemArtifactStore(self.root / "small", max_file_bytes=8)
        with self.assertRaises(ArtifactValidationError):
            small_store.store_evidence_images(
                owner_user_id=self.owner_id, evidence_set_id=self.evidence_id,
                uploads=[self.upload("large.png", "image/png", png)],
            )
        dimension_store = FilesystemArtifactStore(self.root / "dimensions", max_dimension=2)
        with self.assertRaises(ArtifactValidationError):
            dimension_store.store_evidence_images(
                owner_user_id=self.owner_id, evidence_set_id=self.evidence_id,
                uploads=[self.upload("wide.png", "image/png", png)],
            )

    def test_integrity_check_detects_mutation_and_path_escape(self):
        record = self.store.store_evidence_images(
            owner_user_id=self.owner_id, evidence_set_id=self.evidence_id,
            uploads=[self.upload("evidence.png", "image/png", image_bytes("PNG"))],
        )[0]
        stored_path = self.root.joinpath(*record.storage_key.split("/"))
        stored_path.write_bytes(b"changed")
        with self.assertRaises(ArtifactIntegrityError):
            self.store.read_verified(
                storage_key=record.storage_key,
                expected_size=record.byte_size,
                expected_sha256=record.sha256,
            )
        with self.assertRaises(ArtifactIntegrityError):
            self.store.read_verified(storage_key="../outside.png", expected_size=1, expected_sha256="0" * 64)

    def test_commit_failure_removes_files_already_moved(self):
        real_replace = __import__("os").replace
        calls = 0

        def fail_second(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("fixture commit failure")
            return real_replace(source, destination)

        with patch("domaincheck.infrastructure.artifacts.filesystem.os.replace", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.store.store_evidence_images(
                    owner_user_id=self.owner_id,
                    evidence_set_id=self.evidence_id,
                    uploads=[
                        self.upload("one.png", "image/png", image_bytes("PNG")),
                        self.upload("two.jpg", "image/jpeg", image_bytes("JPEG")),
                    ],
                )
        files = [path for path in self.root.rglob("*") if path.is_file()]
        self.assertEqual(files, [])

    def test_preview_payload_is_private_immutable_and_size_limited(self):
        preview_id = uuid4()
        payload = b'{"subject":"Exact preview"}'
        record = self.store.store_preview_payload(
            owner_user_id=self.owner_id, preview_id=preview_id, data=payload,
        )
        self.assertTrue(record.storage_key.startswith(
            f"users/{self.owner_id}/previews/{preview_id}/"
        ))
        self.assertNotIn("Exact preview", record.storage_key)
        self.assertEqual(record.sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(self.store.read_verified(
            storage_key=record.storage_key,
            expected_size=record.byte_size,
            expected_sha256=record.sha256,
        ), payload)
        with self.assertRaises(ArtifactValidationError):
            self.store.store_preview_payload(
                owner_user_id=self.owner_id, preview_id=uuid4(), data=b"",
            )

    def test_artifact_permission_is_bound_to_kind_feature(self):
        self.assertTrue(can_read_artifact("check_domain:evidence", frozenset({"check_domain.view"})))
        self.assertFalse(can_read_artifact("check_domain:evidence", frozenset({"quick_report.view"})))
        self.assertFalse(can_read_artifact("unknown:evidence", frozenset({"check_domain.view"})))
        self.assertFalse(can_read_artifact("check_domain", frozenset({"check_domain.view"})))


if __name__ == "__main__":
    unittest.main()
