"""Unit tests for services.atom_runs writers (atom-cutover Plan 2, #355).

No DB — uses an asyncpg-pool stub that records execute() calls (and can
return a preset status string) for assertion, mirroring
tests/unit/services/test_capability_outcomes.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from services import atom_runs
from services.atom_runs import persist_atom_runs

# --- asyncpg pool stub ------------------------------------------------------


class _Conn:
    def __init__(self, sink: list[tuple[str, tuple[Any, ...]]], result: Any):
        self._sink = sink
        self._result = result

    async def execute(self, sql: str, *args: Any) -> Any:
        self._sink.append((sql, args))
        return self._result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _Acquire:
    def __init__(self, conn: _Conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_a):
        return False


class _Pool:
    def __init__(self, result: Any = None) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._conn = _Conn(self.executed, result)

    def acquire(self):
        return _Acquire(self._conn)


# --- record + site_config stubs --------------------------------------------


@dataclass
class _Rec:
    name: str
    ok: bool = True
    halted: bool = False
    skipped: bool = False
    elapsed_ms: int = 100
    node_id: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


class _Cfg:
    def __init__(self, vals: dict[str, Any]):
        self._vals = vals

    def get(self, key: str, default: Any = None) -> Any:
        return self._vals.get(key, default)


# --- _status_of -------------------------------------------------------------


@pytest.mark.unit
class TestStatusOf:
    def test_ok(self):
        assert atom_runs._status_of(_Rec(name="a", ok=True)) == "ok"

    def test_skipped_wins(self):
        assert atom_runs._status_of(_Rec(name="a", ok=True, skipped=True)) == "skipped"

    def test_halted(self):
        assert atom_runs._status_of(_Rec(name="a", ok=False, halted=True)) == "halted"

    def test_error(self):
        assert atom_runs._status_of(_Rec(name="a", ok=False)) == "error"


# --- persist_atom_runs ------------------------------------------------------


@pytest.mark.unit
class TestPersistAtomRuns:
    async def test_writes_one_row_per_record(self):
        pool = _Pool()
        records = [_Rec(name="atoms.x"), _Rec(name="atoms.y")]
        n = await persist_atom_runs(
            pool, run_id="r1", task_id="t1",
            template_slug="canonical_blog", records=records,
        )
        assert n == 2
        # Two INSERTs into atom_runs.
        inserts = [c for c in pool.executed if "INSERT INTO atom_runs" in c[0]]
        assert len(inserts) == 2

    async def test_maps_record_fields_onto_insert_args(self):
        pool = _Pool()
        rec = _Rec(
            name="atoms.writer", ok=True, elapsed_ms=2500, node_id="n7",
            metrics={
                "model_used": "test-model:9b", "cost": 0.0,
                "retries": 1, "input_digest": "abc123",
                "output_digest": "def456",
                "input_keys": ["task_id", "topic"],
                "output_keys": ["content"],
            },
        )
        await persist_atom_runs(
            pool, run_id="run-9", task_id="task-9",
            template_slug="canonical_blog", records=[rec],
        )
        sql, args = pool.executed[0]
        # Positional INSERT order: run_id, task_id, template_slug, seq, atom,
        # node_id, tier, model, latency_ms, cost, retries, status,
        # input_digest, output_digest, input_keys, output_keys, metrics.
        assert args[0] == "run-9"
        assert args[1] == "task-9"
        assert args[2] == "canonical_blog"
        assert args[3] == 0                 # seq
        assert args[4] == "atoms.writer"    # atom
        assert args[5] == "n7"              # node_id
        assert args[7] == "test-model:9b"   # model
        assert args[8] == 2500              # latency_ms
        assert args[10] == 1                # retries
        assert args[11] == "ok"             # status
        assert args[12] == "abc123"         # input_digest
        assert args[13] == "def456"         # output_digest
        assert args[14] == ["task_id", "topic"]
        assert args[15] == ["content"]

    async def test_disabled_flag_skips_all_writes(self):
        pool = _Pool()
        cfg = _Cfg({"atom_runs_capture_enabled": "false"})
        n = await persist_atom_runs(
            pool, run_id="r", task_id="t",
            template_slug="s", records=[_Rec(name="atoms.x")],
            site_config=cfg,
        )
        assert n == 0
        assert pool.executed == []

    async def test_enabled_flag_true_writes(self):
        pool = _Pool()
        cfg = _Cfg({"atom_runs_capture_enabled": "true"})
        n = await persist_atom_runs(
            pool, run_id="r", task_id="t",
            template_slug="s", records=[_Rec(name="atoms.x")],
            site_config=cfg,
        )
        assert n == 1

    async def test_empty_records_noop(self):
        pool = _Pool()
        n = await persist_atom_runs(
            pool, run_id="r", task_id="t", template_slug="s", records=[],
        )
        assert n == 0
        assert pool.executed == []

    async def test_missing_metrics_persist_nulls_not_crash(self):
        pool = _Pool()
        # A stage-style record: no node_id, no digests in metrics.
        n = await persist_atom_runs(
            pool, run_id="r", task_id="t",
            template_slug="s", records=[_Rec(name="verify_task")],
        )
        assert n == 1
        _, args = pool.executed[0]
        assert args[5] is None    # node_id
        assert args[12] is None   # input_digest
        assert args[15] is None   # output_keys


from services.atom_runs import record_atom_run_outcome


@pytest.mark.unit
class TestRecordOutcome:
    async def test_updates_by_task_id_and_returns_rowcount(self):
        pool = _Pool(result="UPDATE 3")
        n = await record_atom_run_outcome(
            pool, task_id="t1", post_id="00000000-0000-0000-0000-0000000000aa",
            decision="approved", quality_score=88.5, edit_distance=12,
        )
        assert n == 3
        sql, args = pool.executed[0]
        assert "UPDATE atom_runs" in sql
        assert args[0] == "t1"
        assert args[1] is None  # run_id not scoped
        assert args[2] == "00000000-0000-0000-0000-0000000000aa"
        assert args[3] == "approved"
        assert args[4] == 88.5
        assert args[5] == 12

    async def test_run_id_scopes_the_update(self):
        pool = _Pool(result="UPDATE 1")
        await record_atom_run_outcome(
            pool, task_id="t1", run_id="run-1", decision="rejected",
        )
        _, args = pool.executed[0]
        assert args[0] == "t1"
        assert args[1] == "run-1"

    async def test_empty_task_id_noop(self):
        pool = _Pool()
        n = await record_atom_run_outcome(pool, task_id="")
        assert n == 0
        assert pool.executed == []

    async def test_unparseable_rowcount_returns_zero(self):
        pool = _Pool(result=None)  # stub returns None like a no-op execute
        n = await record_atom_run_outcome(pool, task_id="t1", decision="revised")
        assert n == 0
