"""Unit tests — auto_publish_gate atom_runs-backfill failure visibility.

Silent-excepts OLD-debt burn-down (batch 11). ``record_post_approve_metrics``
runs on the approve/publish path and, after writing the primary
``published_post_edit_metrics`` row, backfills the ``"approved"`` outcome (+
operator edit distance) onto every ``atom_runs`` row for the task. That backfill
was swallowed at DEBUG only (invisible in prod, INFO+), so a persistent break
would silently starve the ``atom_runs`` (composition -> outcome) learning signal
the router scores on — the same class as the batch-10 admin_db gap.

Lifted into ``_backfill_atom_run_outcome`` purely for this testability (batch-10
``_mirror_*`` precedent); it now emits a non-paging ``info`` finding on failure
and still never raises (a learning-signal failure must never fail publish).

The two difflib edit-distance refinements in the same function stay
``# silent-ok``: each refines a crude fallback already computed
(``char_diff``/``line_diff`` via ``abs(len(...))``), and stdlib difflib over
post-sized strings cannot realistically raise — so they need no test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import modules.content.auto_publish_gate as apg

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


async def test_backfill_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(
        "services.atom_runs.record_atom_run_outcome",
        AsyncMock(side_effect=RuntimeError("atom_runs backfill boom")),
    )

    # Must not raise — the backfill is additive; publish must never fail on it.
    await apg._backfill_atom_run_outcome(object(), task_id="task-1", edit_distance=42)

    findings = [c for c in calls if c["kind"] == "atom_runs_outcome_backfill_failed"]
    assert len(findings) == 1
    assert findings[0].get("severity") == "info"
    assert "atom_runs_outcome_backfill_failed" in findings[0]["dedup_key"]


async def test_backfill_success_emits_no_finding(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(
        "services.atom_runs.record_atom_run_outcome",
        AsyncMock(return_value=None),
    )

    await apg._backfill_atom_run_outcome(object(), task_id="task-1", edit_distance=42)

    assert calls == []
