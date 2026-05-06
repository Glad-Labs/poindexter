"""Unit tests for services/firefighter_service.py.

Covers Glad-Labs/poindexter#347 step 2's checklist:

- ``build_triage_context`` returns the expected shape against a mocked
  pool with realistic rows
- ``build_triage_context`` handles a missing ``task_id`` label cleanly
  (no KeyError, ``pipeline_task`` simply absent from the bundle)
- ``build_triage_context`` truncates context when the assembled bundle
  exceeds ``ops_triage_max_context_tokens``
- ``run_triage`` calls the (mocked) model_router with the configured
  ``model_class``
- ``run_triage`` truncates the LLM output to
  ``ops_triage_max_diagnosis_tokens``
- ``run_triage`` returns ``diagnosis=""`` (does not raise) when the
  LLM returns empty
- ``_default_system_prompt`` falls back to the seeded default when the
  setting is missing
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.firefighter_service import (
    _FALLBACK_SYSTEM_PROMPT,
    _default_system_prompt,
    build_triage_context,
    run_triage,
)
from services.site_config import SiteConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    alert_row=None,
    history_rows=None,
    audit_rows=None,
    task_row=None,
    settings_rows=None,
):
    """Build a mocked asyncpg-style Pool whose ``acquire()`` yields a
    connection wired to the supplied query results.

    Routing is by SQL substring so callers can supply only the tables
    they care about; everything else returns an empty result.
    """
    conn = MagicMock()

    async def _fetchrow(sql, *args):
        upper = sql.upper()
        if "FROM ALERT_EVENTS" in upper and "WHERE ID =" in upper:
            return alert_row
        if "FROM PIPELINE_TASKS" in upper:
            return task_row
        return None

    async def _fetch(sql, *args):
        upper = sql.upper()
        if "FROM ALERT_EVENTS" in upper and "WHERE ALERTNAME" in upper:
            return list(history_rows or [])
        if "FROM AUDIT_LOG" in upper:
            return list(audit_rows or [])
        if "FROM APP_SETTINGS" in upper:
            return list(settings_rows or [])
        return []

    conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    conn.fetch = AsyncMock(side_effect=_fetch)
    conn.execute = AsyncMock(return_value="OK")

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


def _alert_row(*, alertname="probe_public_site_failed", labels=None, alert_id=42):
    return {
        "id": alert_id,
        "alertname": alertname,
        "status": "firing",
        "severity": "critical",
        "category": "probe",
        "labels": labels if labels is not None else {"severity": "critical"},
        "annotations": {"summary": "API unreachable"},
        "starts_at": datetime(2026, 5, 6, 4, 0, tzinfo=timezone.utc),
        "ends_at": None,
        "fingerprint": "abc123",
        "received_at": datetime(2026, 5, 6, 4, 0, 5, tzinfo=timezone.utc),
    }


def _audit_row(idx: int):
    return {
        "id": idx,
        "timestamp": datetime(2026, 5, 6, 3, 0, idx, tzinfo=timezone.utc),
        "event_type": f"event_{idx}",
        "source": "content_router",
        "task_id": f"task-{idx}",
        "details": {"note": f"row {idx}"},
        "severity": "info",
    }


# ---------------------------------------------------------------------------
# build_triage_context
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildTriageContext:
    @pytest.mark.asyncio
    async def test_returns_expected_shape(self):
        alert = _alert_row()
        history = [
            {
                "id": 40,
                "alertname": "probe_public_site_failed",
                "status": "firing",
                "severity": "critical",
                "labels": {"severity": "critical"},
                "annotations": {},
                "received_at": datetime(2026, 5, 5, tzinfo=timezone.utc),
            }
        ]
        audit = [_audit_row(i) for i in range(10)]
        settings = [
            {"key": "probe_public_site_url", "value": "https://gladlabs.io"},
            {"key": "probe_timeout_seconds", "value": "10"},
        ]
        pool, _ = _make_pool(
            alert_row=alert,
            history_rows=history,
            audit_rows=audit,
            settings_rows=settings,
        )
        cfg = SiteConfig(initial_config={"ops_triage_max_context_tokens": "100000"})

        bundle = await build_triage_context(pool, 42, cfg)

        assert bundle["alert"]["id"] == 42
        assert bundle["alert"]["alertname"] == "probe_public_site_failed"
        assert len(bundle["history"]) == 1
        assert bundle["history"][0]["id"] == 40
        assert len(bundle["audit_log"]) == 10
        assert bundle["app_settings"] == {
            "probe_public_site_url": "https://gladlabs.io",
            "probe_timeout_seconds": "10",
        }
        # No task_id label -> no pipeline_task key
        assert "pipeline_task" not in bundle

    @pytest.mark.asyncio
    async def test_includes_pipeline_task_when_label_present(self):
        alert = _alert_row(labels={"task_id": "task-xyz", "severity": "warn"})
        task_row = {
            "id": 1,
            "task_id": "task-xyz",
            "task_type": "blog_post",
            "topic": "Test",
            "status": "running",
            "stage": "draft",
            "percentage": 50,
            "message": None,
            "model_used": "ollama/glm-4.7-5090",
            "error_message": None,
            "created_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
            "started_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
            "completed_at": None,
        }
        pool, conn = _make_pool(alert_row=alert, task_row=task_row)
        cfg = SiteConfig(initial_config={"ops_triage_max_context_tokens": "100000"})

        bundle = await build_triage_context(pool, 42, cfg)

        assert bundle["pipeline_task"]["task_id"] == "task-xyz"
        # Verify the pipeline_tasks query actually ran with the right arg
        pipeline_calls = [
            c for c in conn.fetchrow.call_args_list
            if "pipeline_tasks" in c.args[0].lower()
        ]
        assert len(pipeline_calls) == 1
        assert pipeline_calls[0].args[1] == "task-xyz"

    @pytest.mark.asyncio
    async def test_handles_missing_task_id_cleanly(self):
        # No task_id key at all in labels — must NOT raise KeyError.
        alert = _alert_row(labels={"severity": "warn", "category": "probe"})
        pool, conn = _make_pool(alert_row=alert)
        cfg = SiteConfig(initial_config={"ops_triage_max_context_tokens": "100000"})

        bundle = await build_triage_context(pool, 42, cfg)

        assert "pipeline_task" not in bundle
        # And we should NOT have hit the pipeline_tasks SQL at all.
        pipeline_calls = [
            c for c in conn.fetchrow.call_args_list
            if "pipeline_tasks" in c.args[0].lower()
        ]
        assert pipeline_calls == []

    @pytest.mark.asyncio
    async def test_handles_labels_serialized_as_json_string(self):
        # asyncpg without the JSONB codec returns labels as a JSON string;
        # _coerce_labels must handle both shapes.
        alert = _alert_row(labels=json.dumps({"task_id": "t-99"}))
        task_row = {"task_id": "t-99", "stage": "draft"}
        pool, _ = _make_pool(alert_row=alert, task_row=task_row)
        cfg = SiteConfig(initial_config={"ops_triage_max_context_tokens": "100000"})

        bundle = await build_triage_context(pool, 42, cfg)

        assert bundle["pipeline_task"]["task_id"] == "t-99"

    @pytest.mark.asyncio
    async def test_truncates_audit_log_when_over_budget(self):
        alert = _alert_row()
        # 10 audit rows; bundle will exceed a tight token budget.
        audit = [_audit_row(i) for i in range(10)]
        pool, _ = _make_pool(alert_row=alert, audit_rows=audit)
        # ~100 tokens budget — much smaller than the natural bundle size
        cfg = SiteConfig(initial_config={"ops_triage_max_context_tokens": "100"})

        bundle = await build_triage_context(pool, 42, cfg)

        # Some audit rows must have been dropped to fit the budget.
        assert len(bundle["audit_log"]) < 10
        # The newest rows are at the head (DESC order) and survive
        # truncation; oldest (tail) get popped first.
        if bundle["audit_log"]:
            assert bundle["audit_log"][0]["id"] == 0  # newest preserved

    @pytest.mark.asyncio
    async def test_handles_missing_alert_row(self):
        # Defensive: caller passed a non-existent alert_event_id; we
        # should still return a bundle with an empty alert dict and
        # not crash on label extraction.
        pool, _ = _make_pool(alert_row=None)
        cfg = SiteConfig(initial_config={"ops_triage_max_context_tokens": "100000"})

        bundle = await build_triage_context(pool, 99, cfg)

        assert bundle["alert"] == {}
        assert bundle["history"] == []
        assert "pipeline_task" not in bundle


# ---------------------------------------------------------------------------
# run_triage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunTriage:
    @pytest.mark.asyncio
    async def test_calls_router_with_configured_model_class(self):
        cfg = SiteConfig(initial_config={
            "ops_triage_model_class": "custom_class",
            "ops_triage_max_diagnosis_tokens": "200",
        })
        router = MagicMock()
        router.invoke = AsyncMock(return_value={
            "text": "Likely a probe timeout.",
            "model": "ollama/glm-4.7-5090",
            "tokens": 25,
        })

        result = await run_triage({"alert": {"id": 1}}, cfg, router)

        router.invoke.assert_awaited_once()
        kwargs = router.invoke.call_args.kwargs
        assert kwargs["model_class"] == "custom_class"
        assert kwargs["max_tokens"] == 200
        assert "system" in kwargs
        assert "user" in kwargs
        # User payload is JSON-serialized context
        assert json.loads(kwargs["user"]) == {"alert": {"id": 1}}
        assert result["diagnosis"] == "Likely a probe timeout."
        assert result["model"] == "ollama/glm-4.7-5090"
        assert result["tokens"] == 25
        assert isinstance(result["ms"], int)
        assert result["ms"] >= 0

    @pytest.mark.asyncio
    async def test_default_model_class_when_unset(self):
        cfg = SiteConfig(initial_config={})
        router = MagicMock()
        router.invoke = AsyncMock(return_value={"text": "ok"})

        await run_triage({}, cfg, router)

        assert router.invoke.call_args.kwargs["model_class"] == "ops_triage"

    @pytest.mark.asyncio
    async def test_truncates_long_output(self):
        # 400 tokens * 4 chars/token = 1600 chars budget
        cfg = SiteConfig(initial_config={"ops_triage_max_diagnosis_tokens": "10"})
        # 10 tokens * 4 = 40 chars budget; produce 200 chars
        long_text = "x" * 200
        router = MagicMock()
        router.invoke = AsyncMock(return_value={"text": long_text})

        result = await run_triage({}, cfg, router)

        assert len(result["diagnosis"]) <= 40
        assert result["diagnosis"].endswith("[...]")

    @pytest.mark.asyncio
    async def test_short_output_not_truncated(self):
        cfg = SiteConfig(initial_config={"ops_triage_max_diagnosis_tokens": "100"})
        router = MagicMock()
        router.invoke = AsyncMock(return_value={"text": "Short answer."})

        result = await run_triage({}, cfg, router)

        assert result["diagnosis"] == "Short answer."
        assert "[...]" not in result["diagnosis"]

    @pytest.mark.asyncio
    async def test_empty_llm_output_returns_empty_diagnosis(self):
        cfg = SiteConfig(initial_config={})
        router = MagicMock()
        router.invoke = AsyncMock(return_value={"text": ""})

        result = await run_triage({"alert": {"id": 1}}, cfg, router)

        # Must NOT raise; must return empty string (not None).
        assert result["diagnosis"] == ""
        assert isinstance(result["diagnosis"], str)
        assert "ms" in result

    @pytest.mark.asyncio
    async def test_whitespace_only_output_treated_as_empty(self):
        cfg = SiteConfig(initial_config={})
        router = MagicMock()
        router.invoke = AsyncMock(return_value={"text": "   \n  "})

        result = await run_triage({}, cfg, router)

        assert result["diagnosis"] == ""

    @pytest.mark.asyncio
    async def test_router_raises_returns_empty_not_propagates(self):
        cfg = SiteConfig(initial_config={})
        router = MagicMock()
        router.invoke = AsyncMock(side_effect=RuntimeError("ollama down"))

        result = await run_triage({}, cfg, router)

        assert result["diagnosis"] == ""
        assert result["tokens"] == 0
        assert "ms" in result

    @pytest.mark.asyncio
    async def test_uses_configured_system_prompt(self):
        cfg = SiteConfig(initial_config={"ops_triage_system_prompt": "BE BRIEF."})
        router = MagicMock()
        router.invoke = AsyncMock(return_value={"text": "ok"})

        await run_triage({}, cfg, router)

        assert router.invoke.call_args.kwargs["system"] == "BE BRIEF."


# ---------------------------------------------------------------------------
# _default_system_prompt
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDefaultSystemPrompt:
    def test_falls_back_to_seeded_default_when_missing(self):
        cfg = SiteConfig(initial_config={})

        assert _default_system_prompt(cfg) == _FALLBACK_SYSTEM_PROMPT

    def test_falls_back_when_setting_is_empty_string(self):
        cfg = SiteConfig(initial_config={"ops_triage_system_prompt": ""})

        assert _default_system_prompt(cfg) == _FALLBACK_SYSTEM_PROMPT

    def test_falls_back_when_setting_is_whitespace(self):
        cfg = SiteConfig(initial_config={"ops_triage_system_prompt": "   \n  "})

        assert _default_system_prompt(cfg) == _FALLBACK_SYSTEM_PROMPT

    def test_returns_configured_value_when_present(self):
        cfg = SiteConfig(initial_config={"ops_triage_system_prompt": "Be terse."})

        assert _default_system_prompt(cfg) == "Be terse."

    def test_fallback_matches_spec_keywords(self):
        # Tripwire — if someone tweaks the wording, this test reminds
        # them to update the seed migration too (or vice versa).
        assert "Poindexter operator" in _FALLBACK_SYSTEM_PROMPT
        assert "ONE SHORT PARAGRAPH" in _FALLBACK_SYSTEM_PROMPT
        assert "Do NOT propose code" in _FALLBACK_SYSTEM_PROMPT
