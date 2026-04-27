"""Unit tests for ``services/stages/approval_gate.py`` (#145).

Covers:

- Gate disabled → passthrough (StageResult ok=True, continue_workflow=True).
- ``skip_if_setting`` truthy → passthrough.
- Gate enabled → halt with continue_workflow=False, persists artifact,
  fires the notify path.
- Missing config / context surfaces as a Stage-level failure.

External surfaces (DB pool, notify) are mocked — no live Postgres /
Telegram calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.stage import Stage, StageResult
from services.stages.approval_gate import ApprovalGateStage


def _make_site_config(values: dict[str, str] | None = None) -> Any:
    """Lightweight site_config stand-in. Real SiteConfig.get is sync and
    reads from an in-memory cache; matching that interface is enough."""
    cache = dict(values or {})
    return SimpleNamespace(
        get=lambda key, default=None: cache.get(key, default),
        get_int=lambda key, default=0: int(cache.get(key, default) or default),
        get_bool=lambda key, default=False: str(
            cache.get(key, default)
        ).lower() in ("true", "on", "1", "yes"),
    )


def _make_context(
    *,
    task_id: str = "t-123",
    site_config: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pool = MagicMock()
    db_service = SimpleNamespace(pool=pool)
    ctx: dict[str, Any] = {
        "task_id": task_id,
        "site_config": site_config or _make_site_config(),
        "database_service": db_service,
        "topic": "Sample topic",
        "title": "Sample title",
    }
    if extra:
        ctx.update(extra)
    return ctx


class TestProtocol:
    def test_conforms_to_stage(self):
        assert isinstance(ApprovalGateStage(), Stage)

    def test_metadata(self):
        s = ApprovalGateStage()
        assert s.name == "approval_gate"
        assert s.halts_on_failure is True
        assert s.timeout_seconds == 30


@pytest.mark.asyncio
class TestExecute:
    async def test_missing_gate_name_in_config_is_failure(self):
        ctx = _make_context()
        result = await ApprovalGateStage().execute(ctx, {})
        assert isinstance(result, StageResult)
        assert result.ok is False
        assert "gate_name" in result.detail
        assert result.continue_workflow is False

    async def test_missing_task_id_is_failure(self):
        ctx = _make_context(task_id="")  # falsy
        ctx.pop("task_id", None)
        result = await ApprovalGateStage().execute(
            ctx, {"gate_name": "topic_decision"},
        )
        assert result.ok is False
        assert "task_id" in result.detail

    async def test_gate_disabled_passthrough(self):
        # Default-off — no pipeline_gate_topic_decision setting present.
        site_cfg = _make_site_config({})
        ctx = _make_context(site_config=site_cfg)
        # No pause_at_gate import-side-effect should occur.
        with patch(
            "services.stages.approval_gate.pause_at_gate",
            AsyncMock(),
        ) as mock_pause:
            result = await ApprovalGateStage().execute(
                ctx, {"gate_name": "topic_decision"},
            )
        assert result.ok is True
        assert result.continue_workflow is True
        assert "disabled" in result.detail
        assert mock_pause.await_count == 0
        assert result.metrics["skipped"] is True

    async def test_skip_if_setting_truthy_passthrough(self):
        site_cfg = _make_site_config({
            "pipeline_gate_topic_decision": "on",
            "automated_test_mode": "true",
        })
        ctx = _make_context(site_config=site_cfg)
        with patch(
            "services.stages.approval_gate.pause_at_gate",
            AsyncMock(),
        ) as mock_pause:
            result = await ApprovalGateStage().execute(
                ctx, {
                    "gate_name": "topic_decision",
                    "skip_if_setting": "automated_test_mode",
                },
            )
        assert result.ok is True
        assert result.continue_workflow is True
        assert "skip_if_setting" in result.detail
        assert mock_pause.await_count == 0
        assert result.metrics["skipped"] is True
        assert result.metrics["reason"].startswith("skip_if_setting")

    async def test_gate_enabled_halts_and_persists(self):
        site_cfg = _make_site_config({
            "pipeline_gate_topic_decision": "on",
        })
        ctx = _make_context(site_config=site_cfg)
        captured: dict[str, Any] = {}

        async def _fake_pause(**kwargs):
            captured.update(kwargs)
            return {
                "ok": True,
                "task_id": kwargs["task_id"],
                "gate_name": kwargs["gate_name"],
                "paused_at": "2026-04-26T12:00:00+00:00",
                "notify": {"sent": True, "reason": "ok"},
            }

        with patch(
            "services.stages.approval_gate.pause_at_gate",
            AsyncMock(side_effect=_fake_pause),
        ):
            result = await ApprovalGateStage().execute(
                ctx, {
                    "gate_name": "topic_decision",
                    "artifact_fn": lambda c: {"topic": c.get("topic", "")},
                },
            )

        assert result.ok is True
        assert result.continue_workflow is False
        assert "topic_decision" in result.detail
        # Artifact passed through to pause_at_gate.
        assert captured["gate_name"] == "topic_decision"
        assert captured["artifact"] == {"topic": "Sample topic"}
        # Context updates surface the gate state for downstream observers.
        assert result.context_updates["awaiting_gate"] == "topic_decision"
        assert result.context_updates["gate_artifact"] == {"topic": "Sample topic"}
        assert result.metrics["gate_name"] == "topic_decision"
        assert result.metrics["skipped"] is False
        assert result.metrics["notify_sent"] is True

    async def test_default_artifact_when_no_artifact_fn(self):
        site_cfg = _make_site_config({"pipeline_gate_topic_decision": "on"})
        ctx = _make_context(site_config=site_cfg)
        captured: dict[str, Any] = {}

        async def _fake_pause(**kwargs):
            captured.update(kwargs)
            return {"ok": True, "paused_at": "x", "notify": {"sent": False}}

        with patch(
            "services.stages.approval_gate.pause_at_gate",
            AsyncMock(side_effect=_fake_pause),
        ):
            result = await ApprovalGateStage().execute(
                ctx, {"gate_name": "topic_decision"},
            )
        assert result.ok is True
        # Default artifact carries enough context for the operator.
        assert captured["artifact"]["task_id"] == "t-123"
        assert captured["artifact"]["topic"] == "Sample topic"
        assert captured["artifact"]["title"] == "Sample title"

    async def test_artifact_fn_exception_surfaces_as_stage_failure(self):
        site_cfg = _make_site_config({"pipeline_gate_topic_decision": "on"})
        ctx = _make_context(site_config=site_cfg)

        def _broken(_ctx):
            raise RuntimeError("artifact_fn boom")

        with patch(
            "services.stages.approval_gate.pause_at_gate",
            AsyncMock(),
        ) as mock_pause:
            result = await ApprovalGateStage().execute(
                ctx,
                {"gate_name": "topic_decision", "artifact_fn": _broken},
            )
        assert result.ok is False
        assert "artifact_fn" in result.detail
        assert result.continue_workflow is False
        assert mock_pause.await_count == 0  # never called past artifact_fn

    async def test_pause_at_gate_failure_halts_workflow(self):
        site_cfg = _make_site_config({"pipeline_gate_topic_decision": "on"})
        ctx = _make_context(site_config=site_cfg)

        with patch(
            "services.stages.approval_gate.pause_at_gate",
            AsyncMock(side_effect=RuntimeError("DB exploded")),
        ):
            result = await ApprovalGateStage().execute(
                ctx, {"gate_name": "topic_decision"},
            )
        assert result.ok is False
        assert "pause_at_gate failed" in result.detail
        assert result.continue_workflow is False

    async def test_no_pool_on_context_surfaces_as_failure(self):
        site_cfg = _make_site_config({"pipeline_gate_topic_decision": "on"})
        ctx = _make_context(site_config=site_cfg)
        # database_service.pool is None.
        ctx["database_service"] = SimpleNamespace(pool=None)
        result = await ApprovalGateStage().execute(
            ctx, {"gate_name": "topic_decision"},
        )
        assert result.ok is False
        assert "DB pool" in result.detail or "pool" in result.detail
