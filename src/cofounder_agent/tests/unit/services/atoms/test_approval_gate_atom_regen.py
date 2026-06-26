"""Unit tests for the regen-steering (#149) additions to atoms.approval_gate.

Covers:
  - ``_read_regen_steering`` — returns feedback, returns None on no row, swallows errors.
  - ``run()`` regen branch — ``regen_steering`` key injected into output when steering
    is present; absent from output when the helper returns None.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import modules.content.atoms.approval_gate as ag
from tests.unit.services._gate_fakes import FakeConn, FakePool

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _read_regen_steering
# ---------------------------------------------------------------------------


class TestReadRegenSteering:
    async def test_returns_feedback_when_row_present(self):
        conn = FakeConn(fetchrow_result={"feedback": "add concrete examples"})
        pool = FakePool(conn)
        result = await ag._read_regen_steering(pool, "t1", "preview_approval")
        assert result == "add concrete examples"

    async def test_returns_none_when_no_row(self):
        conn = FakeConn(fetchrow_result=None)
        pool = FakePool(conn)
        result = await ag._read_regen_steering(pool, "t1", "preview_approval")
        assert result is None

    async def test_returns_none_on_pool_error(self):
        """Best-effort helper: any exception from pool.acquire() returns None."""

        class _BrokenPool:
            def acquire(self):
                raise RuntimeError("pool gone")

        result = await ag._read_regen_steering(_BrokenPool(), "t1", "preview_approval")
        assert result is None

    async def test_passes_task_id_and_gate_name_as_query_args(self):
        """SQL args must include task_id and gate_name — the WHERE clause
        relies on both to return only the right gate's regen rows."""
        seen_args: list[tuple] = []

        def _capture(_sql: str, args: tuple):
            seen_args.append(args)
            return {"feedback": "some reason"}

        conn = FakeConn(fetchrow_result=_capture)
        pool = FakePool(conn)
        await ag._read_regen_steering(pool, "task-abc", "draft_gate")
        assert seen_args, "fetchrow was never called"
        args = seen_args[0]
        assert "task-abc" in args
        assert "draft_gate" in args


# ---------------------------------------------------------------------------
# run() — regen_steering injection into the output dict
# ---------------------------------------------------------------------------


def _regen_state(pool: FakePool) -> dict:
    """Minimal state that drives run() down the regen-text branch."""
    sc = MagicMock()
    sc.get.return_value = "on"  # is_gate_enabled → True
    return {
        "gate_name": "preview_approval",
        "task_id": "t1",
        "database_service": SimpleNamespace(pool=pool),
        "site_config": sc,
        "regen_targets": {"text": "content.generate_draft"},
    }


@pytest.mark.asyncio
class TestRegenSteeringInjection:
    async def test_steering_injected_into_output_when_present(self):
        pool = FakePool(FakeConn())
        state = _regen_state(pool)
        with (
            patch.object(ag, "_gate_decision", AsyncMock(return_value=None)),
            patch.object(ag, "_pending_regen", AsyncMock(return_value="text")),
            patch.object(ag, "_read_regen_steering", AsyncMock(return_value="more examples")),
            patch.object(ag, "_consume_regen", AsyncMock()),
        ):
            out = await ag.run(state)

        assert out.get("_goto") == "content.generate_draft"
        assert out["regen_steering"] == "more examples"

    async def test_steering_absent_from_output_when_none(self):
        """A bare regen with no --reason yields no regen_steering key; the
        writer falls back to its un-steered prompt."""
        pool = FakePool(FakeConn())
        state = _regen_state(pool)
        with (
            patch.object(ag, "_gate_decision", AsyncMock(return_value=None)),
            patch.object(ag, "_pending_regen", AsyncMock(return_value="text")),
            patch.object(ag, "_read_regen_steering", AsyncMock(return_value=None)),
            patch.object(ag, "_consume_regen", AsyncMock()),
        ):
            out = await ag.run(state)

        assert out.get("_goto") == "content.generate_draft"
        assert "regen_steering" not in out

    async def test_consume_always_called_even_when_steering_is_none(self):
        """The one-shot flag must be cleared regardless of whether steering was
        found — otherwise the loop-back re-entry would re-trigger the regen."""
        pool = FakePool(FakeConn())
        state = _regen_state(pool)
        consume_mock = AsyncMock()
        with (
            patch.object(ag, "_gate_decision", AsyncMock(return_value=None)),
            patch.object(ag, "_pending_regen", AsyncMock(return_value="text")),
            patch.object(ag, "_read_regen_steering", AsyncMock(return_value=None)),
            patch.object(ag, "_consume_regen", consume_mock),
        ):
            await ag.run(state)

        consume_mock.assert_awaited_once()
