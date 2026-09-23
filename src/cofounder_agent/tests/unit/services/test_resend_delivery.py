"""Unit tests for services/resend_delivery.py.

The poll replaces an unreachable webhook (POST /api/webhooks/resend answers
401 locally, 404 publicly), so these pin the behaviours that made the
webhook's silence invisible: only terminal states are recorded, re-polling
writes nothing, and a state transition appends rather than mutates.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from poindexter.services.resend_delivery import (
    RECORDED_EVENTS,
    poll_delivery_state,
)


class FakeConn:
    def __init__(self, db: FakeDb):
        self.db = db

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "FROM newsletter_subscribers" in query:
            # The real query selects 1 (existence), not the int id — see the
            # identity note in resend_delivery._record_event.
            return 1 if str(args[0]).lower() in self.db.subscribers else None
        raise AssertionError(f"unexpected fetchval: {query}")

    async def execute(self, query: str, *args: Any) -> str:
        if "INSERT INTO subscriber_events" in query:
            # The uuid subscriber_id column was dropped (migration
            # 20260923_225300) — nothing could ever populate it, and reading
            # its NULLs as meaningful is what failed this poll's first prod
            # tick on all 19 messages. email is the identity.
            assert "subscriber_id" not in query, (
                "subscriber_events.subscriber_id was dropped; do not write it"
            )
            email, event_type, event_data, message_id = args
            key = (message_id, event_type)
            if key in self.db.keys:
                return "INSERT 0 0"          # ux_subscriber_events_provider_event
            self.db.keys.add(key)
            self.db.events.append(
                {
                    "email": email,
                    "event_type": event_type,
                    "event_data": json.loads(event_data),
                    "provider_message_id": message_id,
                }
            )
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {query}")


class _Acquire:
    def __init__(self, conn): self._c = conn
    async def __aenter__(self): return self._c
    async def __aexit__(self, *exc): return None


class FakeDb:
    def __init__(self):
        self.events: list[dict[str, Any]] = []
        self.keys: set[tuple] = set()
        self.subscribers: dict[str, int] = {}
        self._conn = FakeConn(self)

    def acquire(self): return _Acquire(self._conn)


class FakeSiteConfig:
    def __init__(self, key: str = "re_test_key"):
        self._key = key

    async def get_secret(self, name: str, default: str = "") -> str:
        return self._key if name == "resend_api_key" else default


def email(msg_id="e1", last_event="delivered", to="buyer@example.com", subject="Post"):
    return {
        "id": msg_id,
        "to": [to],
        "from": "Glad Labs <newsletter@gladlabs.io>",
        "subject": subject,
        "last_event": last_event,
        "created_at": "2026-09-23T10:00:00.000Z",
        "message_id": f"<{msg_id}@email.amazonses.com>",
    }


def transport_for(*pages):
    """MockTransport serving the given /emails pages in order."""
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.resend.com/emails" in str(request.url)
        page = pages[min(state["i"], len(pages) - 1)]
        state["i"] += 1
        return httpx.Response(200, json=page)

    return httpx.MockTransport(handler)


def page(*emails, has_more=False):
    return {"object": "list", "has_more": has_more, "data": list(emails)}


async def test_delivered_email_is_recorded():
    db = FakeDb()
    db.subscribers["buyer@example.com"] = 7
    outcome = await poll_delivery_state(
        db, FakeSiteConfig(), transport=transport_for(page(email()))
    )
    assert outcome.emails_seen == 1
    assert outcome.rows_written == 1
    row = db.events[0]
    assert row["event_type"] == "email.delivered"
    assert row["provider_message_id"] == "e1"
    assert row["event_data"]["via"] == "resend_delivery_poll"
    # Identity is the EMAIL — the uuid subscriber_id column is gone.
    assert "subscriber_id" not in row
    assert row["email"] == "buyer@example.com"
    # A known recipient is not counted as unknown.
    assert outcome.unknown_recipients == 0


async def test_repoll_writes_nothing():
    """The unique index makes a re-poll free — this is why the job has no
    cursor and can safely re-read the whole window every hour."""
    db = FakeDb()
    t = transport_for(page(email()))
    await poll_delivery_state(db, FakeSiteConfig(), transport=t)
    second = await poll_delivery_state(
        db, FakeSiteConfig(), transport=transport_for(page(email()))
    )
    assert second.emails_seen == 1
    assert second.rows_written == 0
    assert len(db.events) == 1


async def test_state_transition_appends_rather_than_mutates():
    """delivered -> complained is a new fact, not a correction. The ledger
    stays append-only so the original delivery is not erased."""
    db = FakeDb()
    await poll_delivery_state(
        db, FakeSiteConfig(), transport=transport_for(page(email(last_event="delivered")))
    )
    await poll_delivery_state(
        db, FakeSiteConfig(), transport=transport_for(page(email(last_event="complained")))
    )
    assert [e["event_type"] for e in db.events] == [
        "email.delivered",
        "email.complained",
    ]


@pytest.mark.parametrize("state", ["sent", "queued", "scheduled"])
async def test_non_terminal_states_are_skipped(state):
    """Pre-delivery states churn a row per poll until they settle and are
    not outcomes, so they never enter the ledger."""
    db = FakeDb()
    outcome = await poll_delivery_state(
        db, FakeSiteConfig(), transport=transport_for(page(email(last_event=state)))
    )
    assert outcome.skipped_non_terminal == 1
    assert outcome.rows_written == 0
    assert db.events == []


async def test_bounce_is_recorded():
    db = FakeDb()
    db.subscribers["buyer@example.com"] = 7
    outcome = await poll_delivery_state(
        db, FakeSiteConfig(), transport=transport_for(page(email(last_event="bounced")))
    )
    assert outcome.rows_written == 1
    assert db.events[0]["event_type"] == "email.bounced"


async def test_unknown_recipient_is_counted_but_still_recorded():
    """A bounce for someone no longer on the list is exactly what you want
    to keep — record it, and count it so a stale audience is visible."""
    db = FakeDb()  # no subscribers registered
    outcome = await poll_delivery_state(
        db, FakeSiteConfig(), transport=transport_for(page(email(last_event="bounced")))
    )
    assert outcome.rows_written == 1
    assert outcome.unknown_recipients == 1
    assert db.events[0]["email"] == "buyer@example.com"


async def test_missing_api_key_reports_instead_of_silently_passing():
    db = FakeDb()
    outcome = await poll_delivery_state(db, FakeSiteConfig(key=""))
    assert outcome.errors == ["resend_api_key not set"]
    assert outcome.rows_written == 0


async def test_pagination_follows_has_more():
    db = FakeDb()
    t = transport_for(
        page(email("e1"), has_more=True),
        page(email("e2"), has_more=False),
    )
    outcome = await poll_delivery_state(db, FakeSiteConfig(), transport=t)
    assert outcome.emails_seen == 2
    assert {e["provider_message_id"] for e in db.events} == {"e1", "e2"}


def test_recorded_events_excludes_pre_delivery_states():
    assert "delivered" in RECORDED_EVENTS
    assert "bounced" in RECORDED_EVENTS
    assert "complained" in RECORDED_EVENTS
    assert "sent" not in RECORDED_EVENTS
    assert "queued" not in RECORDED_EVENTS
