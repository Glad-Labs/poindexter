"""Unit tests for atoms.set_task_status — the generic status-mutation atom.

Models update_task_status_guarded's real semantics (returns the previous
status on success, None when the current status is not in allowed_from) —
mirrors tests/unit/services/atoms/test_content_evaluate_auto_publish_finalize.py.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class _StatusTrackingDb:
    def __init__(self, status: str) -> None:
        self.status = status
        self.pool = MagicMock()
        self.guarded_calls: list[dict] = []

    async def update_task_status_guarded(
        self, *, task_id, new_status, allowed_from=("in_progress", "pending"), **fields
    ):
        self.guarded_calls.append(
            {
                "task_id": task_id,
                "new_status": new_status,
                "allowed_from": tuple(allowed_from),
                "fields": dict(fields),
            }
        )
        if self.status not in allowed_from:
            return None
        prev = self.status
        self.status = new_status
        return prev


@pytest.mark.asyncio
async def test_flips_in_progress_to_completed_with_percentage():
    from modules.content.atoms.set_task_status import run

    db = _StatusTrackingDb(status="in_progress")
    state = {
        "task_id": "job-1",
        "target_status": "completed",
        "percentage": 100,
        "database_service": db,
    }

    out = await run(state)

    assert out == {}
    assert db.status == "completed"
    call = db.guarded_calls[-1]
    assert call["new_status"] == "completed"
    assert call["allowed_from"] == ("in_progress",)
    assert call["fields"] == {"percentage": 100}


@pytest.mark.asyncio
async def test_target_status_is_config_driven_not_hardcoded():
    """Proves the status is a parameter: a graph can finalize to any valid
    status, not just 'completed'."""
    from modules.content.atoms.set_task_status import run

    db = _StatusTrackingDb(status="in_progress")
    state = {"task_id": "job-2", "target_status": "published", "database_service": db}

    await run(state)

    assert db.status == "published"


@pytest.mark.asyncio
async def test_already_terminal_is_a_benign_noop():
    """Guard returns None (current status not in allowed_from) → no raise,
    status unchanged. Makes a re-run idempotent."""
    from modules.content.atoms.set_task_status import run

    db = _StatusTrackingDb(status="completed")
    state = {"task_id": "job-3", "target_status": "completed", "database_service": db}

    out = await run(state)  # must not raise

    assert out == {}
    assert db.status == "completed"


@pytest.mark.asyncio
async def test_custom_allowed_from_from_config():
    from modules.content.atoms.set_task_status import run

    db = _StatusTrackingDb(status="awaiting_gate")
    state = {
        "task_id": "job-4",
        "target_status": "completed",
        "allowed_from": ["in_progress", "awaiting_gate"],
        "database_service": db,
    }

    await run(state)

    assert db.status == "completed"


@pytest.mark.asyncio
async def test_missing_target_status_fails_loud():
    from modules.content.atoms.set_task_status import run

    db = _StatusTrackingDb(status="in_progress")
    with pytest.raises(RuntimeError, match="target_status"):
        await run({"task_id": "job-5", "database_service": db})


@pytest.mark.asyncio
async def test_invalid_target_status_fails_loud():
    from modules.content.atoms.set_task_status import run

    db = _StatusTrackingDb(status="in_progress")
    with pytest.raises(RuntimeError, match="not a valid"):
        await run(
            {"task_id": "job-6", "target_status": "bogus", "database_service": db}
        )


@pytest.mark.asyncio
async def test_missing_guarded_method_is_nonfatal():
    """A degenerate db without the guarded method must not fail a completed
    graph — mirrors content.evaluate_auto_publish's terminal-node posture."""
    from modules.content.atoms.set_task_status import run

    out = await run(
        {"task_id": "job-7", "target_status": "completed", "database_service": object()}
    )

    assert out == {}


def test_valid_statuses_match_db_constraint():
    """Drift guard: the atom's _VALID_STATUSES must equal the DB
    pipeline_tasks_status_check CHECK constraint set."""
    import re
    from pathlib import Path

    import services
    from modules.content.atoms.set_task_status import _VALID_STATUSES

    schema = (
        Path(services.__file__).parent / "migrations" / "0000_baseline.schema.sql"
    ).read_text(encoding="utf-8")
    m = re.search(
        r"pipeline_tasks_status_check CHECK \(status IN \(([^)]*)\)\)", schema
    )
    assert m, "could not find pipeline_tasks_status_check in baseline schema"
    db_statuses = frozenset(s.strip().strip("'") for s in m.group(1).split(","))
    assert _VALID_STATUSES == db_statuses
