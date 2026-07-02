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


def _make_pool(*, pending_count: int = 0,
               app_setting_template_slug: str | None = "canonical_blog") -> Any:
    """Mock asyncpg pool — supports the connection-context-manager dance.

    ``async with pool.acquire() as conn:`` uses an async ctx manager.
    ``MagicMock`` doesn't speak that out of the box; we wire it with a
    helper class so both ``conn.execute`` and ``conn.fetchval`` are
    AsyncMocks the test can assert on.

    ``app_setting_template_slug`` feeds the ``template_slug_resolver``
    integration in ``propose_topic`` — defaults to ``'canonical_blog'``
    so existing tests keep working without re-asserting on the slug.
    """
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.fetchval = AsyncMock(return_value=pending_count)

    async def _fetchrow(sql, *args, **kwargs):
        # Only the resolver uses fetchrow on this code path; serve the
        # app_settings row shape it expects.
        if "FROM app_settings" in sql:
            if app_setting_template_slug is None:
                return None
            return {"value": app_setting_template_slug}
        return None

    conn.fetchrow = AsyncMock(side_effect=_fetchrow)

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


class TestTopicSanityGate:
    """2026-06-30 dots-incident guard for the manual path: contentless
    topics raise ``TopicSanityError`` (a ``ValueError``, so the CLI/HTTP
    adapters surface it as a friendly error) before any DB write."""

    # The real topic from pipeline_tasks 9921678f-9b5b-4d24-9f07-c9d0398cf793.
    DOTS_TOPIC = ". .. . ... . .... . .... . ... ."

    @pytest.mark.asyncio
    async def test_dots_topic_raises_before_insert(self):
        from services.topic_sanity import TopicSanityError

        pool = _make_pool()
        with pytest.raises(TopicSanityError):
            await propose_topic(
                topic=self.DOTS_TOPIC,
                site_config=_make_site_config(),
                pool=pool,
            )
        pool._conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_single_word_topic_raises_at_default_min(self):
        with pytest.raises(ValueError, match="sanity"):
            await propose_topic(
                topic="Cybersecurity",
                site_config=_make_site_config(),
                pool=_make_pool(),
            )

    @pytest.mark.asyncio
    async def test_operator_tuned_min_allows_single_word(self):
        site_cfg = _make_site_config({"topic_sanity_min_alpha_words": "1"})
        result = await propose_topic(
            topic="Cybersecurity",
            site_config=site_cfg,
            pool=_make_pool(),
        )
        assert result["ok"] is True


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
                topic="   trimmed headline   ",
                site_config=site_cfg,
                pool=pool,
            )
        assert result["topic"] == "trimmed headline"


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
                topic="Sample topic",
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
# Target-length variation (#542) — None gets a varied length from the
# shared picker; an explicit caller value always wins.
# ---------------------------------------------------------------------------


class TestTargetLengthVariation:
    @pytest.mark.asyncio
    async def test_explicit_length_wins(self):
        site_cfg = _make_site_config({})
        pool = _make_pool()
        with patch(
            "services.topic_proposal_service.pause_at_gate",
            AsyncMock(),
        ):
            await propose_topic(
                topic="Explicit length topic",
                target_length=2750,
                site_config=site_cfg,
                pool=pool,
                notify=False,
            )
        # target_length is the 5th positional arg ($5) to the INSERT.
        insert_call = pool._conn.execute.await_args_list[0]
        assert insert_call.args[5] == 2750

    @pytest.mark.asyncio
    async def test_none_length_is_filled_from_picker(self):
        site_cfg = _make_site_config({})
        pool = _make_pool()
        with patch(
            "services.topic_proposal_service.pause_at_gate",
            AsyncMock(),
        ):
            # Force the shared picker to a deterministic value so the test
            # asserts the wiring, not the RNG.
            with patch(
                "services.topic_length.pick_target_length",
                return_value=637,
            ):
                await propose_topic(
                    topic="No explicit length topic",
                    site_config=site_cfg,
                    pool=pool,
                    notify=False,
                )
        insert_call = pool._conn.execute.await_args_list[0]
        assert insert_call.args[5] == 637

    @pytest.mark.asyncio
    async def test_none_length_lands_in_a_default_bucket(self):
        from services.topic_length import DEFAULT_LENGTH_WEIGHTS

        site_cfg = _make_site_config({})
        pool = _make_pool()
        with patch(
            "services.topic_proposal_service.pause_at_gate",
            AsyncMock(),
        ):
            await propose_topic(
                topic="Bucketed length topic",
                site_config=site_cfg,
                pool=pool,
                notify=False,
            )
        insert_call = pool._conn.execute.await_args_list[0]
        picked = insert_call.args[5]
        assert any(
            lo <= picked <= hi for lo, hi, _w in DEFAULT_LENGTH_WEIGHTS
        )


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
