"""Cross-feature authorization checks without database or external operations."""
import inspect
import re
import unittest
from types import SimpleNamespace
from uuid import uuid4

from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from domaincheck.api.dependencies import current_user, get_db_session, require_csrf
from domaincheck.domain.authz import PERMISSIONS_BY_CODE
from domaincheck.infrastructure.auth.sessions import hash_token
from domaincheck.main import create_app
from domaincheck.settings import Settings


def guards(dependency):
    for child in dependency.dependencies:
        call = child.call
        if inspect.isfunction(call):
            values = inspect.getclosurevars(call).nonlocals
            if call.__module__ == 'domaincheck.api.dependencies' and 'code' in values:
                yield child, values['code']
        yield from guards(child)


def uses(dependency, call):
    return dependency.call is call or any(uses(child, call) for child in dependency.dependencies)


def routes(router, prefix=''):
    for route in router.routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, 'original_router'):
            yield from routes(route.original_router, prefix + route.include_context.prefix)


class PermissionAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_registered_business_routes_deny_without_action_permission(self):
        app = create_app(Settings(_env_file=None))
        actor = SimpleNamespace(user_id=uuid4(), status='active', timezone='Asia/Bangkok',
                                permissions=frozenset(), csrf_hash=hash_token('csrf'))

        async def user(): return actor
        async def db(): yield SimpleNamespace()

        app.dependency_overrides[current_user] = user
        app.dependency_overrides[get_db_session] = db
        count = 0
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                for full_path, route in routes(app):
                    protected = list(guards(route.dependant))
                    if not protected:
                        self.assertIn(full_path, {
                            '/api/v1/health/live', '/api/v1/auth/telegram/start',
                            '/api/v1/auth/telegram/status', '/api/v1/auth/telegram/callback',
                            '/api/v1/me', '/api/v1/auth/logout', '/api/v1/artifacts/{artifact_id}',
                            '/api/v1/cache/clear', '/api/v1/form-tasks/exchange',
                            '/api/v1/form-tasks/{task_id}/status',
                        }, 'New business route needs an explicit permission guard')
                        continue
                    for guard, code in protected:
                        self.assertIn(code, PERMISSIONS_BY_CODE, route.path)
                        if route.methods & {'POST', 'PUT', 'PATCH', 'DELETE'}:
                            self.assertTrue(uses(guard, require_csrf), route.path)
                    path = re.sub(r'\{[^}]+\}', str(uuid4()), full_path)
                    for method in route.methods:
                        with self.subTest(method=method, path=route.path):
                            for permissions in (frozenset(), frozenset(PERMISSIONS_BY_CODE) - {protected[0][1]}):
                                actor.permissions = permissions
                                response = await client.request(method, path, json={}, headers={
                                    'Origin': 'http://127.0.0.1:5173', 'X-CSRF-Token': 'csrf'})
                                self.assertEqual(response.status_code, 403, response.text)
                            if method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
                                actor.permissions = frozenset(PERMISSIONS_BY_CODE)
                                response = await client.request(method, path, json={})
                                self.assertEqual(response.status_code, 403, response.text)
                            count += 1
                actor.permissions = frozenset()
                response = await client.post('/api/v1/cache/clear', json={'feature': 'quick-report'},
                    headers={'Origin': 'http://127.0.0.1:5173', 'X-CSRF-Token': 'csrf'})
                self.assertEqual(response.status_code, 403)
            self.assertGreater(count, 70)
        finally:
            await app.state.database.dispose()
            await app.state.redis.aclose()
