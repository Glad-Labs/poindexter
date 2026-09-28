"""Regression guard for curating ``research_tier2_domains``.

The previous default scored three platforms that host anyone's writing
(github.com, medium.com, dev.to) as credible, and no authoritative ``.org``
host. The default changed, and this migration fixes a row still at the old
value, which ``seed_all_defaults`` never rewrites.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from poindexter.services.research_quality_service import ResearchQualityService
from poindexter.services.settings_defaults import DEFAULTS

_MIGRATION_FILE = (
    Path(__file__).resolve().parents[4]
    / "poindexter"
    / "services"
    / "migrations"
    / "20260928_141243_curate_research_tier2_domains.py"
)
_KEY = "research_tier2_domains"
_OLD = (
    "medium.com,dev.to,github.com,stackoverflow.com,wikipedia.org,"
    "arxiv.org,research.google.com,aws.amazon.com,cloud.google.com,"
    "microsoft.com,apple.com"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(_MIGRATION_FILE.stem, _MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class _FakeConn:
    def __init__(self, rows: dict[str, str]) -> None:
        self.rows = dict(rows)
        self.updates: list[tuple[Any, ...]] = []

    async def fetchval(self, _sql: str, key: str) -> str | None:
        return self.rows.get(key)

    async def execute(self, _sql: str, value: str, key: str) -> str:
        self.updates.append((key, value))
        self.rows[key] = value
        return "UPDATE 1"


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "old",
    [_OLD, _OLD.upper(), " , ".join(reversed(_OLD.split(",")))],
)
async def test_rewrites_a_row_still_at_the_old_default(old):
    migration = _load_migration()
    conn = _FakeConn({_KEY: old})
    await migration.up(_FakePool(conn))
    assert conn.rows[_KEY] == migration._NEW_DEFAULT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operator_value",
    [
        _OLD + ",nature.com",  # the old default plus the operator's own entry
        "stackoverflow.com,wikipedia.org",
        "",
    ],
)
async def test_leaves_any_other_list_alone(operator_value):
    conn = _FakeConn({_KEY: operator_value})
    await _load_migration().up(_FakePool(conn))
    assert conn.updates == []
    assert conn.rows[_KEY] == operator_value


@pytest.mark.asyncio
async def test_is_a_noop_without_the_row():
    conn = _FakeConn({})
    await _load_migration().up(_FakePool(conn))
    assert conn.updates == []


@pytest.mark.asyncio
async def test_is_idempotent():
    conn = _FakeConn({_KEY: _OLD})
    migration = _load_migration()
    await migration.up(_FakePool(conn))
    await migration.up(_FakePool(conn))
    assert len(conn.updates) == 1


def test_old_default_is_what_was_seeded_before():
    assert _load_migration()._OLD_DEFAULT == frozenset(_OLD.split(","))


def test_rewrites_to_the_value_the_code_and_the_seed_now_ship():
    migration = _load_migration()
    target = migration._NEW_DEFAULT.split(",")
    assert len(target) == len(set(target))  # no duplicate entries
    shipped = {d.strip() for d in DEFAULTS[_KEY].split(",")}
    assert set(target) == shipped == set(ResearchQualityService._DEFAULT_TIER_2_DOMAINS)
    assert not {"github.com", "medium.com", "dev.to"} & shipped
