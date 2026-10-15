from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError

from domaincheck.api.cache import CacheClearInput, clear_cache
from domaincheck.application.auth import AuthenticatedUser
from domaincheck.infrastructure.cache import (
    CacheUnavailable,
    ScopedCacheKey,
    ScopedRedisCache,
    business_day,
    seconds_until_next_midnight,
)


TEST_REDIS_URL = os.environ.get("DOMAINCHECK_TEST_REDIS_URL")


class MemoryRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, ex=None, **_kwargs):
        self.values[key] = value
        self.ttls[key] = ex
        return True

    async def scan_iter(self, *, match, count=10):
        prefix = match.removesuffix("*")
        for key in list(self.values):
            if key.startswith(prefix):
                yield key

    async def unlink(self, *keys):
        removed = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                removed += 1
        return removed


class FailingRedis(MemoryRedis):
    async def get(self, key):
        raise RedisError("fixture unavailable")

    async def set(self, key, value, **kwargs):
        raise RedisError("fixture unavailable")

    async def scan_iter(self, **kwargs):
        raise RedisError("fixture unavailable")
        yield  # pragma: no cover


class ScopedCacheTests(unittest.IsolatedAsyncioTestCase):
    def test_ttl_uses_next_local_midnight_including_dst(self):
        bangkok_now = datetime(2026, 10, 15, 16, 30, tzinfo=timezone.utc)
        self.assertEqual(seconds_until_next_midnight(bangkok_now, "Asia/Bangkok"), 1800)
        new_york_spring_day = datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc)
        self.assertEqual(seconds_until_next_midnight(new_york_spring_day, "America/New_York"), 23 * 3600)
        self.assertEqual(business_day(bangkok_now, "Asia/Bangkok"), date(2026, 10, 15))
        self.assertEqual(business_day(bangkok_now, "America/New_York"), date(2026, 10, 15))

    async def test_key_is_owner_scoped_and_hashes_sensitive_identity(self):
        cache = ScopedRedisCache(MemoryRedis())
        identity = "https://example.test/login?token=private"
        first = ScopedCacheKey(uuid4(), "quick-report", "lookup", identity, date(2026, 10, 15))
        second = ScopedCacheKey(uuid4(), "quick-report", "lookup", identity, date(2026, 10, 15))
        self.assertNotEqual(cache.key(first), cache.key(second))
        self.assertNotIn(identity, cache.key(first))
        self.assertIn(":quick-report:2026-10-15:lookup:v1:h:", cache.key(first))

    async def test_set_get_checks_day_and_expires_at_midnight(self):
        redis = MemoryRedis()
        cache = ScopedRedisCache(redis)
        now = datetime(2026, 10, 15, 16, 30, tzinfo=timezone.utc)
        scope = ScopedCacheKey(uuid4(), "quick-report", "lookup", "fixture", date(2026, 10, 15))
        self.assertTrue(await cache.set_json(scope, {"status": "cached"}, now=now, timezone_name="Asia/Bangkok"))
        self.assertEqual(await cache.get_json(scope), {"status": "cached"})
        self.assertEqual(redis.ttls[cache.key(scope)], 1800)
        wrong_day = ScopedCacheKey(scope.owner_user_id, "quick-report", "lookup", "other", date(2026, 10, 14))
        with self.assertRaises(ValueError):
            await cache.set_json(wrong_day, {}, now=now, timezone_name="Asia/Bangkok")

    async def test_clear_only_removes_selected_owner_feature_and_day(self):
        redis = MemoryRedis()
        cache = ScopedRedisCache(redis)
        owner_a, owner_b = uuid4(), uuid4()
        now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        scopes = [
            ScopedCacheKey(owner_a, "quick-report", "lookup", "a", date(2026, 10, 15)),
            ScopedCacheKey(owner_a, "domain-worker", "lookup", "a", date(2026, 10, 15)),
            ScopedCacheKey(owner_b, "quick-report", "lookup", "a", date(2026, 10, 15)),
        ]
        for scope in scopes:
            await cache.set_json(scope, {"value": 1}, now=now, timezone_name="Asia/Bangkok")
        deleted = await cache.clear_feature_day(owner_a, "quick-report", date(2026, 10, 15))
        self.assertEqual(deleted, 1)
        self.assertIsNone(await cache.get_json(scopes[0]))
        self.assertIsNotNone(await cache.get_json(scopes[1]))
        self.assertIsNotNone(await cache.get_json(scopes[2]))

    async def test_redis_loss_is_cache_miss_but_clear_reports_unavailable(self):
        cache = ScopedRedisCache(FailingRedis())
        scope = ScopedCacheKey(uuid4(), "quick-report", "lookup", "fixture", date(2026, 10, 15))
        now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        self.assertIsNone(await cache.get_json(scope))
        self.assertFalse(await cache.set_json(scope, {}, now=now, timezone_name="Asia/Bangkok"))
        with self.assertRaises(CacheUnavailable):
            await cache.clear_feature_day(scope.owner_user_id, "quick-report", scope.business_day)

    async def test_clear_api_uses_authenticated_owner_and_feature_permission(self):
        redis = MemoryRedis()
        cache = ScopedRedisCache(redis)
        owner = uuid4()
        actor = AuthenticatedUser(
            user_id=owner, display_name="Fixture", telegram_username=None, status="active",
            timezone="Asia/Bangkok", permissions=frozenset({"quick_report.clear_cache"}),
            session_id=uuid4(), csrf_hash=b"x" * 32,
        )
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scoped_cache=cache)))
        response = await clear_cache(
            CacheClearInput(feature="quick-report", business_day=date(2026, 10, 15)), request, actor
        )
        self.assertEqual(response.deleted_count, 0)
        with self.assertRaises(HTTPException) as raised:
            await clear_cache(CacheClearInput(feature="domain-worker"), request, actor)
        self.assertEqual(raised.exception.status_code, 403)
        unavailable_request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(scoped_cache=ScopedRedisCache(FailingRedis())))
        )
        with self.assertRaises(HTTPException) as unavailable:
            await clear_cache(
                CacheClearInput(feature="quick-report", business_day=date(2026, 10, 15)),
                unavailable_request,
                actor,
            )
        self.assertEqual(unavailable.exception.status_code, 503)


@unittest.skipUnless(TEST_REDIS_URL, "requires isolated Redis test instance")
class RedisIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = Redis.from_url(TEST_REDIS_URL or "", decode_responses=True)
        await self.redis.flushdb()
        self.cache = ScopedRedisCache(self.redis, namespace="domaincheck:test-cache")

    async def asyncTearDown(self):
        await self.redis.flushdb()
        await self.redis.aclose()

    async def test_owner_clear_and_flush_are_non_durable(self):
        day = date(2026, 10, 15)
        now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        owner_a, owner_b = uuid4(), uuid4()
        scope_a = ScopedCacheKey(owner_a, "quick-report", "lookup", "same-url", day)
        scope_b = ScopedCacheKey(owner_b, "quick-report", "lookup", "same-url", day)
        await self.cache.set_json(scope_a, {"owner": "a"}, now=now, timezone_name="Asia/Bangkok")
        await self.cache.set_json(scope_b, {"owner": "b"}, now=now, timezone_name="Asia/Bangkok")
        self.assertEqual(await self.cache.clear_feature_day(owner_a, "quick-report", day), 1)
        self.assertIsNone(await self.cache.get_json(scope_a))
        self.assertEqual(await self.cache.get_json(scope_b), {"owner": "b"})
        await self.redis.flushdb()
        self.assertIsNone(await self.cache.get_json(scope_b))


if __name__ == "__main__":
    unittest.main()
