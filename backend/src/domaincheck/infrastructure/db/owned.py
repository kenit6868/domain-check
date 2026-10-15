from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement


ModelT = TypeVar("ModelT")


class OwnedRepository(Generic[ModelT]):
    """Reusable query boundary for rows owned by one authenticated account.

    The owner is fixed when the repository is constructed. It cannot be
    supplied again by list/get/update/delete calls, which prevents an endpoint
    from accidentally accepting an owner override from request data.
    """

    def __init__(self, session: AsyncSession, model: type[ModelT], owner_user_id: UUID):
        table = getattr(model, "__table__", None)
        if table is None or "owner_user_id" not in table.c or "id" not in table.c:
            raise TypeError("OwnedRepository requires id and owner_user_id columns")
        self._session = session
        self._model = model
        self._owner_user_id = owner_user_id

    @property
    def owner_user_id(self) -> UUID:
        return self._owner_user_id

    def _scope(self, criteria: Sequence[ColumnElement[bool]] = ()) -> tuple[ColumnElement[bool], ...]:
        return (getattr(self._model, "owner_user_id") == self._owner_user_id, *criteria)

    async def get(self, resource_id: UUID, *, for_update: bool = False) -> ModelT | None:
        statement = select(self._model).where(
            *self._scope((getattr(self._model, "id") == resource_id,))
        )
        if for_update:
            # A request may have loaded this identity before waiting on a row
            # lock. Refresh it after the lock is acquired instead of trusting
            # a now-stale identity-map value.
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return await self._session.scalar(statement)

    async def list(
        self,
        *,
        criteria: Sequence[ColumnElement[bool]] = (),
        order_by: Sequence[Any] = (),
        offset: int = 0,
        limit: int = 100,
    ) -> list[ModelT]:
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("offset must be non-negative and limit must be between 1 and 500")
        statement = select(self._model).where(*self._scope(criteria))
        if order_by:
            statement = statement.order_by(*order_by)
        rows = await self._session.scalars(statement.offset(offset).limit(limit))
        return list(rows)

    async def count(self, *, criteria: Sequence[ColumnElement[bool]] = ()) -> int:
        value = await self._session.scalar(
            select(func.count()).select_from(self._model).where(*self._scope(criteria))
        )
        return int(value or 0)

    async def update(
        self,
        resource_id: UUID,
        values: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> bool:
        self._validate_values(values)
        criteria: list[ColumnElement[bool]] = [getattr(self._model, "id") == resource_id]
        assignments = dict(values)
        has_version = hasattr(self._model, "version")
        if expected_version is not None:
            if not has_version:
                raise TypeError("Model does not support optimistic versioning")
            criteria.append(getattr(self._model, "version") == expected_version)
        if has_version:
            assignments["version"] = getattr(self._model, "version") + 1
        result = await self._session.execute(
            update(self._model).where(*self._scope(criteria)).values(**assignments)
        )
        return result.rowcount == 1

    async def delete(self, resource_id: UUID) -> bool:
        result = await self._session.execute(
            delete(self._model).where(
                *self._scope((getattr(self._model, "id") == resource_id,))
            )
        )
        return result.rowcount == 1

    def _validate_values(self, values: dict[str, Any]) -> None:
        if not values:
            raise ValueError("At least one update value is required")
        forbidden = {"id", "owner_user_id", "created_at", "updated_at", "version"}.intersection(values)
        unknown = set(values).difference(self._model.__table__.c.keys())
        if forbidden or unknown:
            raise ValueError("Update contains forbidden or unknown fields")
