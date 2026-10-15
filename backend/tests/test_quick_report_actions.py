from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import unittest
import urllib.error
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4
from xml.etree import ElementTree as ET

from sqlalchemy import select

from quick_report_actions import build_nictop_workbook, submit_netcraft
from domaincheck.application.quick_report_actions import ActionConflict, QuickReportActions
from domaincheck.application.quick_reports import QuickReportNotFound, QuickReportService
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.infrastructure.db.models import Artifact, AuditEvent, ProviderSubmission, User, UserSettings
from domaincheck.infrastructure.db.session import Database

DB = os.environ.get("DOMAINCHECK_TEST_DATABASE_URL")


class ExportAndTransportTests(unittest.TestCase):
    def test_xlsx_preserves_full_urls_and_literal_cells_without_overclaiming(self):
        content = build_nictop_workbook([("fixture.top", "http://fixture.top/login?q=1&lang=vi")],
            brand_name="=HYPERLINK(\"https://example.test\")", detected_date="2026-10-15")
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for name in archive.namelist():
                ET.fromstring(archive.read(name))
            root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            values = [node.text for node in root.findall(".//s:t", ns)]
            self.assertIn("http://fixture.top/login?q=1&lang=vi", values)
            self.assertIn('=HYPERLINK("https://example.test")', values)
            self.assertEqual(root.findall(".//s:f", ns), [])
            self.assertNotIn("harvesting", " ".join(values))
            self.assertEqual(len(root.findall(".//s:row", ns)), 2)
        with self.assertRaises(ValueError):
            build_nictop_workbook([("fixture.com", "https://fixture.com")], brand_name="", detected_date="today")

    def test_transport_has_timeout_no_retry_and_no_raw_response(self):
        response = MagicMock()
        response.__enter__.return_value.status = 201
        opener = MagicMock()
        opener.open.return_value = response
        with patch("quick_report_actions.urllib.request.build_opener", return_value=opener):
            result = submit_netcraft({"email": "fixture@example.test", "urls": []})
        self.assertEqual(result, {"state": "SUBMITTED", "code": "accepted", "http_status": 201})
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 15})
        opener.open.assert_called_once()
        response.read.assert_not_called()

    def test_uncertain_response_and_network_failure_are_never_retried(self):
        for failure, expected in [
            (TimeoutError("secret"), "UNKNOWN"),
            (urllib.error.HTTPError("url", 503, "secret", {}, None), "UNKNOWN"),
            (urllib.error.HTTPError("url", 429, "secret", {}, None), "FAILED"),
            (urllib.error.HTTPError("url", 302, "redirect", {}, None), "UNKNOWN"),
        ]:
            opener = MagicMock()
            opener.open.side_effect = failure
            with patch("quick_report_actions.urllib.request.build_opener", return_value=opener):
                result = submit_netcraft({})
            self.assertEqual(result["state"], expected)
            self.assertNotIn("secret", repr(result))
            opener.open.assert_called_once()


@unittest.skipUnless(DB, "requires isolated PostgreSQL")
class QuickReportActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(DB)
        self.temp = tempfile.TemporaryDirectory()
        self.store = FilesystemArtifactStore(Path(self.temp.name))
        self.now = datetime(2026, 10, 15, 8, tzinfo=UTC)
        self.calls = []
        async with self.db.session_factory() as session, session.begin():
            owner = User(display_name="Action owner", status="active", timezone="Asia/Bangkok")
            other = User(display_name="Other owner", status="active", timezone="Asia/Bangkok")
            session.add_all([owner, other])
            await session.flush()
            self.owner, self.other = owner.id, other.id
            session.add(UserSettings(owner_user_id=owner.id, brand_name="Fixture Brand", contact_email="reporter@example.test"))
            operation = await QuickReportService(session, owner_user_id=owner.id, actor_user_id=owner.id,
                timezone="Asia/Bangkok", request_id="fixture", now=self.now).create([
                    "http://fixture.top/login?flow=1", "https://fixture.top/path?q=2", "https://other.test/path"])
            operation.job.state = "SUCCEEDED"
            self.operation_id = operation.job.id
            self.check_id = operation.checks[0].id
            for check in operation.checks:
                check.status = "succeeded"

    async def asyncTearDown(self):
        await self.db.dispose()
        self.temp.cleanup()

    async def adapter(self, payload):
        self.calls.append(payload)
        async with self.db.session_factory() as session:
            row = await session.scalar(select(ProviderSubmission).where(ProviderSubmission.owner_user_id == self.owner))
            self.assertEqual(row.state, "SENDING")
        return {"state": "SUBMITTED", "http_status": 201, "raw": "private"}

    def service(self, owner=None, adapter=None):
        return QuickReportActions(self.db.session_factory, self.store, owner=owner or self.owner,
            timezone="Asia/Bangkok", request_id="test-action", now=lambda: self.now,
            adapter=adapter or self.adapter)

    async def preview(self):
        formatter = SimpleNamespace(generate_community_report_text=lambda domain, cfg, url: f"Please review {url} for suspected impersonation of {cfg['brand_name']}.")
        with patch("domaincheck.application.quick_report_actions.importlib.import_module", return_value=formatter):
            return await self.service().preview(self.check_id)

    async def send(self, preview, **overrides):
        params = dict(fingerprint=preview["fingerprint"], revision=preview["revision"], confirmed=True)
        params.update(overrides)
        return await self.service().submit(UUID(preview["id"]), **params)

    async def test_preview_exact_payload_intent_before_send_and_durable_dedupe(self):
        preview = await self.preview()
        self.assertEqual(self.calls, [])
        sent = await self.send(preview)
        self.assertEqual(sent["state"], "SUBMITTED")
        self.assertEqual(self.calls, [preview["payload"]])
        self.assertEqual((await self.send(preview))["state"], "SUBMITTED")
        self.assertEqual((await self.preview())["state"], "SUBMITTED")
        self.assertEqual(len(self.calls), 1)
        async with self.db.session_factory() as session:
            events = list(await session.scalars(select(AuditEvent).where(AuditEvent.owner_user_id == self.owner)))
            metadata = json.dumps([event.metadata_allowlist for event in events])
            self.assertNotIn("reporter@example.test", metadata)
            self.assertNotIn("Please review", metadata)

    async def test_cross_owner_get_preview_submit_and_export_are_denied(self):
        preview = await self.preview()
        other = self.service(self.other)
        for action in (other.preview(self.check_id), other.get(UUID(preview["id"])), other.export_nictop(self.operation_id),
                       other.submit(UUID(preview["id"]), fingerprint=preview["fingerprint"], revision=1, confirmed=True)):
            with self.assertRaises(QuickReportNotFound):
                await action
        self.assertEqual(self.calls, [])

    async def test_stale_revision_expiry_and_profile_changes_block_send(self):
        first = await self.preview()
        latest = await self.preview()
        with self.assertRaises(ActionConflict):
            await self.send(first)
        with self.assertRaises(ActionConflict):
            await self.send(latest, confirmed=False)
        self.now += timedelta(minutes=31)
        with self.assertRaises(ActionConflict):
            await self.send(latest)
        self.now -= timedelta(minutes=31)
        async with self.db.session_factory() as session, session.begin():
            profile = await session.get(UserSettings, self.owner)
            profile.version += 1
        with self.assertRaises(ActionConflict):
            await self.send(latest)
        self.assertEqual(self.calls, [])

    async def test_mutated_artifact_is_blocked(self):
        preview = await self.preview()
        async with self.db.session_factory() as session:
            row = await session.get(ProviderSubmission, UUID(preview["id"]))
            artifact = await session.get(Artifact, row.payload_artifact_id)
            (Path(self.temp.name) / artifact.storage_key).write_bytes(b"tampered")
        with self.assertRaises(ActionConflict):
            await self.send(preview)
        self.assertEqual(self.calls, [])

    async def test_concurrent_submit_only_one_adapter_call(self):
        preview = await self.preview()
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(payload):
            self.calls.append(payload)
            entered.set()
            await release.wait()
            return {"state": "SUBMITTED"}
        service = self.service(adapter=slow)
        params = dict(fingerprint=preview["fingerprint"], revision=1, confirmed=True)
        first = asyncio.create_task(service.submit(UUID(preview["id"]), **params))
        await asyncio.wait_for(entered.wait(), timeout=5)
        try:
            second = await service.submit(UUID(preview["id"]), **params)
            self.assertEqual(second["state"], "SENDING")
        finally:
            release.set()
            await first
        self.assertEqual(len(self.calls), 1)

    async def test_unknown_after_intent_or_timeout_is_not_retried(self):
        preview = await self.preview()
        async def interrupted(_payload):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.service(adapter=interrupted).submit(UUID(preview["id"]),
                fingerprint=preview["fingerprint"], revision=1, confirmed=True)
        self.now += timedelta(seconds=61)
        self.assertEqual((await self.service().get(UUID(preview["id"])))["state"], "UNKNOWN")
        self.assertEqual((await self.send(preview, retry=True))["state"], "UNKNOWN")
        self.assertEqual(self.calls, [])

    async def test_failed_delivery_only_retries_when_explicit(self):
        preview = await self.preview()
        async def rejected(_payload):
            return {"state": "FAILED", "http_status": 429}
        await self.service(adapter=rejected).submit(UUID(preview["id"]), fingerprint=preview["fingerprint"], revision=1, confirmed=True)
        with self.assertRaises(ActionConflict):
            await self.send(preview)
        self.assertEqual((await self.send(preview, retry=True))["state"], "SUBMITTED")

    async def test_export_only_completed_top_full_urls_and_brand(self):
        content = await self.service().export_nictop(self.operation_id)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("http://fixture.top/login?flow=1", sheet)
        self.assertIn("https://fixture.top/path?q=2", sheet)
        self.assertIn("Fixture Brand", sheet)
        self.assertNotIn("other.test", sheet)
        self.assertEqual(sheet.count('<row '), 3)
