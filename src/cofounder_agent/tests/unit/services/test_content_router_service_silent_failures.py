"""Unit tests — content_router_service experiment-outcome visibility.

Silent-excepts OLD-debt burn-down (batch 13). After a pipeline run completes,
``process_content_generation_task`` attributes the outcome to its
experiment-assignment row (Glad-Labs/poindexter#27). That attribution was
swallowed at DEBUG only (invisible in prod, INFO+), so a persistent break would
silently starve the A/B Lab's (variant -> outcome) data even though the run
itself succeeded — the router-learning class of batches 10-12.

Lifted into ``_record_experiment_outcome`` for testability; it now emits a
non-paging ``info`` finding on failure and still never raises (experiment
attribution must never poison a successful run).

The other two flagged handlers in the same method stay ``# silent-ok``: the
progress-streaming callback build (best-effort UX — a failure just means no live
progress) and the dry-run severity-demote check (fail-safe — on failure the
error is treated as a real error, the conservative direction).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import services.content_router_service as crs

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


_RESULT = {
    "experiment_assignment": {"variant": "A"},
    "quality_score": 4.0,
    "qa_final_score": 3.5,
    "status": "awaiting_approval",
    "model_used": "gemma-4-31B",
}


async def test_experiment_outcome_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(
        "services.pipeline_experiment_hook.record_pipeline_outcome",
        AsyncMock(side_effect=RuntimeError("record_outcome boom")),
    )

    # Must not raise — attribution is best-effort; the run already succeeded.
    await crs._record_experiment_outcome(
        result=_RESULT,
        task_id="task-1",
        database_service=MagicMock(),
        site_config=MagicMock(),
        ok=True,
    )

    findings = [c for c in calls if c["kind"] == "experiment_outcome_record_failed"]
    assert len(findings) == 1
    assert findings[0].get("severity") == "info"
    assert "experiment_outcome_record_failed" in findings[0]["dedup_key"]


async def test_experiment_outcome_success_emits_no_finding(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(
        "services.pipeline_experiment_hook.record_pipeline_outcome",
        AsyncMock(return_value=None),
    )

    await crs._record_experiment_outcome(
        result=_RESULT,
        task_id="task-1",
        database_service=MagicMock(),
        site_config=MagicMock(),
        ok=True,
    )

    assert calls == []
