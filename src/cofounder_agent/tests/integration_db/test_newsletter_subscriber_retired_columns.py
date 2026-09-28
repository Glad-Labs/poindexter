"""Integration: the ``newsletter_subscribers`` columns nothing read are gone.

Migration 20260928_184647 drops ``company``, ``interest_categories``,
``marketing_consent``, ``ip_address`` and ``user_agent``, plus the GIN index
over ``interest_categories`` (Glad-Labs/poindexter#1109). In the same change,
``POST /api/newsletter/subscribe`` stops writing them. The unit tests fake the
pool, so they cannot show that the route's statement still runs once the
columns are gone. These tests capture that exact statement from the real route
and run it on the migrated table, then drive the migration's own ``up()`` and
``down()``, all inside the rolled-back ``test_txn``.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "poindexter"
    / "services"
    / "migrations"
    / "20260928_184647_drop_the_newsletter_subscriber_columns_nothing_reads.py"
)
_RETIRED = {"company", "interest_categories", "marketing_consent", "ip_address", "user_agent"}
_PROBE_EMAIL = "retired-columns-probe@example.com"


def _load():
    spec = importlib.util.spec_from_file_location("drop_newsletter_columns_int", _MIGRATION)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class _Acquire:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _OneConnPool:
    """Hands ``up()``/``down()`` the test's rolled-back connection."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self._conn)


async def _columns(conn: Any) -> dict[str, dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT column_name, data_type, character_maximum_length, column_default
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'newsletter_subscribers'
        """
    )
    return {r["column_name"]: dict(r) for r in rows}


async def _gin_index_exists(conn: Any) -> bool:
    return await conn.fetchval(
        "SELECT to_regclass('public.idx_newsletter_interests_gin') IS NOT NULL"
    )


async def _route_statements(monkeypatch, payload: dict[str, Any]) -> tuple[tuple, tuple]:
    """POST ``payload`` through the real route with a recording fake pool.

    Returns the (existence check, INSERT) calls exactly as the route issued
    them: SQL first, then the bind arguments.
    """
    from poindexter.routes import newsletter_routes
    from poindexter.services.site_config import SiteConfig
    from poindexter.utils.rate_limiter import limiter
    from poindexter.utils.route_utils import get_database_dependency, get_site_config_dependency

    async def _no_mirror(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(newsletter_routes, "mirror_signup_to_segment", _no_mirror)
    monkeypatch.setattr(limiter, "enabled", False)

    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=1)
    db = MagicMock()
    db.pool = pool
    db.cloud_pool = None

    app = FastAPI()
    app.include_router(newsletter_routes.router)
    app.dependency_overrides[get_database_dependency] = lambda: db
    app.dependency_overrides[get_site_config_dependency] = lambda: SiteConfig(initial_config={})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://worker") as client:
        resp = await client.post("/api/newsletter/subscribe", json=payload)
    assert resp.status_code == 200, resp.text
    return pool.fetchrow.await_args.args, pool.fetchval.await_args.args


async def test_migrated_table_has_none_of_the_retired_columns(test_txn) -> None:
    columns = await _columns(test_txn)
    assert not _RETIRED & set(columns), sorted(_RETIRED & set(columns))
    assert not await _gin_index_exists(test_txn)
    # What the route and the segment sync still write, and the send path reads.
    assert {
        "email",
        "first_name",
        "last_name",
        "subscribed_at",
        "verified",
        "unsubscribe_token",
        "unsubscribed_at",
        "unsubscribe_reason",
    } <= set(columns)


async def test_the_routes_insert_runs_against_the_migrated_table(test_txn, monkeypatch) -> None:
    """An old-shape payload, retired fields and all, still makes a row."""
    lookup, insert = await _route_statements(
        monkeypatch,
        {
            "email": _PROBE_EMAIL,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "company": "Acme Corp",
            "interest_categories": ["AI"],
            "marketing_consent": True,
        },
    )

    assert await test_txn.fetchrow(*lookup) is None
    new_id = await test_txn.fetchval(*insert)

    row = await test_txn.fetchrow(
        "SELECT email, first_name, last_name, verified, unsubscribe_token, unsubscribed_at "
        "FROM newsletter_subscribers WHERE id = $1",
        new_id,
    )
    assert row["email"] == _PROBE_EMAIL
    assert (row["first_name"], row["last_name"]) == ("Ada", "Lovelace")
    assert row["verified"] is True
    assert len(row["unsubscribe_token"]) == 43
    assert row["unsubscribed_at"] is None


async def test_up_discards_the_values_and_down_restores_the_columns(test_txn, caplog) -> None:
    mig = _load()
    pool = _OneConnPool(test_txn)

    # Put the pre-migration shape back inside the rolled-back transaction.
    await mig.down(pool)
    restored = await _columns(test_txn)
    assert _RETIRED <= set(restored)
    # Same definitions as 0000_baseline.schema.sql.
    assert restored["company"]["character_maximum_length"] == 255
    assert restored["interest_categories"]["data_type"] == "jsonb"
    assert restored["marketing_consent"]["data_type"] == "boolean"
    assert restored["marketing_consent"]["column_default"] == "false"
    assert restored["ip_address"]["character_maximum_length"] == 45
    assert restored["user_agent"]["data_type"] == "text"
    assert await _gin_index_exists(test_txn)

    await test_txn.execute(
        """
        INSERT INTO newsletter_subscribers
            (email, company, interest_categories, marketing_consent,
             ip_address, user_agent, unsubscribe_token)
        VALUES ($1, 'Acme Corp', '["AI"]'::jsonb, TRUE, '172.18.0.1', 'node', $2)
        """,
        _PROBE_EMAIL,
        "p" * 43,
    )

    with caplog.at_level(logging.INFO):
        await mig.up(pool)

    assert not _RETIRED & set(await _columns(test_txn))
    assert not await _gin_index_exists(test_txn)
    # The row survives; only the five values went with their columns.
    assert (
        await test_txn.fetchval(
            "SELECT count(*) FROM newsletter_subscribers WHERE email = $1", _PROBE_EMAIL
        )
        == 1
    )

    discarded = [
        r.getMessage()
        for r in caplog.records
        if r.levelname == "WARNING" and "discarding filled values" in r.getMessage()
    ]
    assert len(discarded) == 1
    for part in (
        "company=1",
        "interest_categories=1",
        "marketing_consent=1",
        "ip_address=1",
        "user_agent=1",
    ):
        assert part in discarded[0], discarded[0]

    # Safe to re-run: every drop is IF EXISTS, and there is nothing left to count.
    caplog.clear()
    with caplog.at_level(logging.INFO):
        await mig.up(pool)
    assert not [r for r in caplog.records if "discarding filled values" in r.getMessage()]
