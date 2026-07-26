"""Lock-2 graduation for ``atoms.approval_gate`` (wired 2026-07-25).

Two layers under test:

1. ``services.approval_service.count_trailing_clean_approvals`` — the streak
   scan: trailing *human* approvals (distinct tasks) at a gate, broken by any
   ``rejected``/``dismissed`` row, with non-human approvals trust-neutral.
2. ``run()`` graduation branch — streak >= threshold auto-approves (writes the
   ``auto_approved`` history row, never pauses); every other path — setting
   absent/0/unparseable, streak short, streak-read failure, record failure —
   falls back to the normal pause, and pre-graduation pauses surface
   ``graduation_progress`` in the review artifact.

The seo_refresh graph opts in via node config
(``graduation_setting='seo.refresh.auto_publish_after_clean_runs'``); these
tests drive the atom directly with the same shape.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import modules.content.atoms.approval_gate as ag
from services.approval_service import count_trailing_clean_approvals
from tests.unit.services._gate_fakes import FakeConn, FakePool, executed_sql

pytestmark = pytest.mark.unit


def _row(task: str, kind: str, actor: str = "human") -> dict:
    return {"task_id": task, "event_kind": kind, "actor": actor}


# ---------------------------------------------------------------------------
# 1. count_trailing_clean_approvals
# ---------------------------------------------------------------------------


class TestCountTrailingCleanApprovals:
    async def test_counts_trailing_human_approvals(self):
        conn = FakeConn(
            fetch_result=[_row("t3", "approved"), _row("t2", "approved"), _row("t1", "approved")]
        )
        assert await count_trailing_clean_approvals(FakePool(conn), gate_name="g") == 3

    async def test_rejected_breaks_the_streak(self):
        conn = FakeConn(
            fetch_result=[
                _row("t4", "approved"),
                _row("t3", "rejected"),
                _row("t2", "approved"),
                _row("t1", "approved"),
            ]
        )
        assert await count_trailing_clean_approvals(FakePool(conn), gate_name="g") == 1

    async def test_dismissed_breaks_even_from_the_staleness_sweep(self):
        # An expired gate (ExpireStaleSeoRefreshGatesJob dismisses with
        # actor='staleness_sweep') is not a trust signal — streak resets.
        conn = FakeConn(
            fetch_result=[
                _row("t3", "approved"),
                _row("t2", "dismissed", actor="staleness_sweep"),
                _row("t1", "approved"),
            ]
        )
        assert await count_trailing_clean_approvals(FakePool(conn), gate_name="g") == 1

    async def test_non_human_approvals_are_trust_neutral(self):
        # Skipped, not counted, not streak-breaking: only operator sign-offs
        # earn trust, but an automated approval must not reset it either.
        conn = FakeConn(
            fetch_result=[
                _row("t3", "approved", actor="auto_publish"),
                _row("t2", "approved"),
                _row("t1", "approved"),
            ]
        )
        assert await count_trailing_clean_approvals(FakePool(conn), gate_name="g") == 2

    async def test_double_approval_of_one_task_counts_once(self):
        # Crash-and-reapprove (the c2 stale-approval path) writes two rows for
        # one task; "N clean runs" means N distinct tasks.
        conn = FakeConn(
            fetch_result=[_row("t1", "approved"), _row("t1", "approved"), _row("t2", "approved")]
        )
        assert await count_trailing_clean_approvals(FakePool(conn), gate_name="g") == 2

    async def test_empty_history_is_zero(self):
        assert (
            await count_trailing_clean_approvals(FakePool(FakeConn()), gate_name="g") == 0
        )

    async def test_query_scopes_to_gate_and_decision_kinds(self):
        seen: list[tuple] = []

        def _capture(sql: str, args: tuple):
            seen.append((sql, args))
            return []

        conn = FakeConn(fetch_result=_capture)
        await count_trailing_clean_approvals(
            FakePool(conn), gate_name="seo_refresh_gate", scan_limit=40
        )
        sql, args = seen[0]
        assert "gate_name = $1" in sql
        # auto_approved (graduated passes) must stay OUTSIDE the scan so
        # graduation cannot un-graduate itself.
        assert "IN ('approved', 'rejected', 'dismissed')" in sql
        assert args == ("seo_refresh_gate", 40)


# ---------------------------------------------------------------------------
# 2. run() — the graduation branch
# ---------------------------------------------------------------------------

_SETTING = "seo.refresh.auto_publish_after_clean_runs"


class _SC:
    def __init__(self, cfg: dict):
        self._cfg = cfg

    def get(self, key, default=None):
        return self._cfg.get(key, default)


def _state(conn: FakeConn, *, threshold: str | None = "2", **extra) -> dict:
    cfg = {"pipeline_gate_seo_refresh_gate": "on"}
    if threshold is not None:
        cfg[_SETTING] = threshold
    base = {
        "task_id": "task-123",
        "gate_name": "seo_refresh_gate",
        "database_service": SimpleNamespace(pool=FakePool(conn)),
        "site_config": _SC(cfg),
        "graduation_setting": _SETTING,
    }
    base.update(extra)
    return base


class TestGraduationBranch:
    async def test_streak_met_auto_approves_without_pausing(self):
        conn = FakeConn(fetch_result=[_row("a", "approved"), _row("b", "approved")])
        pause = AsyncMock()
        with patch("services.approval_service.pause_at_gate", pause):
            out = await ag.run(_state(conn, threshold="2"))

        assert out == {}
        pause.assert_not_awaited()
        sql = executed_sql(conn)
        assert "auto_approved" in sql
        assert "'graduation'" in sql
        insert_args = conn.executed[-1][1]
        assert insert_args[0] == "task-123"
        assert insert_args[1] == "seo_refresh_gate"

    async def test_streak_short_pauses_with_progress_in_artifact(self):
        conn = FakeConn(fetch_result=[_row("a", "approved")])
        pause = AsyncMock()
        with (
            patch("services.approval_service.pause_at_gate", pause),
            patch.object(ag, "_notify_critical", AsyncMock()),
            patch.object(ag, "interrupt", return_value="resumed"),
        ):
            out = await ag.run(_state(conn, threshold="2"))

        assert out == {}
        pause.assert_awaited_once()
        artifact = pause.await_args.kwargs["artifact"]
        assert artifact["graduation_progress"] == (
            "1/2 trailing clean approvals toward auto-publish"
        )
        assert "auto_approved" not in executed_sql(conn)

    async def test_no_graduation_setting_means_plain_pause(self):
        conn = FakeConn(fetch_result=[_row("a", "approved"), _row("b", "approved")])
        pause = AsyncMock()
        state = _state(conn)
        state.pop("graduation_setting")
        with (
            patch("services.approval_service.pause_at_gate", pause),
            patch.object(ag, "_notify_critical", AsyncMock()),
            patch.object(ag, "interrupt", return_value="resumed"),
        ):
            out = await ag.run(state)

        assert out == {}
        pause.assert_awaited_once()
        assert "graduation_progress" not in pause.await_args.kwargs["artifact"]

    async def test_zero_threshold_disables_graduation(self):
        conn = FakeConn(fetch_result=[_row("a", "approved"), _row("b", "approved")])
        pause = AsyncMock()
        with (
            patch("services.approval_service.pause_at_gate", pause),
            patch.object(ag, "_notify_critical", AsyncMock()),
            patch.object(ag, "interrupt", return_value="resumed"),
        ):
            out = await ag.run(_state(conn, threshold="0"))

        assert out == {}
        pause.assert_awaited_once()
        # Disabled = no progress line either: 0 means "never graduate", not "0/0".
        assert "graduation_progress" not in pause.await_args.kwargs["artifact"]

    async def test_unparseable_threshold_disables_graduation(self):
        conn = FakeConn(fetch_result=[_row("a", "approved"), _row("b", "approved")])
        pause = AsyncMock()
        with (
            patch("services.approval_service.pause_at_gate", pause),
            patch.object(ag, "_notify_critical", AsyncMock()),
            patch.object(ag, "interrupt", return_value="resumed"),
        ):
            out = await ag.run(_state(conn, threshold="not-a-number"))

        assert out == {}
        pause.assert_awaited_once()

    async def test_streak_read_failure_fails_closed_to_pause(self):
        conn = FakeConn(fetch_result=[_row("a", "approved"), _row("b", "approved")])
        pause = AsyncMock()
        with (
            patch(
                "services.approval_service.count_trailing_clean_approvals",
                AsyncMock(side_effect=RuntimeError("db gone")),
            ),
            patch("services.approval_service.pause_at_gate", pause),
            patch.object(ag, "_notify_critical", AsyncMock()),
            patch.object(ag, "interrupt", return_value="resumed"),
        ):
            out = await ag.run(_state(conn, threshold="2"))

        assert out == {}
        pause.assert_awaited_once()
        assert "auto_approved" not in executed_sql(conn)

    async def test_record_failure_fails_closed_to_pause(self):
        # An autonomous pass that can't be recorded would be unauditable —
        # the gate must pause instead.
        conn = FakeConn(fetch_result=[_row("a", "approved"), _row("b", "approved")])
        pause = AsyncMock()
        with (
            patch.object(
                ag, "_record_graduated_pass", AsyncMock(return_value=False)
            ),
            patch("services.approval_service.pause_at_gate", pause),
            patch.object(ag, "_notify_critical", AsyncMock()),
            patch.object(ag, "interrupt", return_value="resumed"),
        ):
            out = await ag.run(_state(conn, threshold="2"))

        assert out == {}
        pause.assert_awaited_once()

    async def test_explicit_rejection_outranks_graduation(self):
        # A rejected row for THIS task halts even when the gate's streak has
        # graduated — an operator veto is newer intent than earned trust.
        conn = FakeConn(
            fetchrow_result={
                "event_kind": "rejected",
                "approved_at_retry_count": None,
                "current_retry_count": 0,
            },
            fetch_result=[_row("a", "approved"), _row("b", "approved")],
        )
        out = await ag.run(_state(conn, threshold="2"))
        assert out.get("_halt") is True
        assert "auto_approved" not in executed_sql(conn)
