from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class Database:
    def __init__(self, database_url: str, *, echo: bool = False, **engine_options: Any):
        engine_options.setdefault('pool_size', 5)
        engine_options.setdefault('max_overflow', 2)
        engine_options.setdefault('pool_timeout', 30)
        self.engine: AsyncEngine = create_async_engine(
            database_url,
            echo=echo,
            pool_pre_ping=True,
            **engine_options,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()


class UnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
        self.session: AsyncSession | None = None
        self._transaction = None

    async def __aenter__(self) -> "UnitOfWork":
        self.session = self._session_factory()
        self._transaction = await self.session.begin()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        assert self.session is not None
        try:
            if exc_type is None:
                await self._transaction.commit()
            else:
                await self._transaction.rollback()
        finally:
            await self.session.close()
            self.session = None
            self._transaction = None

