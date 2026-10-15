from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from domaincheck.api.dependencies import current_user, get_db_session
from domaincheck.application.profile_readiness import missing_report_profile
from domaincheck.infrastructure.auth.sessions import hash_token
from domaincheck.main import create_app
from domaincheck.settings import Settings


class ProfileReadinessTests(unittest.IsolatedAsyncioTestCase):
    def test_public_profile_validation(self):
        self.assertEqual(missing_report_profile(None), ['brand_name', 'contact_name', 'contact_email'])
        for email in ('', 'invalid', ' a@b ', 'bad name@example.test'):
            self.assertIn('contact_email', missing_report_profile(SimpleNamespace(
                brand_name='Brand', contact_name='Name', contact_email=email)))
        self.assertEqual(missing_report_profile(SimpleNamespace(
            brand_name=' Brand ', contact_name=' Name ', contact_email=' report@example.test ')), [])

    async def test_api_gate_blocks_actions_and_reports_only_current_owner_missing_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Settings(_env_file=None, artifact_root=Path(directory)))
            owner = uuid4()
            actor = SimpleNamespace(user_id=owner, status='active', csrf_hash=hash_token('fixture-csrf'),
                permissions=frozenset({'quick_report.view', 'quick_report.check', 'quick_report.open_form',
                    'quick_report.submit_netcraft', 'quick_report.export'}))
            profile = None

            class Session:
                async def get(self, model, key):
                    self_test.assertEqual(key, owner)
                    return profile

            self_test = self
            async def session():
                yield Session()

            app.dependency_overrides[current_user] = lambda: actor
            app.dependency_overrides[get_db_session] = session
            headers = {'Origin': 'http://127.0.0.1:5173', 'X-CSRF-Token': 'fixture-csrf'}
            identifier = str(uuid4())
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                initial = await client.get('/api/v1/quick-reports/readiness')
                self.assertFalse(initial.json()['ready'])
                actions = [
                    ('post', '/quick-reports/checks', {'targets': ['https://fixture.test']}),
                    ('post', f'/quick-reports/checks/{identifier}/netcraft/preview', {}),
                    ('post', f'/quick-reports/netcraft/{identifier}/submit', {
                        'fingerprint': 'a' * 64, 'revision': 1, 'confirmed': True}),
                    ('post', f'/quick-reports/checks/{identifier}/form-tasks', {
                        'provider': 'google_gsb', 'device_id': 'fixture-device-12345'}),
                    ('get', f'/quick-reports/checks/{identifier}/exports/nictop', None),
                ]
                for method, path, body in actions:
                    response = await client.request(method, '/api/v1' + path, headers=headers,
                        **({'json': body} if body is not None else {}))
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertIn('brand_name', response.json()['message'])
                profile = SimpleNamespace(brand_name='Brand', contact_name='Name', contact_email='not-an-email')
                self.assertEqual((await client.get('/api/v1/quick-reports/readiness')).json(),
                    {'ready': False, 'missing': ['contact_email']})
                profile.contact_email = 'report@example.test'
                self.assertEqual((await client.get('/api/v1/quick-reports/readiness')).json(),
                    {'ready': True, 'missing': []})
            await app.state.database.dispose()
            await app.state.redis.aclose()
