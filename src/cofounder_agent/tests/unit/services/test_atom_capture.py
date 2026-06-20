"""Unit tests for the atom-node capture extension (atom-cutover Plan 2, #355):
_wrap_atom stamps node_id + input/output state-key digests onto the
TemplateRunRecord it appends to record_sink.

Also covers the ATOM_META.retry enforcement added for poindexter#681."""

from __future__ import annotations

import pytest

from plugins.atom import RetryPolicy
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

    async def test_atom_node_stamps_stage_column_with_node_id(self):
        """Validation finding 2: an atom node stamps ``pipeline_tasks.stage``
        with its node_id (via _mark_stage_column, which also folds in the
        last_progress_at heartbeat), so the stage column tracks the LIVE atom
        instead of freezing at the last stage.* node (verify_task)."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        sink: list = []

        async def run_fn(state):
            return {"content": "ok"}

        mark = AsyncMock()
        # _mark_stage_column is lazily imported inside _wrap_atom, so patch the
        # source before constructing the node.
        with patch("services.template_runner._mark_stage_column", mark):
            node = _wrap_atom(run_fn, "atoms.fake", "qa.programmatic", sink)
            db = SimpleNamespace(pool=object())
            await node({"task_id": "t-1", "database_service": db}, None)

        mark.assert_awaited_once()
        # _mark_stage_column(pool, task_id, stage_name) — stage is the node_id
        pool_arg, task_arg, stage_arg = mark.await_args.args
        assert pool_arg is db.pool
        assert task_arg == "t-1"
        assert stage_arg == "qa.programmatic"

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

    async def test_retry_succeeds_on_second_attempt(self):
        """Matching exception triggers retry; atom succeeds on retry."""
        calls = []

        async def flaky(state):
            calls.append(1)
            if len(calls) < 2:
                raise ConnectionError("transient")
            return {"ok": True}

        policy = RetryPolicy(max_attempts=3, backoff_s=0.0, retry_on=("ConnectionError",))
        node = _wrap_atom(flaky, "atoms.flaky", "n3", None, retry_policy=policy)
        out = await node({"task_id": "t"}, None)

        assert out == {"ok": True}
        assert len(calls) == 2

    async def test_retry_not_triggered_for_non_matching_exception(self):
        """Non-matching exception halts without retry."""
        calls = []

        async def boom(state):
            calls.append(1)
            raise RuntimeError("nope")

        policy = RetryPolicy(max_attempts=3, backoff_s=0.0, retry_on=("ConnectionError",))
        node = _wrap_atom(boom, "atoms.boom2", "n4", None, retry_policy=policy)
        out = await node({"task_id": "t"}, None)

        assert out.get("_halt") is True
        assert len(calls) == 1  # no retry

    async def test_retry_exhaustion_halts_graph(self):
        """All retries exhausted: final exception is recorded as halt."""
        calls = []

        async def always_fails(state):
            calls.append(1)
            raise ConnectionError("persistent")

        policy = RetryPolicy(max_attempts=3, backoff_s=0.0, retry_on=("ConnectionError",))
        node = _wrap_atom(always_fails, "atoms.persistent", "n5", None, retry_policy=policy)
        out = await node({"task_id": "t"}, None)

        assert out.get("_halt") is True
        assert len(calls) == 3  # all 3 attempts
