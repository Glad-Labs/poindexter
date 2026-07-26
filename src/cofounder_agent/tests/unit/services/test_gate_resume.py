"""Unit tests for ``services/gate_resume.py`` — HTTP-side approve-and-resume.

The service mirrors the CLI's resume-atomicity contract: approve first
(synchronous), resume in the background, roll the approval back when the
resume raises so the task honestly reappears in the gate queue. All heavy
seams (TemplateRunner, approval_service, notify) are patched — no DB.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from services import gate_resume as mod
from services.approval_service import (
    ApprovalServiceError,
    TaskNotFoundError,
    TaskNotPausedError,
)

PAUSED_AT = datetime(2026, 7, 17, 8, 32, 43, tzinfo=timezone.utc)


class _FakeConn:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetchrow(self, sql: str, *args):
        return self._row


class _FakePool:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def acquire(self):
        return _FakeConn(self._row)


def _db_service(row: dict[str, Any] | None) -> Any:
    return SimpleNamespace(pool=_FakePool(row), database_url="postgresql://fake/db")


def _paused_row(task_id: str = "t-1") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "status": "awaiting_gate",
        "awaiting_gate": "seo_refresh_gate",
        "gate_artifact": '{"title": "T"}',
        "gate_paused_at": PAUSED_AT,
        "topic": "some-slug",
        "template_slug": "seo_refresh",
    }


class _SiteConfig:
    def get_float(self, key: str, default: float) -> float:
        return default


class _CollectSpawn:
    """Spawn stub that captures the coroutine instead of scheduling it."""

    def __init__(self) -> None:
        self.coros: list[Any] = []

    def __call__(self, coro):
        self.coros.append(coro)
        return coro


@pytest.fixture(autouse=True)
def _clean_inflight():
    mod._RESUMING.clear()
    yield
    mod._RESUMING.clear()


@pytest.fixture
def patched_seams(monkeypatch):
    approve = AsyncMock(
        return_value={"ok": True, "gate_history_id": 42, "gate_name": "seo_refresh_gate"}
    )
    rollback = AsyncMock(return_value={"ok": True, "deleted_row": True})
    audit = []
    notify = AsyncMock()
    monkeypatch.setattr(mod, "approve_service", approve)
    monkeypatch.setattr(mod, "rollback_resume_approval", rollback)
    monkeypatch.setattr(mod, "audit_log_bg", lambda **kw: audit.append(kw))
    monkeypatch.setattr(mod, "_notify", notify)
    return SimpleNamespace(approve=approve, rollback=rollback, audit=audit, notify=notify)


@pytest.mark.asyncio
class TestValidation:
    async def test_unknown_task_raises_not_found(self, patched_seams):
        with pytest.raises(TaskNotFoundError):
            await mod.approve_and_schedule_resume(
                task_id="nope", feedback=None, actor="human",
                db_service=_db_service(None), site_config=_SiteConfig(),
            )

    async def test_not_paused_raises(self, patched_seams):
        row = _paused_row()
        row["awaiting_gate"] = None
        row["status"] = "in_progress"
        with pytest.raises(TaskNotPausedError):
            await mod.approve_and_schedule_resume(
                task_id="t-1", feedback=None, actor="human",
                db_service=_db_service(row), site_config=_SiteConfig(),
            )

    async def test_missing_template_slug_raises(self, patched_seams):
        row = _paused_row()
        row["template_slug"] = None
        with pytest.raises(ApprovalServiceError):
            await mod.approve_and_schedule_resume(
                task_id="t-1", feedback=None, actor="human",
                db_service=_db_service(row), site_config=_SiteConfig(),
            )

    async def test_inflight_guard_refuses_second_approve(self, patched_seams):
        mod._RESUMING.add("t-1")
        with pytest.raises(mod.ResumeInFlightError):
            await mod.approve_and_schedule_resume(
                task_id="t-1", feedback=None, actor="human",
                db_service=_db_service(_paused_row()), site_config=_SiteConfig(),
            )
        # The guard path must not have recorded an approval.
        patched_seams.approve.assert_not_awaited()


@pytest.mark.asyncio
class TestApproveAndResume:
    async def test_success_approves_then_resumes(self, patched_seams, monkeypatch):
        run_resume = AsyncMock(return_value=SimpleNamespace(ok=True, halted_at=None))
        monkeypatch.setattr(mod, "_run_resume", run_resume)
        spawn = _CollectSpawn()

        result = await mod.approve_and_schedule_resume(
            task_id="t-1", feedback="ship it", actor="human",
            db_service=_db_service(_paused_row()), site_config=_SiteConfig(),
            spawn=spawn,
        )

        assert result["ok"] is True
        assert result["mode"] == "approve_resume_started"
        assert result["gate_name"] == "seo_refresh_gate"
        patched_seams.approve.assert_awaited_once()
        approve_kwargs = patched_seams.approve.await_args.kwargs
        assert approve_kwargs["task_id"] == "t-1"
        assert approve_kwargs["gate_name"] == "seo_refresh_gate"
        assert approve_kwargs["feedback"] == "ship it"
        # The resume is scheduled, in-flight is reserved, and awaiting the
        # captured coroutine completes it and releases the slot.
        assert "t-1" in mod._RESUMING
        assert len(spawn.coros) == 1
        await spawn.coros[0]
        assert "t-1" not in mod._RESUMING
        run_kwargs = run_resume.await_args.kwargs
        assert run_kwargs["template_slug"] == "seo_refresh"
        assert run_kwargs["gate_name"] == "seo_refresh_gate"
        assert any(
            a["event_type"] == "gate_resume_completed" for a in patched_seams.audit
        )
        patched_seams.rollback.assert_not_awaited()

    async def test_resume_failure_rolls_approval_back(self, patched_seams, monkeypatch):
        run_resume = AsyncMock(side_effect=RuntimeError("checkpointer down"))
        monkeypatch.setattr(mod, "_run_resume", run_resume)
        spawn = _CollectSpawn()

        await mod.approve_and_schedule_resume(
            task_id="t-1", feedback=None, actor="human",
            db_service=_db_service(_paused_row()), site_config=_SiteConfig(),
            spawn=spawn,
        )
        await spawn.coros[0]

        patched_seams.rollback.assert_awaited_once()
        rb = patched_seams.rollback.await_args.kwargs
        # Compensation targets the exact approval row + the pre-approve pause.
        assert rb["gate_history_id"] == 42
        assert rb["artifact"] == '{"title": "T"}'
        assert rb["paused_at"] == PAUSED_AT
        assert any(
            a["event_type"] == "gate_resume_failed" and a["severity"] == "error"
            for a in patched_seams.audit
        )
        patched_seams.notify.assert_awaited()
        assert "t-1" not in mod._RESUMING

    async def test_halted_resume_keeps_approval_and_notifies(
        self, patched_seams, monkeypatch,
    ):
        run_resume = AsyncMock(
            return_value=SimpleNamespace(ok=False, halted_at="republish")
        )
        monkeypatch.setattr(mod, "_run_resume", run_resume)
        spawn = _CollectSpawn()

        await mod.approve_and_schedule_resume(
            task_id="t-1", feedback=None, actor="human",
            db_service=_db_service(_paused_row()), site_config=_SiteConfig(),
            spawn=spawn,
        )
        await spawn.coros[0]

        # The gate was durably passed — a downstream halt is NOT rolled back.
        patched_seams.rollback.assert_not_awaited()
        halted = [
            a for a in patched_seams.audit if a["event_type"] == "gate_resume_halted"
        ]
        assert halted and halted[0]["details"]["halted_at"] == "republish"
        patched_seams.notify.assert_awaited()

    async def test_rollback_failure_still_releases_inflight(
        self, patched_seams, monkeypatch,
    ):
        monkeypatch.setattr(
            mod, "_run_resume", AsyncMock(side_effect=RuntimeError("boom"))
        )
        patched_seams.rollback.side_effect = RuntimeError("db gone")
        spawn = _CollectSpawn()

        await mod.approve_and_schedule_resume(
            task_id="t-1", feedback=None, actor="human",
            db_service=_db_service(_paused_row()), site_config=_SiteConfig(),
            spawn=spawn,
        )
        await spawn.coros[0]

        assert "t-1" not in mod._RESUMING
        failed = [
            a for a in patched_seams.audit if a["event_type"] == "gate_resume_failed"
        ]
        assert failed and failed[0]["details"]["rolled_back"] is False

    async def test_spawn_failure_releases_inflight_and_closes_coro(
        self, patched_seams, monkeypatch,
    ):
        monkeypatch.setattr(mod, "_run_resume", AsyncMock())

        def _bad_spawn(coro):
            raise RuntimeError("loop shutting down")

        with pytest.raises(RuntimeError):
            await mod.approve_and_schedule_resume(
                task_id="t-1", feedback=None, actor="human",
                db_service=_db_service(_paused_row()), site_config=_SiteConfig(),
                spawn=_bad_spawn,
            )
        assert "t-1" not in mod._RESUMING
