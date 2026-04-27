"""Unit tests for ``services/rejection_handlers.py`` (#148).

Per-gate handlers turn operator rejections into actionable signals:

- ``topic_decision_handler``  → writes a brain_knowledge weight-down row.
- ``preview_approval_handler`` → emits a ``task.regen_draft`` event with reason as steering.
- ``final_publish_approval_handler`` → emits a ``task.regen_media`` event.

All three honor a per-gate retry cap so degenerate "always reject"
loops can't burn the GPU forever.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.rejection_handlers import (
    RejectionContext,
    dispatch_rejection,
    final_publish_approval_handler,
    get_handler,
    list_registered_handlers,
    preview_approval_handler,
    register_handler,
    topic_decision_handler,
)


# ---------------------------------------------------------------------------
# Fake pool — minimal in-memory store for the SQL the handlers use
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
        if sql_norm.startswith("INSERT INTO brain_knowledge"):
            entity, attribute, value, confidence, source = args
            self._store.brain_knowledge.append({
                "entity": entity, "attribute": attribute, "value": value,
                "confidence": confidence, "source": source,
            })
            return "INSERT 0 1"
        if sql_norm.startswith("INSERT INTO pipeline_events"):
            event_type, payload = args
            self._store.pipeline_events.append({
                "event_type": event_type, "payload": payload,
            })
            return "INSERT 0 1"
        raise AssertionError(
            f"FakeConnection.execute saw unexpected SQL: {sql_norm[:80]}"
        )

    async def fetchval(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT COUNT(*) FROM pipeline_events"):
            event_type, primary_id = args
            return sum(
                1 for e in self._store.pipeline_events
                if e["event_type"] == event_type
                and primary_id in (e.get("payload") or "")
            )
        raise AssertionError(
            f"FakeConnection.fetchval saw unexpected SQL: {sql_norm[:80]}"
        )


class FakeStore:
    def __init__(self) -> None:
        self.brain_knowledge: list[dict[str, Any]] = []
        self.pipeline_events: list[dict[str, Any]] = []


class FakePool:
    def __init__(self) -> None:
        self.store = FakeStore()

    def acquire(self):
        return FakeConnection(self.store)

    # The handlers call pool.execute / pool.fetchval directly (no
    # acquire context) for the cheap one-off queries.
    async def execute(self, sql: str, *args):
        async with self.acquire() as conn:
            return await conn.execute(sql, *args)

    async def fetchval(self, sql: str, *args):
        async with self.acquire() as conn:
            return await conn.fetchval(sql, *args)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeSiteConfig:
    def __init__(self, **kvs: Any) -> None:
        self._cache = dict(kvs)

    def get(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)


@pytest.fixture
def fake_pool():
    return FakePool()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_bundled_handlers_registered_at_import(self):
        registered = set(list_registered_handlers())
        assert "topic_decision" in registered
        assert "preview_approval" in registered
        assert "final_publish_approval" in registered

    def test_register_and_get_roundtrip(self):
        async def _custom(_ctx):
            pass
        register_handler("__test_gate", _custom)
        assert get_handler("__test_gate") is _custom

    def test_unknown_gate_returns_none(self):
        assert get_handler("__never_registered_gate") is None


# ---------------------------------------------------------------------------
# topic_decision_handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTopicDecisionHandler:
    async def test_writes_brain_signal(self, fake_pool):
        ctx = RejectionContext(
            gate_name="topic_decision",
            task_id="t-1",
            post_id=None,
            reason="off-brand for our voice",
            artifact={"topic": "Crypto memecoins", "title": "Crypto memecoins"},
            pool=fake_pool,
            site_config=None,
        )
        await topic_decision_handler(ctx)
        assert len(fake_pool.store.brain_knowledge) == 1
        row = fake_pool.store.brain_knowledge[0]
        assert row["entity"] == "topic:Crypto memecoins"
        assert row["attribute"] == "rejected_by_operator"
        assert "off-brand" in row["value"]
        assert row["confidence"] >= 0.8

    async def test_skips_when_artifact_missing_title(self, fake_pool):
        ctx = RejectionContext(
            gate_name="topic_decision",
            task_id="t-1",
            post_id=None,
            reason="x",
            artifact={},
            pool=fake_pool,
            site_config=None,
        )
        await topic_decision_handler(ctx)
        # Empty artifact → nothing written rather than a junk
        # entity:"" row.
        assert fake_pool.store.brain_knowledge == []

    async def test_no_pipeline_events_emitted(self, fake_pool):
        """Topic rejection is terminal — no regen, no event."""
        ctx = RejectionContext(
            gate_name="topic_decision",
            task_id="t-1",
            post_id=None,
            reason="bad",
            artifact={"title": "Something"},
            pool=fake_pool,
            site_config=None,
        )
        await topic_decision_handler(ctx)
        assert fake_pool.store.pipeline_events == []


# ---------------------------------------------------------------------------
# preview_approval_handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPreviewApprovalHandler:
    async def test_emits_regen_event_with_feedback(self, fake_pool):
        ctx = RejectionContext(
            gate_name="preview_approval",
            task_id="t-1",
            post_id=None,
            reason="title is clickbait",
            artifact={"title": "10 SHOCKING tricks"},
            pool=fake_pool,
            site_config=None,
        )
        await preview_approval_handler(ctx)
        assert len(fake_pool.store.pipeline_events) == 1
        ev = fake_pool.store.pipeline_events[0]
        assert ev["event_type"] == "task.regen_draft"
        assert "title is clickbait" in ev["payload"]
        assert "t-1" in ev["payload"]
        assert "retry_n" in ev["payload"]

    async def test_retry_cap_blocks_third_emit(self, fake_pool):
        # Pre-seed two prior regen events for this task.
        fake_pool.store.pipeline_events = [
            {"event_type": "task.regen_draft",
             "payload": '{"primary_id": "t-1", "retry_n": 1}'},
            {"event_type": "task.regen_draft",
             "payload": '{"primary_id": "t-1", "retry_n": 2}'},
        ]
        ctx = RejectionContext(
            gate_name="preview_approval",
            task_id="t-1",
            post_id=None,
            reason="still bad",
            artifact={},
            pool=fake_pool,
            site_config=_FakeSiteConfig(),  # default cap = 2
        )
        await preview_approval_handler(ctx)
        # Third regen must NOT fire — store stays at 2.
        regen_events = [
            e for e in fake_pool.store.pipeline_events
            if e["event_type"] == "task.regen_draft"
        ]
        assert len(regen_events) == 2

    async def test_custom_retry_cap_via_site_config(self, fake_pool):
        ctx = RejectionContext(
            gate_name="preview_approval",
            task_id="t-1",
            post_id=None,
            reason="r",
            artifact={},
            pool=fake_pool,
            # Higher cap → emit fires.
            site_config=_FakeSiteConfig(
                approval_gate_preview_approval_max_retries=5,
            ),
        )
        # Pre-seed 2 prior regens.
        for i in range(2):
            fake_pool.store.pipeline_events.append({
                "event_type": "task.regen_draft",
                "payload": f'{{"primary_id": "t-1", "retry_n": {i+1}}}',
            })
        await preview_approval_handler(ctx)
        regen_events = [
            e for e in fake_pool.store.pipeline_events
            if e["event_type"] == "task.regen_draft"
        ]
        # 2 prior + 1 new = 3 (cap raised to 5).
        assert len(regen_events) == 3

    async def test_no_task_id_skips_emission(self, fake_pool):
        ctx = RejectionContext(
            gate_name="preview_approval",
            task_id=None,
            post_id=None,
            reason="x",
            artifact={},
            pool=fake_pool,
            site_config=None,
        )
        await preview_approval_handler(ctx)
        assert fake_pool.store.pipeline_events == []


# ---------------------------------------------------------------------------
# final_publish_approval_handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFinalPublishApprovalHandler:
    async def test_emits_regen_media_event(self, fake_pool):
        ctx = RejectionContext(
            gate_name="final_publish_approval",
            task_id=None,
            post_id="p-1",
            reason="podcast voice is wrong",
            artifact={"title": "Hello", "slug": "hello"},
            pool=fake_pool,
            site_config=None,
        )
        await final_publish_approval_handler(ctx)
        assert len(fake_pool.store.pipeline_events) == 1
        ev = fake_pool.store.pipeline_events[0]
        # Specifically NOT regen_draft — the writing was fine.
        assert ev["event_type"] == "task.regen_media"
        assert "p-1" in ev["payload"]
        assert "podcast voice is wrong" in ev["payload"]

    async def test_retry_cap_default_2(self, fake_pool):
        for i in range(2):
            fake_pool.store.pipeline_events.append({
                "event_type": "task.regen_media",
                "payload": f'{{"primary_id": "p-1", "retry_n": {i+1}}}',
            })
        ctx = RejectionContext(
            gate_name="final_publish_approval",
            task_id=None,
            post_id="p-1",
            reason="still bad",
            artifact={},
            pool=fake_pool,
            site_config=_FakeSiteConfig(),
        )
        await final_publish_approval_handler(ctx)
        regen_events = [
            e for e in fake_pool.store.pipeline_events
            if e["event_type"] == "task.regen_media"
        ]
        assert len(regen_events) == 2  # cap held

    async def test_no_post_id_skips_emission(self, fake_pool):
        ctx = RejectionContext(
            gate_name="final_publish_approval",
            task_id=None,
            post_id=None,
            reason="x",
            artifact={},
            pool=fake_pool,
            site_config=None,
        )
        await final_publish_approval_handler(ctx)
        assert fake_pool.store.pipeline_events == []


# ---------------------------------------------------------------------------
# dispatch_rejection — registry routing + failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDispatchRejection:
    async def test_routes_to_registered_handler(self, fake_pool):
        ctx = RejectionContext(
            gate_name="topic_decision",
            task_id="t-1",
            post_id=None,
            reason="off-brand",
            artifact={"title": "X"},
            pool=fake_pool,
            site_config=None,
        )
        await dispatch_rejection(ctx)
        assert len(fake_pool.store.brain_knowledge) == 1

    async def test_unknown_gate_is_quiet_noop(self, fake_pool):
        """Gate without a registered handler — dispatch returns
        cleanly without writing anything."""
        ctx = RejectionContext(
            gate_name="__never_registered_gate",
            task_id="t-1",
            post_id=None,
            reason="x",
            artifact={},
            pool=fake_pool,
            site_config=None,
        )
        await dispatch_rejection(ctx)  # no exception
        assert fake_pool.store.brain_knowledge == []
        assert fake_pool.store.pipeline_events == []

    async def test_handler_exception_is_swallowed(self, fake_pool):
        """A buggy handler must NOT propagate — the rejection itself
        already committed."""
        async def _broken(_ctx):
            raise RuntimeError("simulated handler crash")

        register_handler("__test_broken_gate", _broken)
        ctx = RejectionContext(
            gate_name="__test_broken_gate",
            task_id="t-1",
            post_id=None,
            reason="x",
            artifact={},
            pool=fake_pool,
            site_config=None,
        )
        # Must not raise.
        await dispatch_rejection(ctx)
