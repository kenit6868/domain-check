from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Trusted identity and operation scope created by the server.

    ``owner_user_id`` is never accepted from a feature request body. Admin
    cross-owner support is intentionally absent from the first web version.
    """

    actor_user_id: UUID
    owner_user_id: UUID
    timezone: str
    operation_id: UUID
    request_id: str
    permissions: frozenset[str]
    clock: Clock

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise PermissionError(f"Missing permission: {permission}")

