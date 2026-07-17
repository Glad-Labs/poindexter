"""Unit tests — template_runner best-effort-failure visibility.

Silent-excepts OLD-debt burn-down (batch 6). template_runner is the PRIMARY
pipeline path (the LangGraph orchestrator every post runs through). Two of its
five flagged handlers are the per-run outcome-telemetry writes at finalize;
they now emit a non-paging ``info`` finding when they fail, so a silently-broken
router-feedback loop becomes visible instead of degrading in the dark:

* ``_record_capability_outcomes`` — the (atom, tier, model) scoring substrate
  the model router learns from. A persistent silent failure means the router
  stops learning with no signal.
* ``_capture_atom_runs`` — the per-invocation composition→outcome capture.

Both were lifted out of ``run()`` for testability (mirrors writer_core's
``_snapshot_initial_draft``). The other three flagged handlers stay
``# silent-ok``: the on_event callback contract (#361 — a caller's observer must
never break the run), the routine progress-streaming notify (the Discord "spam"
channel), and the per-node stage/heartbeat stamp (consistent with the already
silent-ok'd ``_mark_progress`` — the brain probe has a duration-threshold
fallback and a finding would spam once per node).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import services.template_runner as tr

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


async def test_capability_outcomes_write_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(
        "services.capability_outcomes.record_run",
        AsyncMock(side_effect=RuntimeError("capability_outcomes insert boom")),
    )

    # Must not raise — telemetry never fails the pipeline.
    await tr._record_capability_outcomes(
        pool=None,
        ok=True,
        template_slug="canonical_blog",
        halted_at=None,
        records=[],
        final_state={},
        initial_state={"task_id": "task-1"},
    )

    findings = [c for c in calls if c["kind"] == "capability_outcomes_write_failed"]
    assert len(findings) == 1
    assert findings[0]["dedup_key"] == "capability_outcomes_write_failed"
    assert findings[0].get("severity", "info") == "info"


async def test_capability_outcomes_success_emits_no_finding(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(
        "services.capability_outcomes.record_run", AsyncMock(return_value=3),
    )
    await tr._record_capability_outcomes(
        pool=None, ok=True, template_slug="canonical_blog", halted_at=None,
        records=[], final_state={}, initial_state={"task_id": "task-1"},
    )
    assert calls == []


async def test_atom_runs_capture_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(
        "services.atom_runs.persist_atom_runs",
        AsyncMock(side_effect=RuntimeError("atom_runs insert boom")),
    )

    await tr._capture_atom_runs(
        pool=None,
        run_id="thread-1",
        task_id="task-1",
        template_slug="canonical_blog",
        records=[],
        site_config=None,
    )

    findings = [c for c in calls if c["kind"] == "atom_runs_capture_failed"]
    assert len(findings) == 1
    assert findings[0]["dedup_key"] == "atom_runs_capture_failed"
    assert findings[0].get("severity", "info") == "info"


async def test_atom_runs_capture_success_emits_no_finding(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(
        "services.atom_runs.persist_atom_runs", AsyncMock(return_value=5),
    )
    await tr._capture_atom_runs(
        pool=None, run_id="thread-1", task_id="task-1",
        template_slug="canonical_blog", records=[], site_config=None,
    )
    assert calls == []
