"""``newsletter_audience.sync_segment_to_subscribers`` against the REAL schema.

The unit tests fake the three statements the pull issues; these run them on a
migrated database inside a rolled-back transaction. What only the real table
can show: the case-insensitive match against a case-sensitive UNIQUE column,
the ``COALESCE`` that turns Resend's ``created_at`` into ``subscribed_at``
(asyncpg binds timestamptz from a datetime only), the NOT NULL unsubscribe
token, and the ``unsubscribed_at IS NULL`` guard that keeps an existing
opt-out's timestamp and reason intact.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_SEGMENT = "44444444-aaaa-4bbb-8ccc-eeeeeeeeeeee"
_CANARY = "delivered+signup-canary@resend.dev"


class _Acquire:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _OneConnPool:
    """Hands the sync the test's rolled-back connection."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self._conn)


class _SiteConfig:
    def get(self, key: str, default: str = "") -> str:
        return {
            "resend_audience_id": _SEGMENT,
            "newsletter_signup_canary_email": _CANARY,
        }.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        return default

    async def get_secret(self, key: str, default: str = "") -> str:
        return "re_test" if key == "resend_api_key" else default


def _contact(email: str, *, unsubscribed: bool = False, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"c-{email}",
        "email": email,
        "first_name": extra.get("first_name"),
        "last_name": None,
        "created_at": extra.get("created_at", "2026-09-01 12:00:00+00"),
        "unsubscribed": unsubscribed,
    }


def _segment(*contacts: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/segments/{_SEGMENT}/contacts"
        return httpx.Response(
            200, json={"object": "list", "has_more": False, "data": list(contacts)}
        )

    return httpx.MockTransport(handler)


async def _no_pause(_s: float) -> None:
    return None


async def _owned(conn: Any, email: str, token: str, *, unsubscribed: bool = False) -> None:
    await conn.execute(
        "INSERT INTO newsletter_subscribers (email, verified, unsubscribe_token, "
        "unsubscribed_at, unsubscribe_reason) VALUES ($1, TRUE, $2, "
        "CASE WHEN $3 THEN TIMESTAMPTZ '2026-08-01 00:00:00+00' END, "
        "CASE WHEN $3 THEN 'relay' END)",
        email, token, unsubscribed,
    )


async def test_pull_reconciles_the_segment_against_the_real_table(test_txn) -> None:
    from poindexter.services.newsletter_audience import (
        RESEND_UNSUBSCRIBE_REASON,
        sync_segment_to_subscribers,
    )

    await _owned(test_txn, "Active@Example.com", "a" * 43)
    await _owned(test_txn, "gone@example.com", "b" * 43, unsubscribed=True)
    await _owned(test_txn, "leaving@example.com", "c" * 43)

    contacts = (
        _contact("new@example.com", first_name="Grace"),
        _contact("active@example.com"),                      # case variant
        _contact("gone@example.com"),                        # opted out here
        _contact("leaving@example.com", unsubscribed=True),  # opted out in Resend
        _contact(_CANARY),
    )
    outcome = await sync_segment_to_subscribers(
        _OneConnPool(test_txn), _SiteConfig(),
        transport=_segment(*contacts), pause=_no_pause,
    )
    assert outcome.errors == []
    assert (
        outcome.imported, outcome.already_subscribed, outcome.opted_out_kept,
        outcome.unsubscribes_applied, outcome.skipped_canary,
    ) == (1, 1, 1, 1, 1)

    new = await test_txn.fetchrow(
        "SELECT * FROM newsletter_subscribers WHERE email = 'new@example.com'"
    )
    assert new["verified"] is True
    assert new["first_name"] == "Grace"
    assert len(new["unsubscribe_token"]) == 43
    assert new["unsubscribed_at"] is None
    # Resend's created_at, not the moment the pull ran.
    assert new["subscribed_at"] == datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    assert await test_txn.fetchval(
        "SELECT count(*) FROM newsletter_subscribers WHERE lower(email) = 'active@example.com'"
    ) == 1

    gone = await test_txn.fetchrow(
        "SELECT unsubscribed_at, unsubscribe_reason FROM newsletter_subscribers "
        "WHERE email = 'gone@example.com'"
    )
    assert gone["unsubscribed_at"] == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert gone["unsubscribe_reason"] == "relay"

    leaving = await test_txn.fetchrow(
        "SELECT unsubscribed_at, unsubscribe_reason FROM newsletter_subscribers "
        "WHERE email = 'leaving@example.com'"
    )
    assert leaving["unsubscribed_at"] is not None
    assert leaving["unsubscribe_reason"] == RESEND_UNSUBSCRIBE_REASON

    assert await test_txn.fetchval(
        "SELECT count(*) FROM newsletter_subscribers WHERE lower(email) = $1", _CANARY
    ) == 0

    # A second pull over the same segment changes nothing.
    again = await sync_segment_to_subscribers(
        _OneConnPool(test_txn), _SiteConfig(),
        transport=_segment(*contacts), pause=_no_pause,
    )
    assert (again.imported, again.unsubscribes_applied, again.errors) == (0, 0, [])
    assert again.already_unsubscribed == 1


async def test_an_unparseable_created_at_falls_back_to_now(test_txn) -> None:
    from poindexter.services.newsletter_audience import sync_segment_to_subscribers

    before = await test_txn.fetchval("SELECT CURRENT_TIMESTAMP")
    outcome = await sync_segment_to_subscribers(
        _OneConnPool(test_txn), _SiteConfig(),
        transport=_segment(_contact("undated@example.com", created_at="garbage")),
        pause=_no_pause,
    )
    assert outcome.imported == 1
    subscribed_at = await test_txn.fetchval(
        "SELECT subscribed_at FROM newsletter_subscribers WHERE email = 'undated@example.com'"
    )
    assert subscribed_at >= before
