"""Shared asyncpg fakes for the approval-gate service characterization tests.

The approval services use the ``async with pool.acquire() as conn`` →
``conn.fetchrow / fetch / execute`` pattern. These fakes record every
``execute`` (sql, args) so tests can assert on the SQL issued, and return
canned ``fetchrow`` / ``fetch`` results. Kept deliberately tiny — they exist
to pin current behavior before the #622 gate-machinery extraction, not to
emulate Postgres.
"""

from __future__ import annotations

from typing import Any


class FakeConn:
    def __init__(
        self,
        *,
        fetchrow_result: Any = None,
        fetch_result: Any = None,
    ) -> None:
        self._fetchrow_result = fetchrow_result
        self._fetch_result = fetch_result if fetch_result is not None else []
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        if callable(self._fetchrow_result):
            return self._fetchrow_result(sql, args)
        return self._fetchrow_result

    async def fetch(self, sql: str, *args: Any) -> Any:
        if callable(self._fetch_result):
            return self._fetch_result(sql, args)
        return self._fetch_result

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "OK"

    def transaction(self) -> _Transaction:
        return _Transaction()


class _Transaction:
    """No-op stand-in for ``asyncpg`` connection.transaction()."""

    async def __aenter__(self) -> _Transaction:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _Acquire:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class FakePool:
    """Minimal asyncpg-pool stand-in returning a single shared FakeConn.

    Supports both access patterns asyncpg exposes: ``async with pool.acquire()
    as conn`` and the pool-level shortcuts ``pool.fetchrow/fetch/execute`` that
    asyncpg proxies to a transient connection.
    """

    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        return await self.conn.fetchrow(sql, *args)

    async def fetch(self, sql: str, *args: Any) -> Any:
        return await self.conn.fetch(sql, *args)

    async def execute(self, sql: str, *args: Any) -> str:
        return await self.conn.execute(sql, *args)


def executed_sql(conn: FakeConn) -> str:
    """Concatenate every executed statement for substring assertions."""
    return "\n".join(sql for sql, _ in conn.executed)
