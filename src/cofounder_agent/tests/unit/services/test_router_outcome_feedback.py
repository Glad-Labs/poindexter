"""Unit tests for ``services/router_outcome_feedback.py`` (#361 part 1).

No real DB — a programmable asyncpg-pool stub records execute() calls and
returns scripted fetch/fetchval results so we can assert the EWMA weight
nudge + the atom_runs backfill + the best-effort (never-raises) contract.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.router_outcome_feedback import ewma, record_task_outcome

# ---------------------------------------------------------------------------
# Programmable asyncpg-shaped stub
# ---------------------------------------------------------------------------


class _Conn:
    def __init__(self, owner: _Pool) -> None:
        self._owner = owner

    async def execute(self, sql: str, *args: Any) -> Any:
        self._owner.executed.append((sql, args))
        return self._owner.execute_result

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self._owner.fetched.append((sql, args))
        return self._owner.variant_rows

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self._owner.fetchvals.append((sql, args))
        if "app_settings" in sql:
            return self._owner.alpha_value
        if "weight" in sql.lower():
            return self._owner.weight_value
        return None

    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _Conn:
        return self._conn

    async def __aexit__(self, *_a: Any) -> bool:
        return False


class _Pool:
    def __init__(
        self,
        *,
        variant_rows: list[Any] | None = None,
        weight_value: Any = 0.5,
        alpha_value: Any = None,
        execute_result: Any = "UPDATE 1",
    ) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.fetched: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchvals: list[tuple[str, tuple[Any, ...]]] = []
        self.variant_rows = variant_rows if variant_rows is not None else []
        self.weight_value = weight_value
        self.alpha_value = alpha_value
        self.execute_result = execute_result

    def acquire(self) -> _Acquire:
        return _Acquire(_Conn(self))


class _RaisingPool:
    def acquire(self) -> Any:
        raise RuntimeError("simulated DB outage")


def _variant_row(variant_id: str) -> dict[str, Any]:
    return {"variant_id": variant_id}


# ---------------------------------------------------------------------------
# EWMA math (pure helper)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEwma:
    def test_approved_nudges_up(self):
        # alpha=0.2, old=0.5, signal=1.0 -> 0.8*0.5 + 0.2*1.0 = 0.6
        assert ewma(0.5, 1.0, 0.2) == pytest.approx(0.6)

    def test_rejected_nudges_down(self):
        # alpha=0.2, old=0.5, signal=0.0 -> 0.8*0.5 = 0.4
        assert ewma(0.5, 0.0, 0.2) == pytest.approx(0.4)

    def test_clamps_to_floor(self):
        # A long reject streak can't drive below the floor.
        assert ewma(0.011, 0.0, 0.2) >= 0.01

    def test_clamps_to_ceiling(self):
        assert ewma(1.0, 1.0, 0.2) <= 1.0


# ---------------------------------------------------------------------------
# record_task_outcome
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecordTaskOutcome:
    async def test_approved_backfills_and_nudges_up(self):
        pool = _Pool(
            variant_rows=[_variant_row("v-1")],
            weight_value=0.5,
            alpha_value="0.2",
        )
        summary = await record_task_outcome(
            pool=pool, task_id="t1", decision="approved",
        )
        assert summary["ok"] is True
        assert summary["decision"] == "approved"
        # atom_runs backfill ran (UPDATE atom_runs is one of the executes).
        assert any("UPDATE atom_runs" in sql for sql, _ in pool.executed)
        # backfill rowcount parsed from "UPDATE 1".
        assert summary["atom_rows_backfilled"] == 1
        # weight UPDATE ran with the EWMA-nudged-up value.
        weight_updates = [
            args for sql, args in pool.executed
            if "UPDATE experiment_variants" in sql
        ]
        assert len(weight_updates) == 1
        # args = (variant_id, new_weight)
        assert weight_updates[0][0] == "v-1"
        assert weight_updates[0][1] == pytest.approx(0.6)
        assert summary["variants_updated"][0]["new_weight"] == pytest.approx(0.6)
        assert summary["variants_updated"][0]["new_weight"] > 0.5

    async def test_rejected_nudges_down(self):
        pool = _Pool(
            variant_rows=[_variant_row("v-2")],
            weight_value=0.5,
            alpha_value="0.2",
        )
        summary = await record_task_outcome(
            pool=pool, task_id="t2", decision="rejected",
        )
        assert summary["ok"] is True
        weight_updates = [
            args for sql, args in pool.executed
            if "UPDATE experiment_variants" in sql
        ]
        assert len(weight_updates) == 1
        assert weight_updates[0][1] == pytest.approx(0.4)
        assert summary["variants_updated"][0]["new_weight"] < 0.5

    async def test_no_variant_still_backfills(self):
        # No variant rows for the task -> backfill happens, no weight update.
        pool = _Pool(variant_rows=[], alpha_value="0.2")
        summary = await record_task_outcome(
            pool=pool, task_id="t3", decision="approved",
        )
        assert summary["ok"] is True
        assert summary["variants_updated"] == []
        assert any("UPDATE atom_runs" in sql for sql, _ in pool.executed)
        assert not any(
            "UPDATE experiment_variants" in sql for sql, _ in pool.executed
        )

    async def test_quality_and_edit_distance_threaded_to_backfill(self):
        pool = _Pool(variant_rows=[], alpha_value="0.2")
        await record_task_outcome(
            pool=pool, task_id="t4", decision="approved",
            quality_score=88.5, edit_distance=12,
        )
        # The atom_runs UPDATE carries quality_score + edit_distance in its
        # params (positions 5 and 6 per record_atom_run_outcome's signature).
        atom_update = next(
            args for sql, args in pool.executed if "UPDATE atom_runs" in sql
        )
        assert 88.5 in atom_update
        assert 12 in atom_update

    async def test_ambiguous_decision_skips_weight_nudge(self):
        pool = _Pool(variant_rows=[_variant_row("v-9")], alpha_value="0.2")
        summary = await record_task_outcome(
            pool=pool, task_id="t5", decision="dismissed",
        )
        assert summary["ok"] is True
        assert summary["variants_updated"] == []
        # No variant fetch should even be attempted.
        assert not any(
            "UPDATE experiment_variants" in sql for sql, _ in pool.executed
        )

    async def test_never_raises_on_pool_error(self):
        # acquire() raises -> summary returned, not an exception.
        summary = await record_task_outcome(
            pool=_RaisingPool(), task_id="t6", decision="approved",
        )
        assert summary["ok"] is False
        assert "error" in summary
        # Did not raise — the whole point of the best-effort contract.

    async def test_none_pool_noop(self):
        summary = await record_task_outcome(
            pool=None, task_id="t7", decision="approved",
        )
        assert summary["ok"] is False
        assert summary["variants_updated"] == []

    async def test_alpha_default_when_setting_missing(self):
        # alpha_value=None (no app_settings row) -> default 0.2.
        pool = _Pool(
            variant_rows=[_variant_row("v-3")],
            weight_value=0.5,
            alpha_value=None,
        )
        summary = await record_task_outcome(
            pool=pool, task_id="t8", decision="approved",
        )
        assert summary["variants_updated"][0]["alpha"] == pytest.approx(0.2)
        assert summary["variants_updated"][0]["new_weight"] == pytest.approx(0.6)
