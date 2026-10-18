from __future__ import annotations

import asyncio
import io
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException, Response
from PIL import Image

from domaincheck.api.domain_checks import request_capture
from domaincheck.application.domain_checks import DomainCheckService
from domaincheck.application.evidence import EvidenceNotFound, EvidenceService
from domaincheck.core.evidence import CaptureFailed, LegacyBrowserCaptureAdapter, capture_failure_code
from domaincheck.infrastructure.artifacts import FilesystemArtifactStore
from domaincheck.infrastructure.db.models import User
from domaincheck.infrastructure.db.session import Database
from domaincheck.main import create_app
from domaincheck.ports.artifacts import ImageUpload
from domaincheck.settings import Settings


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3, 2), (10, 20, 30)).save(output, format="PNG")
    return output.getvalue()


class EvidenceWorkflowTests(unittest.TestCase):
    def test_capture_failure_codes_are_specific_and_never_expose_exception_text(self):
        cases = {"No module named 'playwright'": 'browser_runtime_missing',
            "Chưa cài Playwright": 'browser_runtime_missing',
            "Executable doesn't exist at C:/private/browser.exe": 'browser_executable_missing',
            "Timeout 45000ms exceeded for https://user:secret@example.com": 'browser_timeout',
            "net::ERR_NAME_NOT_RESOLVED": 'browser_navigation_failed',
            "password=secret local path private": 'capture_failed'}
        for detail, expected in cases.items():
            self.assertEqual(capture_failure_code(RuntimeError(detail)), expected)
            self.assertEqual(capture_failure_code(CaptureFailed(expected)), expected)

    def test_missing_browser_runtime_is_reported_from_legacy_result(self):
        fake = SimpleNamespace(capture_normal_report_evidence=lambda *_a, **_k:
            {'success': False, 'error': 'Chưa cài Playwright'})
        with tempfile.TemporaryDirectory() as directory, \
             patch('domaincheck.core.evidence.importlib.import_module', return_value=fake):
            with self.assertRaisesRegex(CaptureFailed, 'browser_runtime_missing'):
                LegacyBrowserCaptureAdapter().capture('https://example.com/', staging_root=Path(directory))

    def test_evidence_routes_are_permission_gated_and_capture_is_disabled_by_default(self):
        paths = create_app(Settings(_env_file=None)).openapi()["paths"]
        self.assertEqual(set(paths["/api/v1/domain-checks/checks/{check_id}/evidence/upload"]), {"post"})
        self.assertEqual(set(paths["/api/v1/domain-checks/checks/{check_id}/evidence/capture"]), {"post"})
        self.assertEqual(set(paths["/api/v1/domain-checks/checks/{check_id}/evidence"]), {"get"})
        self.assertEqual(set(paths["/api/v1/domain-checks/captures/{job_id}"]), {"get"})
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            settings=Settings(_env_file=None, browser_capture_enabled=False))))
        with self.assertRaises(HTTPException) as denied:
            asyncio.run(request_capture(uuid4(), request, Response(), SimpleNamespace(user_id=uuid4())))
        self.assertEqual(denied.exception.status_code, 503)

    def test_capture_adapter_reads_only_staged_valid_images_and_redacts_metadata(self):
        png = png_bytes()
        def capture(target, directory, **_kwargs):
            path = Path(directory) / "source.png"
            path.write_bytes(png)
            manifest = Path(directory) / "manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            return {"success": True, "screenshot_path": str(path),
                    "manifest_path": str(manifest), "requested_url": target,
                    "landing_url": "https://user:secret@example.com/login?token=private",
                    "capture_strategy": "passive_fallback"}
        fake = SimpleNamespace(capture_normal_report_evidence=capture,
            validate_evidence_artifacts=lambda _raw: {"valid": True,
                "manifest": {"evidence_type": "dom_observed", "dom_element": "secret"}},
            redact_url=lambda value: "redacted" if "secret" in value else value)
        with tempfile.TemporaryDirectory() as directory, \
             patch("domaincheck.core.evidence.importlib.import_module", return_value=fake):
            uploads, manifest = LegacyBrowserCaptureAdapter().capture(
                "https://example.com/login", staging_root=Path(directory))
            self.assertEqual(len(uploads), 1)
            self.assertEqual(uploads[0].data, png)
            self.assertEqual(manifest["landing_url"], "redacted")
            self.assertNotIn("dom_element", manifest)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_capture_uses_shared_legacy_defaults_without_extra_network_callbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = Mock(return_value={"success": False, "terminal": False})
            fake = SimpleNamespace(capture_normal_report_evidence=capture)
            with patch("domaincheck.core.evidence.importlib.import_module", return_value=fake):
                with self.assertRaises(CaptureFailed):
                    LegacyBrowserCaptureAdapter().capture("https://example.com/", staging_root=Path(directory))
            self.assertEqual(capture.call_args.kwargs, {"headless": True})

    def test_visible_capture_passes_headless_false_without_changing_shared_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = Mock(return_value={'success': False})
            fake = SimpleNamespace(capture_normal_report_evidence=capture)
            with patch('domaincheck.core.evidence.importlib.import_module', return_value=fake):
                with self.assertRaises(CaptureFailed):
                    LegacyBrowserCaptureAdapter(headless=False).capture('https://example.com/', staging_root=Path(directory))
            self.assertEqual(capture.call_args.kwargs, {'headless': False})

    def test_projection_keeps_dom_narrative_facts_without_html_or_credentials(self):
        from domaincheck.core.evidence import capture_manifest
        source = 'https://source.example/vi-vn/'
        destination = 'https://destination.example/wap/'
        manifest = capture_manifest({'requested_url': source}, {
            'requested_url': source, 'landing_url': source, 'final_url': destination,
            'evidence_type': 'dom_destination_opened', 'destination_opened': True,
            'control': {'found': True, 'label': 'ĐĂNG KÝ', 'raw_href': destination,
                'resolved_destination': destination, 'dom_element': '<a password="private">secret</a>'},
            'navigation': {'mode': 'new_tab_direct', 'source_url': source, 'destination_url': destination,
                'referrer_sent': True, 'redirect_chain': [{'status': 302, 'url': destination,
                    'location': 'https://user:secret@destination.example/login?token=private'}]},
            'page_signals': {'passwordInputs': 1, 'field_values': ['secret']},
            'page': {'title': 'Source'}, 'destination_page': {'title': 'Destination'}})
        self.assertEqual(manifest['control']['label'], 'ĐĂNG KÝ')
        self.assertEqual(manifest['control']['resolved_destination'], destination)
        self.assertEqual(manifest['navigation']['source_url'], source)
        self.assertTrue(manifest['navigation']['referrer_sent'])
        self.assertEqual(manifest['page_signals']['passwordInputs'], 1)
        self.assertNotIn('dom_element', str(manifest))
        self.assertNotIn('secret', str(manifest)); self.assertNotIn('private', str(manifest))

    def test_only_explicit_browser_proxy_is_forwarded_to_shared_capture(self):
        capture = Mock(return_value={"success": False})
        proxy = {"server": "http://proxy:3128"}
        with tempfile.TemporaryDirectory() as directory, patch(
                "domaincheck.core.evidence.importlib.import_module",
                return_value=SimpleNamespace(capture_normal_report_evidence=capture)):
            with self.assertRaises(CaptureFailed):
                LegacyBrowserCaptureAdapter(browser_proxy=proxy).capture(
                    "https://example.com/", staging_root=Path(directory))
        self.assertEqual(capture.call_args.kwargs, {"headless": True, "browser_proxy": proxy})

    def test_capture_rejects_terminal_and_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            terminal = SimpleNamespace(capture_normal_report_evidence=lambda *_a, **_k:
                {"success": False, "terminal": True})
            with patch("domaincheck.core.evidence.importlib.import_module", return_value=terminal):
                with self.assertRaises(CaptureFailed) as error:
                    LegacyBrowserCaptureAdapter().capture("https://example.com/", staging_root=Path(directory))
            self.assertTrue(error.exception.terminal)
            outside = Path(directory) / "outside.png"
            outside.write_bytes(png_bytes())
            def escaped(_target, staging, **_kwargs):
                manifest = Path(staging) / "manifest.json"
                manifest.write_text("{}", encoding="utf-8")
                return {"success": True, "screenshot_path": str(outside), "manifest_path": str(manifest)}
            fake = SimpleNamespace(capture_normal_report_evidence=escaped,
                validate_evidence_artifacts=lambda _raw: {"valid": True, "manifest": {}})
            with patch("domaincheck.core.evidence.importlib.import_module", return_value=fake):
                with self.assertRaises(CaptureFailed):
                    LegacyBrowserCaptureAdapter().capture("https://example.com/", staging_root=Path(directory))



@unittest.skipUnless(os.environ.get("DOMAINCHECK_TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class EvidenceDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_and_capture_job_are_owned_and_artifacts_are_verified(self):
        database = Database(os.environ["DOMAINCHECK_TEST_DATABASE_URL"])
        try:
            with tempfile.TemporaryDirectory() as directory:
                store = FilesystemArtifactStore(Path(directory))
                async with database.session_factory() as session, session.begin():
                    first = User(display_name="Evidence first", status="active", timezone="Asia/Bangkok")
                    second = User(display_name="Evidence second", status="active", timezone="Asia/Bangkok")
                    session.add_all([first, second]); await session.flush()
                    first_id, second_id = first.id, second.id
                    operation = await DomainCheckService(session, owner_user_id=first_id,
                        actor_user_id=first_id, timezone="Asia/Bangkok", request_id="evidence-test",
                        now=datetime.now(UTC)).create("https://example.com/login", submit_vt=False)
                    operation.checks[0].status = "succeeded"
                    check_id = operation.checks[0].id
                first_service = EvidenceService(database.session_factory, store,
                    owner_user_id=first_id, actor_user_id=first_id, request_id="evidence-test")
                saved = await first_service.save_images(check_id,
                    [ImageUpload("manual.png", "image/png", png_bytes())],
                    mode="manual_upload", manifest={"evidence_type": "manual_upload"})
                self.assertEqual(saved.manifest["screenshots"][0]["artifact_id"], str(saved.artifact_ids[0]))
                self.assertEqual(len(await first_service.list_for_check(check_id)), 1)
                job = await first_service.create_capture_job(check_id)
                self.assertEqual(job.job_type, "browser_evidence")
                second_service = EvidenceService(database.session_factory, store,
                    owner_user_id=second_id, actor_user_id=second_id, request_id="evidence-test")
                with self.assertRaises(EvidenceNotFound):
                    await second_service.list_for_check(check_id)
        finally:
            await database.dispose()


if __name__ == "__main__":
    unittest.main()
