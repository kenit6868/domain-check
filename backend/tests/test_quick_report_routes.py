from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from domaincheck.engine import phishing_toolkit as toolkit
from domaincheck.core.quick_report_routes import report_forms
from domaincheck.core.form_tasks import available_providers
from domaincheck.application.quick_reports import QuickReportService
from domaincheck.infrastructure.db.models import User, UserSettings
from domaincheck.infrastructure.db.session import Database
from domaincheck.api.dependencies import current_user, get_db_session
from domaincheck.main import create_app
from domaincheck.settings import Settings


class LegacyRoutingParityTests(unittest.TestCase):
    def test_registrar_email_table_is_used_without_network(self):
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No network')):
            for registrar in toolkit.REGISTRAR_ABUSE_EMAILS:
                expected = toolkit.lookup_registrar_abuse_email(registrar)
                recipients = toolkit.resolve_quick_report_recipients('fixture.test', who={'registrar': registrar})
                uses_form = any(key in registrar.casefold() for key in toolkit.WEB_FORM_REGISTRARS)
                if expected and not uses_form and not toolkit.is_blocked_report_recipient(expected):
                    self.assertIn(expected, [row['email'] for row in recipients])

    def test_every_legacy_registrar_form_and_mail_exclusion_is_preserved(self):
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No network in routing')):
            for registrar, url in toolkit.WEB_FORM_REGISTRARS.items():
                with self.subTest(registrar=registrar):
                    forms = report_forms('fixture.test', {'registrar': registrar})
                    self.assertEqual(forms[0]['form_url'], url)
                    recipients = toolkit.resolve_quick_report_recipients('fixture.test', who={
                        'registrar': registrar, 'emails': ['fixture@example.test']})
                    self.assertFalse(any(row['channel'] == 'registrar' for row in recipients))

    def test_every_legacy_registry_form_and_email_is_preserved(self):
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No network in routing')):
            for suffix, entry in toolkit.CCTLD_REGISTRY_CONTACTS.items():
                with self.subTest(suffix=suffix):
                    domain = f'fixture.{suffix}'
                    forms = report_forms(domain, {})
                    expected = entry.get('report_webform')
                    self.assertEqual([row['form_url'] for row in forms], [expected] if expected else [])
                    recipients = toolkit.resolve_quick_report_recipients(domain, registry_contact=entry)
                    expected_mail = entry.get('abuse_email')
                    if expected_mail and not toolkit.is_blocked_report_recipient(expected_mail):
                        self.assertIn(expected_mail, [row['email'] for row in recipients])

    def test_xyz_family_uses_adapter_but_centralnic_com_co_does_not_use_co_adapter(self):
        for suffix, entry in toolkit.CCTLD_REGISTRY_CONTACTS.items():
            if entry.get('report_webform') == 'https://gen.xyz/account/submitticket.php?step=2&deptid=6':
                self.assertIn('xyz_registry_abuse', available_providers(f'fixture.{suffix}', {}))
        self.assertNotIn('registry_co_phishing', available_providers('fixture.com.co', {}))
        self.assertEqual(report_forms('fixture.com.co', {})[0]['label'], 'CentralNic')


@unittest.skipUnless(os.environ.get('DOMAINCHECK_TEST_DATABASE_URL'), 'requires isolated PostgreSQL')
class RoutingDraftApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_drafts_keep_full_url_and_cross_owner_is_hidden(self):
        database = Database(os.environ['DOMAINCHECK_TEST_DATABASE_URL'])
        app = create_app(Settings(_env_file=None))
        try:
            async with database.session_factory() as session, session.begin():
                first = User(display_name='First', status='active', timezone='Asia/Bangkok')
                second = User(display_name='Second', status='active', timezone='Asia/Bangkok')
                session.add_all([first, second]); await session.flush()
                for owner in (first, second):
                    session.add(UserSettings(owner_user_id=owner.id, brand_name='Fixture Brand',
                        contact_name='Fixture Name', contact_email='contact@example.test'))
                operation = await QuickReportService(session, owner_user_id=first.id, actor_user_id=first.id,
                    timezone=first.timezone, request_id='fixture', now=datetime.now(UTC)).create(['https://fixture.monster/login?q=1'])
                check = operation.checks[0]
                operation.job.state = 'completed'
                check.status = 'succeeded'; check.result = {'registrar': 'Dynadot LLC', 'cloudflare': True}
                await session.flush()
                actor = SimpleNamespace(user_id=first.id, status='active', permissions={'quick_report.open_form'})
                async def same_session(): yield session
                app.dependency_overrides[get_db_session] = same_session
                app.dependency_overrides[current_user] = lambda: actor
                async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                    path = f'/api/v1/quick-reports/checks/{check.id}/form-drafts'
                    with patch('requests.sessions.Session.request', side_effect=AssertionError('No network')):
                        response = await client.get(path)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(len(response.json()), 2)
                    self.assertIn('private, no-store', response.headers['cache-control'])
                    for form in response.json():
                        self.assertIn('https://fixture.monster/login?q=1', form['draft'])
                        self.assertIn('Fixture Brand', form['draft'])
                    with patch('requests.sessions.Session.request', side_effect=AssertionError('No network')):
                        expanded = await client.get(path + '?include_providers=true')
                    self.assertEqual(expanded.status_code, 200, expanded.text)
                    self.assertEqual(len(expanded.json()), 8)
                    cloudflare = next(row for row in expanded.json() if row['channel'] == 'infrastructure')
                    self.assertEqual(cloudflare['draft'], toolkit.generate_cloudflare_report_text(check.domain, {'brand_name': 'Fixture Brand'}, check.requested_url))
                    general = [row for row in expanded.json() if row['channel'] == 'general']
                    self.assertEqual([row['label'] for row in general], ['Google', 'Microsoft', 'Chống Lừa Đảo', 'Cốc Cốc Safe', 'Netcraft'])
                    self.assertEqual(next(row['draft'] for row in general if row['label'] == 'Microsoft'), '')
                    for row in general:
                        if row['draft']:
                            self.assertIn('https://fixture.monster/login?q=1', row['draft'])
                    actor.user_id = second.id
                    self.assertEqual((await client.get(path)).status_code, 404)
                    self.assertEqual((await client.get(path + '?include_providers=true')).status_code, 404)
                    actor.permissions = set()
                    self.assertEqual((await client.get(path)).status_code, 403)
        finally:
            await database.dispose()
            await app.state.database.dispose()
            await app.state.redis.aclose()
