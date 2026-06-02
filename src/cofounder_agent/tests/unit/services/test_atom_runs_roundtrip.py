"""Composition test (atom-cutover Plan 2, #355): a record produced by the
real _wrap_atom flows through the real persist_atom_runs and lands the
node_id + digests + status on the INSERT — pins that the wrapper's metrics
keys match the writer's reads."""

from __future__ import annotations

from typing import Any

import pytest

from services.atom_runs import persist_atom_runs
from services.pipeline_architect import _wrap_atom


class _Conn:
    def __init__(self, sink):
        self._sink = sink

    async def execute(self, sql: str, *args: Any):
        self._sink.append((sql, args))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_a):
        return False


class _Pool:
    def __init__(self):
        self.executed: list = []
        self._conn = _Conn(self.executed)

    def acquire(self):
        return _Acquire(self._conn)


@pytest.mark.unit
class TestStatusOf:
    """Unit tests for the _status_of precedence chain."""

    def test_skipped_beats_halted_and_error(self):
        from services.atom_runs import _status_of
        from services.template_runner import TemplateRunRecord

        r = TemplateRunRecord(name="x", ok=False, halted=True, skipped=True)
        assert _status_of(r) == "skipped"

    def test_halted_beats_error(self):
        from services.atom_runs import _status_of
        from services.template_runner import TemplateRunRecord

        r = TemplateRunRecord(name="x", ok=False, halted=True)
        assert _status_of(r) == "halted"

    def test_not_ok_without_halt_is_error(self):
        from services.atom_runs import _status_of
        from services.template_runner import TemplateRunRecord

        r = TemplateRunRecord(name="x", ok=False)
        assert _status_of(r) == "error"

    def test_ok_record_is_ok(self):
        from services.atom_runs import _status_of
        from services.template_runner import TemplateRunRecord

        r = TemplateRunRecord(name="x", ok=True)
        assert _status_of(r) == "ok"


@pytest.mark.unit
class TestPersistAtomRunsEdgeCases:
    async def test_returns_zero_when_pool_is_none(self):
        from services.atom_runs import persist_atom_runs
        from services.template_runner import TemplateRunRecord

        records = [TemplateRunRecord(name="atoms.demo", ok=True)]
        n = await persist_atom_runs(None, run_id="r1", task_id="t1",
                                    template_slug="s", records=records)
        assert n == 0

    async def test_returns_zero_when_records_empty(self):
        from services.atom_runs import persist_atom_runs

        pool = _Pool()
        n = await persist_atom_runs(pool, run_id="r1", task_id="t1",
                                    template_slug="s", records=[])
        assert n == 0
        assert pool.executed == []

    async def test_returns_zero_when_capture_disabled(self):
        from services.atom_runs import persist_atom_runs
        from services.template_runner import TemplateRunRecord

        class _FakeConfig:
            def get(self, key, default=None):
                if key == "atom_runs_capture_enabled":
                    return "false"
                return default

        pool = _Pool()
        records = [TemplateRunRecord(name="atoms.demo", ok=True)]
        n = await persist_atom_runs(pool, run_id="r1", task_id="t1",
                                    template_slug="s", records=records,
                                    site_config=_FakeConfig())
        assert n == 0
        assert pool.executed == []

    async def test_swallows_db_exception_and_returns_zero(self):
        from services.atom_runs import persist_atom_runs
        from services.template_runner import TemplateRunRecord

        class _ErrConn:
            async def execute(self, *a, **kw):
                raise RuntimeError("db exploded")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

        class _ErrPool:
            def acquire(self):
                class _Ctx:
                    async def __aenter__(self):
                        return _ErrConn()

                    async def __aexit__(self, *_):
                        return False
                return _Ctx()

        records = [TemplateRunRecord(name="atoms.demo", ok=True)]
        n = await persist_atom_runs(_ErrPool(), run_id="r1", task_id="t1",
                                    template_slug="s", records=records)
        assert n == 0

    async def test_multiple_records_returns_count(self):
        from services.atom_runs import persist_atom_runs
        from services.template_runner import TemplateRunRecord

        records = [
            TemplateRunRecord(name="atoms.a", ok=True,
                              metrics={"input_keys": ["k"], "output_keys": ["v"],
                                       "input_digest": "d1", "output_digest": "d2"}),
            TemplateRunRecord(name="atoms.b", ok=True,
                              metrics={"input_keys": ["v"], "output_keys": ["w"],
                                       "input_digest": "d3", "output_digest": "d4"}),
        ]
        pool = _Pool()
        n = await persist_atom_runs(pool, run_id="r1", task_id="t1",
                                    template_slug="s", records=records)
        assert n == 2
        assert len(pool.executed) == 2


@pytest.mark.unit
class TestWrapAtomExceptionPath:
    """_wrap_atom must catch atom errors and emit a halted record."""

    async def test_exception_in_run_fn_writes_halted_record(self):
        from services.pipeline_architect import _wrap_atom

        sink: list = []

        async def boom(state):
            raise ValueError("atom kaboom")

        node = _wrap_atom(boom, "atoms.boom", "node-B", sink)
        result = await node({"task_id": "t1", "topic": "x"}, None)

        assert result.get("_halt") is True
        assert "atoms.boom" in result.get("_halt_reason", "")
        assert len(sink) == 1
        assert sink[0].ok is False
        assert sink[0].halted is True

    async def test_exception_record_persists_with_halted_status(self):
        from services.atom_runs import persist_atom_runs
        from services.pipeline_architect import _wrap_atom

        sink: list = []

        async def boom(state):
            raise RuntimeError("oops")

        node = _wrap_atom(boom, "atoms.crash", "node-C", sink)
        await node({"task_id": "t2", "topic": "y"}, None)

        pool = _Pool()
        n = await persist_atom_runs(pool, run_id="run-2", task_id="t2",
                                    template_slug="canonical_blog", records=sink)
        assert n == 1
        _, args = pool.executed[0]
        assert args[11] == "halted"   # status derived from record


@pytest.mark.unit
class TestWrapPersistRoundtrip:
    async def test_record_sink_persists_with_node_id_and_digests(self):
        sink: list = []

        async def run_fn(state):
            return {"content": "abc", "draft": "x"}

        node = _wrap_atom(run_fn, "atoms.demo", "node-A", sink)
        await node({"task_id": "task-1", "topic": "t"}, None)

        pool = _Pool()
        n = await persist_atom_runs(
            pool, run_id="run-1", task_id="task-1",
            template_slug="canonical_blog", records=sink,
        )
        assert n == 1
        _, args = pool.executed[0]
        # run_id, task_id, template_slug, seq, atom, node_id, tier, model,
        # latency_ms, cost, retries, status, input_digest, output_digest, ...
        assert args[0] == "run-1"
        assert args[4] == "atoms.demo"
        assert args[5] == "node-A"        # node_id threaded end-to-end
        assert args[11] == "ok"           # status derived
        assert args[12] is not None       # input_digest
        assert args[13] is not None       # output_digest
        assert "content" in args[15]      # output_keys
