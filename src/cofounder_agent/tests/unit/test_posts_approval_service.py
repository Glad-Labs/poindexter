"""Unit tests for ``services/posts_approval_service.py``.

Mirrors the structure of ``test_approval_service.py`` — fake asyncpg
pool, in-memory ``posts`` store, audit + notify patched. Coverage:

- ``pause_post_at_gate`` writes the gate columns + audit row.
- ``approve_publish`` clears the gate and leaves status='scheduled'
  for the publisher to pick up.
- ``approve_publish`` raises PostNotFoundError / PostNotPausedError /
  PostGateMismatchError per spec.
- ``reject_publish`` flips status to ``rejected`` (default) or a
  per-gate custom status from app_settings.
- ``list_pending_publish`` returns parsed artifacts ordered oldest-first
  with optional gate filter.
- ``show_pending_publish`` returns the single-row detail.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from services.posts_approval_service import (
    PostGateMismatchError,
    PostNotFoundError,
    PostNotPausedError,
    approve_publish,
    list_pending_publish,
    pause_post_at_gate,
    reject_publish,
    show_pending_publish,
)


# ---------------------------------------------------------------------------
# Fake asyncpg pool — in-memory ``posts`` store keyed on the SQL prefix.
# ---------------------------------------------------------------------------


class FakeConnection:
    def __init__(self, store: "FakeStore") -> None:
        self._store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("UPDATE posts SET awaiting_gate = $1"):
            gate, artifact_json, paused_at, post_id = args
            row = self._store.posts.get(post_id)
            if row is not None:
                row["awaiting_gate"] = gate
                row["gate_artifact"] = artifact_json
                row["gate_paused_at"] = paused_at
            return "UPDATE 1"
        if sql_norm.startswith("UPDATE posts SET awaiting_gate = NULL"):
            (post_id,) = args
            row = self._store.posts.get(post_id)
            if row is not None:
                row["awaiting_gate"] = None
                row["gate_artifact"] = "{}"
                row["gate_paused_at"] = None
            return "UPDATE 1"
        if sql_norm.startswith("UPDATE posts SET status = $2"):
            post_id, new_status = args
            row = self._store.posts.get(post_id)
            if row is not None:
                row["status"] = new_status
                row["awaiting_gate"] = None
                row["gate_artifact"] = "{}"
                row["gate_paused_at"] = None
            return "UPDATE 1"
        raise AssertionError(
            f"FakeConnection.execute saw unexpected SQL: {sql_norm[:80]}"
        )

    async def fetchrow(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT id::text AS id, slug, title"):
            (post_id,) = args
            row = self._store.posts.get(post_id)
            if row is None:
                return None
            return {
                "id": post_id,
                "slug": row.get("slug"),
                "title": row.get("title"),
                "status": row.get("status"),
                "published_at": row.get("published_at"),
                "awaiting_gate": row.get("awaiting_gate"),
                "gate_artifact": row.get("gate_artifact"),
                "gate_paused_at": row.get("gate_paused_at"),
            }
        raise AssertionError(
            f"FakeConnection.fetchrow saw unexpected SQL: {sql_norm[:80]}"
        )

    async def fetch(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT id::text AS post_id, slug, title"):
            gate_filter = args[0] if len(args) >= 2 else None
            limit = args[-1]
            rows = []
            for pid, p in self._store.posts.items():
                if not p.get("awaiting_gate"):
                    continue
                if gate_filter and p["awaiting_gate"] != gate_filter:
                    continue
                rows.append(
                    {
                        "post_id": pid,
                        "slug": p.get("slug"),
                        "title": p.get("title"),
                        "status": p.get("status"),
                        "published_at": p.get("published_at"),
                        "gate_name": p["awaiting_gate"],
                        "gate_artifact": p.get("gate_artifact"),
                        "gate_paused_at": p.get("gate_paused_at"),
                    }
                )
            rows.sort(
                key=lambda r: r["gate_paused_at"]
                or datetime.max.replace(tzinfo=timezone.utc),
            )
            return rows[:limit]
        raise AssertionError(
            f"FakeConnection.fetch saw unexpected SQL: {sql_norm[:80]}"
        )


class FakeStore:
    def __init__(self) -> None:
        self.posts: dict[str, dict[str, Any]] = {}


class FakePool:
    def __init__(self) -> None:
        self.store = FakeStore()

    def acquire(self):
        return FakeConnection(self.store)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_site_config(values: dict[str, str] | None = None):
    cache = dict(values or {})
    return SimpleNamespace(
        get=lambda k, d=None: cache.get(k, d),
        _config=cache,
    )


@pytest.fixture
def fake_pool():
    return FakePool()


@pytest.fixture
def patched_audit():
    with patch("services.posts_approval_service.audit_log_bg") as m:
        yield m


@pytest.fixture
def patched_notify():
    with patch(
        "services.posts_approval_service._notify_publish_gate_tripped",
    ) as m:
        async def _ok(**kwargs):
            return {"sent": True, "reason": "ok"}
        m.side_effect = _ok
        yield m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPausePostAtGate:
    async def test_writes_gate_columns_and_audit(
        self, fake_pool, patched_audit, patched_notify,
    ):
        fake_pool.store.posts["p-1"] = {
            "slug": "hello-world",
            "title": "Hello, World",
            "status": "scheduled",
        }
        result = await pause_post_at_gate(
            post_id="p-1",
            gate_name="final_publish_approval",
            artifact={"slug": "hello-world", "title": "Hello, World"},
            site_config=_make_site_config(),
            pool=fake_pool,
        )
        row = fake_pool.store.posts["p-1"]
        assert row["awaiting_gate"] == "final_publish_approval"
        assert json.loads(row["gate_artifact"]) == {
            "slug": "hello-world",
            "title": "Hello, World",
        }
        assert row["gate_paused_at"] is not None
        # Status NOT changed — remains 'scheduled'.
        assert row["status"] == "scheduled"
        assert result["ok"] is True
        assert result["gate_name"] == "final_publish_approval"
        # Audit log fired.
        assert patched_audit.called
        kwargs = patched_audit.call_args.kwargs
        assert kwargs["event_type"] == "approval_gate_paused"
        assert kwargs["details"]["gate_name"] == "final_publish_approval"
        assert kwargs["details"]["post_id"] == "p-1"
        # Notify path attempted.
        assert patched_notify.await_count == 1

    async def test_notify_off_skips_notification(
        self, fake_pool, patched_audit, patched_notify,
    ):
        fake_pool.store.posts["p-1"] = {"status": "scheduled"}
        result = await pause_post_at_gate(
            post_id="p-1",
            gate_name="final_publish_approval",
            artifact={"x": 1},
            site_config=_make_site_config(),
            pool=fake_pool,
            notify=False,
        )
        assert patched_notify.await_count == 0
        assert result["notify"]["sent"] is False
        assert result["notify"]["reason"] == "skipped"


@pytest.mark.asyncio
class TestApprovePublish:
    async def test_clears_gate_keeps_status_scheduled(
        self, fake_pool, patched_audit,
    ):
        fake_pool.store.posts["p-1"] = {
            "slug": "x", "title": "X",
            "status": "scheduled",
            "awaiting_gate": "final_publish_approval",
            "gate_artifact": json.dumps({"slug": "x"}),
            "gate_paused_at": datetime.now(timezone.utc),
        }
        result = await approve_publish(
            post_id="p-1",
            site_config=_make_site_config(),
            pool=fake_pool,
        )
        assert result["ok"] is True
        assert result["gate_name"] == "final_publish_approval"
        # Gate cleared, status untouched — publisher's next tick promotes.
        row = fake_pool.store.posts["p-1"]
        assert row["awaiting_gate"] is None
        assert row["status"] == "scheduled"

    async def test_raises_when_post_missing(self, fake_pool):
        with pytest.raises(PostNotFoundError):
            await approve_publish(
                post_id="nope",
                site_config=_make_site_config(),
                pool=fake_pool,
            )

    async def test_raises_when_no_gate_active(self, fake_pool):
        fake_pool.store.posts["p-1"] = {
            "status": "scheduled",
            "awaiting_gate": None,
        }
        with pytest.raises(PostNotPausedError):
            await approve_publish(
                post_id="p-1",
                site_config=_make_site_config(),
                pool=fake_pool,
            )

    async def test_raises_on_gate_mismatch(self, fake_pool, patched_audit):
        fake_pool.store.posts["p-1"] = {
            "status": "scheduled",
            "awaiting_gate": "final_publish_approval",
            "gate_artifact": "{}",
            "gate_paused_at": datetime.now(timezone.utc),
        }
        with pytest.raises(PostGateMismatchError):
            await approve_publish(
                post_id="p-1",
                gate_name="some_other_gate",
                site_config=_make_site_config(),
                pool=fake_pool,
            )

    async def test_records_feedback_in_audit(self, fake_pool, patched_audit):
        fake_pool.store.posts["p-1"] = {
            "status": "scheduled",
            "awaiting_gate": "final_publish_approval",
            "gate_artifact": "{}",
            "gate_paused_at": datetime.now(timezone.utc),
        }
        await approve_publish(
            post_id="p-1",
            feedback="ship it",
            site_config=_make_site_config(),
            pool=fake_pool,
        )
        assert patched_audit.called
        kwargs = patched_audit.call_args.kwargs
        assert kwargs["details"]["feedback"] == "ship it"


@pytest.mark.asyncio
class TestRejectPublish:
    async def test_default_status_is_rejected(self, fake_pool, patched_audit):
        fake_pool.store.posts["p-1"] = {
            "status": "scheduled",
            "awaiting_gate": "final_publish_approval",
            "gate_artifact": "{}",
            "gate_paused_at": datetime.now(timezone.utc),
        }
        result = await reject_publish(
            post_id="p-1",
            reason="off-brand",
            site_config=_make_site_config(),
            pool=fake_pool,
        )
        assert result["new_status"] == "rejected"
        assert fake_pool.store.posts["p-1"]["status"] == "rejected"
        assert fake_pool.store.posts["p-1"]["awaiting_gate"] is None

    async def test_custom_reject_status_via_settings(
        self, fake_pool, patched_audit,
    ):
        fake_pool.store.posts["p-1"] = {
            "status": "scheduled",
            "awaiting_gate": "final_publish_approval",
            "gate_artifact": "{}",
            "gate_paused_at": datetime.now(timezone.utc),
        }
        cfg = _make_site_config({
            "approval_gate_final_publish_approval_reject_status": "draft",
        })
        result = await reject_publish(
            post_id="p-1",
            site_config=cfg,
            pool=fake_pool,
        )
        assert result["new_status"] == "draft"
        assert fake_pool.store.posts["p-1"]["status"] == "draft"

    async def test_raises_on_unknown_post(self, fake_pool):
        with pytest.raises(PostNotFoundError):
            await reject_publish(
                post_id="nope",
                site_config=_make_site_config(),
                pool=fake_pool,
            )

    async def test_raises_on_gate_mismatch(self, fake_pool, patched_audit):
        fake_pool.store.posts["p-1"] = {
            "status": "scheduled",
            "awaiting_gate": "final_publish_approval",
            "gate_artifact": "{}",
            "gate_paused_at": datetime.now(timezone.utc),
        }
        with pytest.raises(PostGateMismatchError):
            await reject_publish(
                post_id="p-1",
                gate_name="other_gate",
                site_config=_make_site_config(),
                pool=fake_pool,
            )


@pytest.mark.asyncio
class TestListPendingPublish:
    async def test_orders_oldest_first(self, fake_pool):
        now = datetime.now(timezone.utc)
        fake_pool.store.posts["p-1"] = {
            "slug": "newer", "title": "Newer",
            "status": "scheduled",
            "awaiting_gate": "final_publish_approval",
            "gate_artifact": json.dumps({"k": "v"}),
            "gate_paused_at": now,
        }
        fake_pool.store.posts["p-2"] = {
            "slug": "older", "title": "Older",
            "status": "scheduled",
            "awaiting_gate": "final_publish_approval",
            "gate_artifact": json.dumps({"k": "v"}),
            "gate_paused_at": now - timedelta(hours=2),
        }
        rows = await list_pending_publish(pool=fake_pool)
        assert [r["post_id"] for r in rows] == ["p-2", "p-1"]
        assert all(isinstance(r["artifact"], dict) for r in rows)

    async def test_filters_by_gate_name(self, fake_pool):
        now = datetime.now(timezone.utc)
        fake_pool.store.posts["p-1"] = {
            "slug": "a", "title": "A",
            "status": "scheduled",
            "awaiting_gate": "final_publish_approval",
            "gate_artifact": "{}",
            "gate_paused_at": now,
        }
        fake_pool.store.posts["p-2"] = {
            "slug": "b", "title": "B",
            "status": "scheduled",
            "awaiting_gate": "another_gate",
            "gate_artifact": "{}",
            "gate_paused_at": now,
        }
        rows = await list_pending_publish(
            pool=fake_pool, gate_name="final_publish_approval",
        )
        assert {r["post_id"] for r in rows} == {"p-1"}

    async def test_skips_unpaused(self, fake_pool):
        fake_pool.store.posts["p-1"] = {
            "slug": "x", "title": "X",
            "status": "published",
            "awaiting_gate": None,
        }
        rows = await list_pending_publish(pool=fake_pool)
        assert rows == []


@pytest.mark.asyncio
class TestShowPendingPublish:
    async def test_returns_full_row(self, fake_pool):
        now = datetime.now(timezone.utc)
        fake_pool.store.posts["p-1"] = {
            "slug": "hello", "title": "Hello",
            "status": "scheduled",
            "published_at": now + timedelta(hours=1),
            "awaiting_gate": "final_publish_approval",
            "gate_artifact": json.dumps({"k": "v"}),
            "gate_paused_at": now,
        }
        row = await show_pending_publish(pool=fake_pool, post_id="p-1")
        assert row["post_id"] == "p-1"
        assert row["gate_name"] == "final_publish_approval"
        assert row["artifact"] == {"k": "v"}
        assert row["status"] == "scheduled"

    async def test_raises_when_post_missing(self, fake_pool):
        with pytest.raises(PostNotFoundError):
            await show_pending_publish(pool=fake_pool, post_id="nope")

    async def test_raises_when_not_paused(self, fake_pool):
        fake_pool.store.posts["p-1"] = {
            "status": "published",
            "awaiting_gate": None,
        }
        with pytest.raises(PostNotPausedError):
            await show_pending_publish(pool=fake_pool, post_id="p-1")
