"""Unit tests for the atom-node capture extension (atom-cutover Plan 2, #355):
_wrap_atom stamps node_id + input/output state-key digests onto the
TemplateRunRecord it appends to record_sink."""

from __future__ import annotations

import pytest

from services.atom_runs import digest_keys
from services.pipeline_architect import _wrap_atom


@pytest.mark.unit
class TestAtomCapture:
    async def test_success_record_carries_node_id_and_digests(self):
        sink: list = []

        async def run_fn(state):
            return {"content": "hello world", "new_key": 1}

        node = _wrap_atom(run_fn, "atoms.fake", "n1", sink)
        out = await node({"task_id": "t", "topic": "x"}, None)

        assert out == {"content": "hello world", "new_key": 1}
        assert len(sink) == 1
        rec = sink[0]
        assert rec.ok is True
        assert rec.node_id == "n1"
        # Input keys captured from the merged atom input (services none here).
        assert "task_id" in rec.metrics["input_keys"]
        assert "topic" in rec.metrics["input_keys"]
        # Output keys captured from the atom's returned dict.
        assert set(rec.metrics["output_keys"]) == {"content", "new_key"}
        # Digests are the sha256-of-sorted-keys helper.
        assert rec.metrics["input_digest"] == digest_keys(rec.metrics["input_keys"])
        assert rec.metrics["output_digest"] == digest_keys(rec.metrics["output_keys"])

    async def test_failure_record_carries_node_id(self):
        sink: list = []

        async def boom(state):
            raise ValueError("nope")

        node = _wrap_atom(boom, "atoms.boom", "n2", sink)
        out = await node({"task_id": "t"}, None)

        # Failure path halts the graph.
        assert out.get("_halt") is True
        assert len(sink) == 1
        rec = sink[0]
        assert rec.ok is False
        assert rec.halted is True
        assert rec.node_id == "n2"
        assert "task_id" in rec.metrics["input_keys"]
