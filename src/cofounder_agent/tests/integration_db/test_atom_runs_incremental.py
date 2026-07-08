"""Incremental ``atom_runs`` upsert — ``persist_one_atom_run`` is idempotent.

The console task-trace needs per-node rows to appear *as a run executes* (live
trace) and to survive a run being killed mid-graph (partial trace), instead of
a single batch write at end-of-run. That means each node's row is upserted on
``(run_id, seq)`` — a re-persist (incremental write, then the end-of-run batch
safety net, or a rescue-loop) must update in place, never duplicate.

Runs against the disposable ``integration_db`` test DB — baseline schema + the
full migration chain, including the ``output_preview`` column and the
``atom_runs_run_id_seq_key`` UNIQUE constraint — so the ``ON CONFLICT`` clause
is validated against the *real* schema. That's the class of SQL-vs-schema bug a
mocked unit test can't catch (see ``test_claim_pending_task`` next door).

``persist_one_atom_run`` acquires its own connection from the pool, so it needs
the real ``test_pool`` (not the auto-rollback ``test_txn``); the whole test DB
is dropped at session teardown, so session-scoped writes don't leak.

Run with::

    poetry run pytest tests/integration_db/test_atom_runs_incremental.py \
        -m integration_db -q
"""

from __future__ import annotations

import pytest

from services.atom_runs import persist_one_atom_run
from services.template_runner import TemplateRunRecord

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


def _record(*, preview: str) -> TemplateRunRecord:
    return TemplateRunRecord(
        name="qa.critic",
        ok=True,
        elapsed_ms=1200,
        node_id="qa_critic",
        output_preview=preview,
        metrics={
            "model": "sonnet",
            "input_keys": ["draft"],
            "output_keys": ["x"],
            "input_digest": "din",
            "output_digest": "dout",
        },
    )


async def test_persist_one_is_idempotent_on_run_id_seq(test_pool):
    """A second persist of the same (run_id, seq) upserts, not duplicates."""
    run_id = "trace-incremental-idempotent"
    async with test_pool.acquire() as c:
        await c.execute("DELETE FROM atom_runs WHERE run_id = $1", run_id)

    rec = _record(preview='{"x":1}')
    a = await persist_one_atom_run(
        test_pool, run_id=run_id, task_id="T1",
        template_slug="canonical_blog", seq=5, record=rec,
    )
    b = await persist_one_atom_run(
        test_pool, run_id=run_id, task_id="T1",
        template_slug="canonical_blog", seq=5, record=rec,
    )
    assert a == 1 and b == 1  # both report a write; the second is an upsert

    async with test_pool.acquire() as c:
        n = await c.fetchval(
            "SELECT count(*) FROM atom_runs WHERE run_id = $1 AND seq = 5", run_id,
        )
        row = await c.fetchrow(
            "SELECT atom, node_id, output_preview, output_keys "
            "FROM atom_runs WHERE run_id = $1 AND seq = 5",
            run_id,
        )
    assert n == 1  # exactly one row, not two
    assert row["output_preview"] == '{"x":1}'
    assert row["atom"] == "qa.critic"
    assert row["node_id"] == "qa_critic"
    assert list(row["output_keys"]) == ["x"]


async def test_upsert_refreshes_output_preview_on_conflict(test_pool):
    """The end-of-run batch (or a re-run) refines the row an incremental write
    left behind — a changed ``output_preview`` overwrites in place."""
    run_id = "trace-incremental-refresh"
    async with test_pool.acquire() as c:
        await c.execute("DELETE FROM atom_runs WHERE run_id = $1", run_id)

    first = await persist_one_atom_run(
        test_pool, run_id=run_id, task_id="T2",
        template_slug="canonical_blog", seq=0, record=_record(preview='{"stage":"partial"}'),
    )
    second = await persist_one_atom_run(
        test_pool, run_id=run_id, task_id="T2",
        template_slug="canonical_blog", seq=0, record=_record(preview='{"stage":"final"}'),
    )
    assert first == 1 and second == 1

    async with test_pool.acquire() as c:
        n = await c.fetchval(
            "SELECT count(*) FROM atom_runs WHERE run_id = $1", run_id,
        )
        preview = await c.fetchval(
            "SELECT output_preview FROM atom_runs WHERE run_id = $1 AND seq = 0", run_id,
        )
    assert n == 1
    assert preview == '{"stage":"final"}'  # overwritten, not appended
