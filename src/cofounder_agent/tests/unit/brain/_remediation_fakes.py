"""Shared asyncpg-pool test double for the firefighter unit tests.

Records executes for assertion, and lets each test register canned results for
fetch / fetchval / fetchrow keyed by a substring of the SQL so a single pool can
serve several distinct queries in call order.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


class FakePool:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self._fetch: Callable[[str, tuple], list] | None = None
        self._fetchval: Callable[[str, tuple], Any] | None = None
        self._fetchrow: Callable[[str, tuple], Any] | None = None

    def set_fetch(self, fn: Callable[[str, tuple], list]) -> None:
        self._fetch = fn

    def set_fetchval(self, fn: Callable[[str, tuple], Any]) -> None:
        self._fetchval = fn

    def set_fetchrow(self, fn: Callable[[str, tuple], Any]) -> None:
        self._fetchrow = fn

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list:
        return list(self._fetch(sql, args)) if self._fetch else []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return self._fetchval(sql, args) if self._fetchval else None

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        return self._fetchrow(sql, args) if self._fetchrow else None
