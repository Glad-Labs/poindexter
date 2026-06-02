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
