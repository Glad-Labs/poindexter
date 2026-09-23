"""Unit tests for services/unsubscribe_relay.py.

Pins the invariants that make a queued opt-out safe to lose sleep over:
validation happens here (the Worker has no DB), an already-unsubscribed
token does not overwrite its original timestamp, and the ack happens only
after the write lands.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from poindexter.services.unsubscribe_relay import drain_unsubscribe_queue

TOKEN = "A" * 43
OTHER = "B" * 43


class FakeConn:
    def __init__(self, db: FakeDb):
        self.db = db

    async def execute(self, query: str, *args: Any) -> str:
        assert "UPDATE newsletter_subscribers" in query
        # The route's own guard: only an ACTIVE subscription flips, so a
        # re-click cannot overwrite the original unsubscribed_at.
        assert "unsubscribed_at IS NULL" in query
        token = args[0]
        if token in self.db.active:
            self.db.active.remove(token)
            self.db.unsubscribed.append(token)
            return "UPDATE 1"
        return "UPDATE 0"


class _Acquire:
    def __init__(self, c): self._c = c
    async def __aenter__(self): return self._c
    async def __aexit__(self, *e): return None


class FakeDb:
    def __init__(self, active: list[str] | None = None):
        self.active = set(active or [])
        self.unsubscribed: list[str] = []
        self._conn = FakeConn(self)

    def acquire(self): return _Acquire(self._conn)


class FakeSiteConfig:
    def __init__(self, url="https://relay.example.com", secret="s3cret"):
        self._url, self._secret = url, secret

    def get(self, key: str, default: str = "") -> str:
        return self._url if key == "newsletter_unsubscribe_relay_url" else default

    async def get_secret(self, key: str, default: str = "") -> str:
        return self._secret if key == "newsletter_unsubscribe_relay_secret" else default


def transport_for(pending: list[str], *, calls: list[dict] | None = None,
                  ack_status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append({"method": request.method, "path": request.url.path})
        assert request.headers.get("Authorization") == "Bearer s3cret"
        if request.url.path == "/pending":
            return httpx.Response(200, json={"tokens": pending})
        if request.url.path == "/ack":
            body = json.loads(request.content)
            return httpx.Response(ack_status, json={"removed": len(body["tokens"])})
        raise AssertionError(f"unexpected {request.url}")
    return httpx.MockTransport(handler)


async def test_active_token_is_unsubscribed_and_acked():
    db = FakeDb(active=[TOKEN])
    out = await drain_unsubscribe_queue(
        db, FakeSiteConfig(), transport=transport_for([TOKEN])
    )
    assert out.pending_seen == 1
    assert out.unsubscribed == 1
    assert out.acked == 1
    assert db.unsubscribed == [TOKEN]


async def test_unknown_token_is_acked_not_retried_forever():
    """The Worker cannot validate tokens, so junk posted at the public
    endpoint arrives here. It must drain, not accumulate."""
    db = FakeDb(active=[])
    out = await drain_unsubscribe_queue(
        db, FakeSiteConfig(), transport=transport_for([TOKEN])
    )
    assert out.unsubscribed == 0
    assert out.already_or_unknown == 1
    assert out.acked == 1


async def test_already_unsubscribed_does_not_overwrite():
    db = FakeDb(active=[])          # token exists but already opted out
    out = await drain_unsubscribe_queue(
        db, FakeSiteConfig(), transport=transport_for([TOKEN])
    )
    assert out.unsubscribed == 0
    assert out.already_or_unknown == 1


async def test_malformed_tokens_are_dropped_before_touching_the_db():
    db = FakeDb(active=[TOKEN])
    out = await drain_unsubscribe_queue(
        db, FakeSiteConfig(), transport=transport_for(["nope", "", "../*", TOKEN])
    )
    assert out.pending_seen == 1        # only the well-formed one survives
    assert out.unsubscribed == 1


async def test_ack_happens_after_the_write():
    """Ack-first would drop an opt-out if the process died mid-drain;
    write-first only costs a harmless re-apply."""
    calls: list[dict] = []
    db = FakeDb(active=[TOKEN])
    await drain_unsubscribe_queue(
        db, FakeSiteConfig(), transport=transport_for([TOKEN], calls=calls)
    )
    assert [c["path"] for c in calls] == ["/pending", "/ack"]
    assert db.unsubscribed == [TOKEN]


async def test_no_relay_configured_is_a_quiet_noop():
    out = await drain_unsubscribe_queue(FakeDb(), FakeSiteConfig(url=""))
    assert out.pending_seen == 0
    assert out.errors == []


async def test_missing_secret_reports_instead_of_silently_passing():
    out = await drain_unsubscribe_queue(FakeDb(), FakeSiteConfig(secret=""))
    assert out.errors == ["newsletter_unsubscribe_relay_secret not set"]


async def test_mixed_batch_applies_each_independently():
    db = FakeDb(active=[TOKEN])          # OTHER is unknown
    out = await drain_unsubscribe_queue(
        db, FakeSiteConfig(), transport=transport_for([TOKEN, OTHER])
    )
    assert out.unsubscribed == 1
    assert out.already_or_unknown == 1
    assert out.acked == 2
