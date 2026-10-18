from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from domaincheck.api.auth import telegram_callback
from domaincheck.infrastructure.auth.oidc import OIDCError


class LoginRedirectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        recorder = patch('domaincheck.api.auth.persist', new_callable=AsyncMock)
        self.recorder = recorder.start()
        self.addCleanup(recorder.stop)

    def request(self, binding=True):
        return SimpleNamespace(state=SimpleNamespace(request_id='login-test'), cookies={'dc_oidc_binding': 'fixture'} if binding else {},
            app=SimpleNamespace(state=SimpleNamespace(
                settings=SimpleNamespace(default_timezone='Asia/Bangkok', environment='production'),
                oidc=SimpleNamespace(exchange_code=AsyncMock(return_value=({'iss': 'https://oauth.telegram.org', 'sub': 'fixture'}, '/quick-report'))))))

    async def test_success_always_returns_dashboard_and_keeps_cookie_contract(self):
        for account_status in ('active', 'pending'):
            with self.subTest(account_status=account_status):
                login = SimpleNamespace(user=SimpleNamespace(status=account_status), token='fixture-session',
                    csrf_token='fixture-csrf', expires_at=datetime.now(UTC) + timedelta(hours=1))
                with patch('domaincheck.api.auth.AuthService') as service:
                    service.return_value.login_oidc_identity = AsyncMock(return_value=login)
                    response = await telegram_callback(self.request(), code='fixture', state='fixture', session=object())
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers['location'], '/')
                cookies = response.headers.getlist('set-cookie')
                self.assertTrue(any('dc_session=' in value and 'HttpOnly' in value and 'Secure' in value for value in cookies))
                self.assertTrue(any('dc_csrf=' in value and 'Secure' in value for value in cookies))

    async def test_expired_binding_and_exchange_error_return_login_without_raw_error(self):
        response = await telegram_callback(self.request(False), code='fixture', state='fixture', session=object())
        self.assertEqual(response.headers['location'], '/login?login_error=telegram_expired')
        request = self.request()
        request.app.state.oidc.exchange_code.side_effect = OIDCError('private failure detail')
        response = await telegram_callback(request, code='fixture', state='fixture', session=object())
        self.assertEqual(response.headers['location'], '/login?login_error=telegram_callback')
        self.assertNotIn('private failure', str(response.headers))
