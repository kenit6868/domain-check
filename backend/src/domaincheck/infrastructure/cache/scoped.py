from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from redis.asyncio import Redis
from redis.exceptions import RedisError


CACHE_CLEAR_PERMISSIONS = {
    "quick-report": "quick_report.clear_cache",
    "cloudflare-worker": "cloudflare_worker.clear_cache",
    "domain-worker": "domain_worker.clear_cache",
}
_TOKEN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


class CacheUnavailable(RuntimeError):
    pass


def _zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Unknown IANA timezone") from exc


def business_day(now: datetime, timezone_name: str) -> date:
    if now.tzinfo is None:
        raise ValueError("Cache clock must return a timezone-aware datetime")
    return now.astimezone(_zone(timezone_name)).date()


def seconds_until_next_midnight(now: datetime, timezone_name: str) -> int:
    if now.tzinfo is None:
        raise ValueError("Cache clock must return a timezone-aware datetime")
    zone = _zone(timezone_name)
    local_now = now.astimezone(zone)
    next_day = local_now.date() + timedelta(days=1)
    next_midnight = datetime.combine(next_day, time.min, tzinfo=zone)
    seconds = (next_midnight.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds()
    return max(1, math.ceil(seconds))


@dataclass(frozen=True, slots=True)
class ScopedCacheKey:
    owner_user_id: UUID
    feature: str
    bucket: str
    identity: str | bytes
    business_day: date
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not _TOKEN.fullmatch(self.feature) or not _TOKEN.fullmatch(self.bucket):
            raise ValueError("Cache feature and bucket must be registered-style tokens")
        if self.schema_version < 1:
            raise ValueError("Cache schema version must be positive")

    @property
    def digest(self) -> str:
        value = self.identity.encode("utf-8") if isinstance(self.identity, str) else self.identity
        return hashlib.sha256(value).hexdigest()


class ScopedRedisCache:
    """Best-effort cache for reproducible data; never a durable ledger."""

    def __init__(self, redis: Redis, *, namespace: str = "domaincheck:v1"):
        self._redis = redis
        self._namespace = namespace.rstrip(":")

    def key(self, scope: ScopedCacheKey) -> str:
        return (
            f"{self._namespace}:u:{scope.owner_user_id}:{scope.feature}:"
            f"{scope.business_day.isoformat()}:{scope.bucket}:v{scope.schema_version}:h:{scope.digest}"
        )

    async def get_json(self, scope: ScopedCacheKey) -> Any | None:
        try:
            raw = await self._redis.get(self.key(scope))
        except (RedisError, OSError, TimeoutError):
            return None
        if raw is None:
            return None
        try:
            envelope = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(envelope, dict):
            return None
        if envelope.get("business_day") != scope.business_day.isoformat():
            return None
        if envelope.get("schema_version") != scope.schema_version:
            return None
        return envelope.get("value")

    async def set_json(
        self,
        scope: ScopedCacheKey,
        value: Any,
        *,
        now: datetime,
        timezone_name: str,
    ) -> bool:
        if scope.business_day != business_day(now, timezone_name):
            raise ValueError("Cache scope business day does not match the operation timezone")
        payload = json.dumps(
            {
                "business_day": scope.business_day.isoformat(),
                "schema_version": scope.schema_version,
                "value": value,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        ttl = seconds_until_next_midnight(now, timezone_name)
        try:
            await self._redis.set(self.key(scope), payload, ex=ttl)
        except (RedisError, OSError, TimeoutError):
            return False
        return True

    async def clear_feature_day(self, owner_user_id: UUID, feature: str, day: date) -> int:
        if feature not in CACHE_CLEAR_PERMISSIONS:
            raise ValueError("Cache feature is not clearable")
        pattern = f"{self._namespace}:u:{owner_user_id}:{feature}:{day.isoformat()}:*"
        deleted = 0
        try:
            batch: list[str] = []
            async for key in self._redis.scan_iter(match=pattern, count=200):
                batch.append(key)
                if len(batch) >= 200:
                    deleted += int(await self._redis.unlink(*batch))
                    batch.clear()
            if batch:
                deleted += int(await self._redis.unlink(*batch))
        except (RedisError, OSError, TimeoutError) as exc:
            raise CacheUnavailable("Cache service is unavailable") from exc
        return deleted
