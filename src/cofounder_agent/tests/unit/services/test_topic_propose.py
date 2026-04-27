"""Unit tests for ``services/topic_proposal_service.py`` (#146).

Covers:

- ``propose_topic`` writes a ``pipeline_tasks`` row and routes it
  through the gate so it lands at ``awaiting_gate='topic_decision'``
  when the gate is enabled.
- Gate disabled → row lands at ``status='pending'`` (no gate columns
  set), matching anticipation_engine's existing behaviour.
- Empty topic → ValueError (bail-loud rule).
- Missing pool → RuntimeError.

DB writes + the ``pause_at_gate`` notify path are mocked. No live
Postgres / Telegram calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.topic_proposal_service import (
    DEFAULT_MAX_PENDING,
    propose_topic,
    resolve_max_pending,
)


def _make_site_config(values: dict[str, str] | None = None) -> Any:
    cache = dict(values or {})

    def _get(key, default=None):
        return cache.get(key, default)

    def _get_int(key, default=0):
        try:
            return int(cache.get(key, default) or default)
        except (TypeError, ValueError):
            return int(default)

    return SimpleNamespace(get=_get, get_int=_get_int)


def _make_pool(*, pending_count: int = 0) -> Any:
    """Mock asyncpg pool — supports the connection-context-manager dance.

    ``async with pool.acquire() as conn:`` uses an async ctx manager.
    ``MagicMock`` doesn't speak that out of the box; we wire it with a
    helper class so both ``conn.execute`` and ``conn.fetchval`` are
    AsyncMocks the test can assert on.
    """
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.fetchval = AsyncMock(return_value=pending_count)

    class _AcquireCtx:
        async def __aenter__(self_inner):
            return conn

        async def __aexit__(self_inner, *_):
            return False

    pool = MagicMock()
    pool.acquire = lambda: _AcquireCtx()
    pool._conn = conn  # exposed for the test to assert on
    return pool


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.asyncio
    async def test_empty_topic_raises(self):
        with pytest.raises(ValueError):
            await propose_topic(
                topic="",
                site_config=_make_site_config(),
                pool=_make_pool(),
            )

    @pytest.mark.asyncio
    async def test_whitespace_topic_raises(self):
        with pytest.raises(ValueError):
            await propose_topic(
                topic="   \n\t",
                site_config=_make_site_config(),
                pool=_make_pool(),
            )

    @pytest.mark.asyncio
    async def test_missing_pool_raises(self):
        with pytest.raises(RuntimeError):
            await propose_topic(
                topic="A fine topic",
                site_config=_make_site_config(),
                pool=None,
            )


# ---------------------------------------------------------------------------
# Gate disabled — row lands at status=pending, no gate columns set
# ---------------------------------------------------------------------------


class TestGateDisabled:
    @pytest.mark.asyncio
    async def test_lands_at_pending_no_pause_call(self):
        site_cfg = _make_site_config({})  # gate flag absent → default off
        pool = _make_pool()

        with patch(
            "services.topic_proposal_service.pause_at_gate",
            AsyncMock(),
        ) as mock_pause:
            result = await propose_topic(
                topic="A great topic",
                primary_keyword="kw",
                site_config=site_cfg,
                pool=pool,
            )

        assert result["ok"] is True
        assert result["awaiting_gate"] is None
        assert result["status"] == "pending"
        assert result["gate_enabled"] is False
        assert mock_pause.await_count == 0
        # We still wrote the pipeline_tasks row.
        assert pool._conn.execute.await_count >= 1

    @pytest.mark.asyncio
    async def test_topic_string_is_trimmed(self):
        site_cfg = _make_site_config({})
        pool = _make_pool()

        with patch(
            "services.topic_proposal_service.pause_at_gate",
            AsyncMock(),
        ):
            result = await propose_topic(
                topic="   trimmed   ",
                site_config=site_cfg,
                pool=pool,
            )
        assert result["topic"] == "trimmed"


# ---------------------------------------------------------------------------
# Gate enabled — row paused at topic_decision, artifact built, notify fires
# ---------------------------------------------------------------------------


class TestGateEnabled:
    @pytest.mark.asyncio
    async def test_lands_at_awaiting_gate(self):
        site_cfg = _make_site_config({"pipeline_gate_topic_decision": "on"})
        pool = _make_pool(pending_count=0)
        captured: dict[str, Any] = {}

        async def _fake_pause(**kwargs):
            captured.update(kwargs)
            return {"ok": True, "paused_at": "x", "notify": {"sent": True}}

        with patch(
            "services.topic_proposal_service.pause_at_gate",
            AsyncMock(side_effect=_fake_pause),
        ):
            result = await propose_topic(
                topic="Custom water cooling 2026",
                primary_keyword="custom water cooling",
                tags=["pc-hardware", "cooling"],
                category="hardware",
                source="manual",
                site_config=site_cfg,
                pool=pool,
                notify=False,
            )

        assert result["ok"] is True
        assert result["awaiting_gate"] == "topic_decision"
        assert result["gate_enabled"] is True
        assert result["queue_full"] is False
        # Artifact carries the source label so list --source filters work.
        assert captured["gate_name"] == "topic_decision"
        assert captured["artifact"]["topic"] == "Custom water cooling 2026"
        assert captured["artifact"]["source"] == "manual"
        assert captured["artifact"]["tags"] == ["pc-hardware", "cooling"]
        assert captured["artifact"]["primary_keyword"] == "custom water cooling"

    @pytest.mark.asyncio
    async def test_inserts_pipeline_tasks_row(self):
        site_cfg = _make_site_config({"pipeline_gate_topic_decision": "on"})
        pool = _make_pool(pending_count=0)

        with patch(
            "services.topic_proposal_service.pause_at_gate",
            AsyncMock(return_value={"ok": True, "paused_at": "x", "notify": {}}),
        ):
            result = await propose_topic(
                topic="Sample",
                site_config=site_cfg,
                pool=pool,
                notify=False,
            )
        # First call inserts pipeline_tasks; second call inserts version.
        assert pool._conn.execute.await_count >= 2
        first_call = pool._conn.execute.await_args_list[0]
        sql = first_call.args[0]
        assert "INSERT INTO pipeline_tasks" in sql
        # task_id slot — second positional arg to execute() — should be
        # a non-empty string we can echo back to the operator.
        assert result["task_id"]
        assert isinstance(result["task_id"], str)


# ---------------------------------------------------------------------------
# Queue cap — refuses to propose past the cap when the gate is enabled
# ---------------------------------------------------------------------------


class TestQueueCap:
    def test_resolve_max_pending_default(self):
        assert resolve_max_pending(_make_site_config({})) == DEFAULT_MAX_PENDING

    def test_resolve_max_pending_custom(self):
        site_cfg = _make_site_config({"topic_discovery_max_pending": "10"})
        assert resolve_max_pending(site_cfg) == 10

    def test_resolve_max_pending_invalid_falls_back(self):
        site_cfg = _make_site_config({"topic_discovery_max_pending": "not-a-number"})
        # Falls back to default rather than crashing — the "no silent
        # fallback" rule says "log loudly", not "raise unhandled".
        assert resolve_max_pending(site_cfg) == DEFAULT_MAX_PENDING

    @pytest.mark.asyncio
    async def test_propose_refused_when_queue_full(self):
        site_cfg = _make_site_config({
            "pipeline_gate_topic_decision": "on",
            "topic_discovery_max_pending": "3",
        })
        pool = _make_pool(pending_count=3)  # at cap

        with patch(
            "services.topic_proposal_service.pause_at_gate",
            AsyncMock(),
        ) as mock_pause:
            result = await propose_topic(
                topic="One more",
                site_config=site_cfg,
                pool=pool,
                notify=False,
            )

        assert result["ok"] is False
        assert result["queue_full"] is True
        assert "full" in (result.get("detail") or "").lower()
        # No insert, no pause.
        assert mock_pause.await_count == 0
        # No INSERT either — the cap check refuses early so we don't
        # leave an orphan pipeline_tasks row that never gets paused.
        # (execute may still be called for the count query, but our
        # mock pool's pending count came from fetchval so execute
        # should be zero here.)
        assert pool._conn.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_cap_ignored_when_gate_disabled(self):
        # Gate off — the cap doesn't apply since the auto-queue path
        # hasn't historically been capped.
        site_cfg = _make_site_config({"topic_discovery_max_pending": "0"})
        pool = _make_pool(pending_count=999)

        with patch(
            "services.topic_proposal_service.pause_at_gate",
            AsyncMock(),
        ):
            result = await propose_topic(
                topic="With gate off",
                site_config=site_cfg,
                pool=pool,
            )
        assert result["ok"] is True
        assert result["status"] == "pending"
