"""Regression: content.evaluate_auto_publish finalizes the terminal status.

The ``preview_gate`` component-scoped regen gate (canonical_blog graph_def) sits
AFTER the finalize block:

    ... persist_task -> record_pipeline_version -> preview_gate
        -> evaluate_auto_publish -> END

``content.persist_task`` sets ``status='awaiting_approval'``, but the
``preview_gate`` interrupt overrides it to ``'awaiting_gate'``
(``services/approval_service.py::pause_at_gate``) and the operator approve cycle
leaves it at ``'in_progress'`` before resuming the graph
(``approval_service.approve``). The CLI/MCP resume path does NOT run
``post_pipeline_actions``, so on approve-resume the graph ran
``preview_gate(passthrough) -> evaluate_auto_publish -> END`` and left the row at
``status='in_progress'`` (gate=NULL). The stale-inprogress sweep would then
reset that to ``'pending'`` and silently re-run an already-approved post
(observed live 2026-06-22).

Fix: ``evaluate_auto_publish`` is the graph's terminal node, so it re-asserts
``'awaiting_approval'`` itself — making the graph authoritative about its own end
state regardless of which caller (CLI resume / MCP approve / forward Prefect
flow) drove it there. Guarded so an already-published / already-rejected task is
never reverted.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


class _StatusTrackingDb:
    """Minimal DatabaseService stand-in modelling ``pipeline_tasks.status`` and
    the ``update_task_status_guarded`` delegate's real semantics: returns the
    previous status on success, ``None`` when the current status is not in
    ``allowed_from`` (services/tasks_db.py::update_task_status_guarded)."""

    def __init__(self, status: str) -> None:
        self.status = status
        self.pool = MagicMock()
        self.guarded_calls: list[dict] = []

    async def update_task_status_guarded(
        self,
        *,
        task_id,
        new_status,
        allowed_from=("in_progress", "pending"),
        **fields,
    ):
        self.guarded_calls.append(
            {
                "task_id": task_id,
                "new_status": new_status,
                "allowed_from": tuple(allowed_from),
            }
        )
        if self.status not in allowed_from:
            return None
        prev = self.status
        self.status = new_status
        return prev


@pytest.fixture(autouse=True)
def _stub_gate(monkeypatch):
    """Stub the auto-publish gate so the observe-only branch is a quiet no-op —
    these tests assert on terminal-status finalization, not the gate decision."""

    async def _fake_evaluate(pool, **kwargs):
        return SimpleNamespace(
            would_fire=False,
            dry_run=True,
            gate_state="disabled",
            reason="stub",
            quality_score=0.0,
            threshold=-1.0,
            trailing_clean_runs=0,
            required_clean_runs=3,
        )

    monkeypatch.setattr(
        "modules.content.auto_publish_gate.evaluate", _fake_evaluate
    )


@pytest.mark.asyncio
async def test_approve_resume_finalizes_awaiting_approval():
    """The preview_gate approve-resume path lands the terminal node with
    status='in_progress'; it must flip the row to 'awaiting_approval' so the
    stale-inprogress sweep can't re-run the approved post."""
    from modules.content.atoms.content_evaluate_auto_publish import run

    db = _StatusTrackingDb(status="in_progress")
    state = {"task_id": "t-approve-resume", "database_service": db}

    await run(state)

    assert db.status == "awaiting_approval", (
        "evaluate_auto_publish left status at in_progress on approve-resume — "
        "the stale-inprogress sweep would reset it to pending and re-run the "
        "already-approved post"
    )
    assert db.guarded_calls, "terminal node did not attempt a status finalize"
    call = db.guarded_calls[-1]
    assert call["new_status"] == "awaiting_approval"
    assert "in_progress" in call["allowed_from"]


@pytest.mark.asyncio
async def test_forward_path_already_awaiting_is_idempotent():
    """On the forward path persist_task already set awaiting_approval; the
    terminal node's re-assert must be a benign guarded no-op (never raises,
    never clobbers)."""
    from modules.content.atoms.content_evaluate_auto_publish import run

    db = _StatusTrackingDb(status="awaiting_approval")
    state = {"task_id": "t-forward", "database_service": db}

    await run(state)  # must not raise

    assert db.status == "awaiting_approval"


@pytest.mark.asyncio
async def test_does_not_revert_a_published_task():
    """Defense-in-depth: a task the forward path already auto-published must
    never be reverted to awaiting_approval by a re-run of the terminal node.
    Constrains the fix to a guarded write (a naive update_task would clobber)."""
    from modules.content.atoms.content_evaluate_auto_publish import run

    db = _StatusTrackingDb(status="published")
    state = {"task_id": "t-published", "database_service": db}

    await run(state)

    assert db.status == "published"


@pytest.mark.asyncio
async def test_missing_database_service_is_a_noop():
    """No database_service in state → atom returns {} and never touches status
    (preserves the existing early-return contract)."""
    from modules.content.atoms.content_evaluate_auto_publish import run

    out = await run({"task_id": "t-no-db"})

    assert out == {}
