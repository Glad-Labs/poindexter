"""Integration tests for the Phase 1 variant runner.

End-to-end coverage of ``services/experiment_runner.py::pick_variant``
+ the lab views' read of the resulting ``variant_id`` against a real
disposable Postgres. The unit tests
(``tests/unit/services/test_experiment_runner.py``) cover the runner's
behavior with stubbed pools; this file pins the SQL contract:

1. Insert experiment + 2 variants on niche ``glad-labs``, activate, call
   ``pick_variant``, assert returns a variant.
2. Insert a ``capability_outcomes`` row with state-dict variant_id +
   query ``lab_outcomes_v1``: variant_label + experiment_key columns
   populated end-to-end (PR #699 LEFT JOINs work).
3. ``experiment_variant_scorecard_v1`` shows posts_attempted=1 for the
   matched variant after the above insert.

Run with:

    cd src/cofounder_agent && \\
      poetry run pytest tests/integration_db/test_phase1_experiment_runner.py -v
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


# ---------------------------------------------------------------------------
# 1. pick_variant returns a row for an active 2-variant experiment.
#
# The runner reads through the real partial-unique-index path
# (status='active' + ev.active=true). With 2 actives we don't assert
# WHICH one comes back — random.choice — but both are valid, and the
# return shape is verified.
# ---------------------------------------------------------------------------
async def test_pick_variant_returns_active_variant(test_txn) -> None:
    from services.experiment_runner import ExperimentVariant, pick_variant

    exp_id = await test_txn.fetchval(
        """
        INSERT INTO experiments (key, niche_slug, status, activated_at)
        VALUES ($1, 'glad-labs', 'active', now())
        RETURNING id
        """,
        f"glad-labs/runner-test-{uuid.uuid4().hex[:8]}",
    )
    variant_ids = {}
    for label, model in (("A", "gemma4:31b"), ("B", "qwen3.6:latest")):
        vid = await test_txn.fetchval(
            """
            INSERT INTO experiment_variants
              (experiment_id, label, writer_model)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            exp_id, label, model,
        )
        variant_ids[label] = str(vid)

    # The runner uses pool.acquire() — we have a single connection inside
    # a txn here. Wrap so the runner's ``async with pool.acquire()`` sees
    # a no-op acquire that yields our existing conn.
    pool = _ConnAsPool(test_txn)

    result = await pick_variant(pool, "glad-labs", task_id="task-int-1")

    assert isinstance(result, ExperimentVariant)
    assert result.variant_label in ("A", "B")
    assert result.variant_id in variant_ids.values()
    assert result.experiment_key.startswith("glad-labs/runner-test-")
    # Expected model override (depends on which variant the random
    # picked, but it must match one of the two configured).
    assert result.writer_model in ("gemma4:31b", "qwen3.6:latest")


# ---------------------------------------------------------------------------
# 2. lab_outcomes_v1 surfaces variant_label + experiment_key.
#
# Insert a tagged capability_outcomes row and verify the new columns
# come back populated via the LEFT JOINs PR #699 added to the view.
# ---------------------------------------------------------------------------
async def test_lab_outcomes_v1_joins_variant_and_experiment(test_txn) -> None:
    exp_id = await test_txn.fetchval(
        """
        INSERT INTO experiments (key, niche_slug, status)
        VALUES ($1, 'glad-labs', 'draft')
        RETURNING id
        """,
        f"glad-labs/joins-{uuid.uuid4().hex[:8]}",
    )
    variant_id = await test_txn.fetchval(
        """
        INSERT INTO experiment_variants
          (experiment_id, label, writer_model)
        VALUES ($1, 'A-joins', 'qwen3.6:latest')
        RETURNING id
        """,
        exp_id,
    )

    task_id = f"phase1-runner-{uuid.uuid4().hex[:8]}"
    await test_txn.execute(
        """
        INSERT INTO capability_outcomes
          (task_id, template_slug, node_name, ok, variant_id, niche_slug)
        VALUES ($1, 'canonical_blog', 'generate_content', TRUE, $2, 'glad-labs')
        """,
        task_id, variant_id,
    )

    row = await test_txn.fetchrow(
        """
        SELECT variant_label, variant_id, experiment_key, experiment_status,
               experiment_objective_function
        FROM lab_outcomes_v1
        WHERE task_id = $1
        """,
        task_id,
    )
    assert row is not None, (
        "lab_outcomes_v1 dropped the tagged row — LEFT JOIN regression"
    )
    assert row["variant_label"] == "A-joins"
    assert row["variant_id"] == variant_id
    assert row["experiment_key"].startswith("glad-labs/joins-")
    assert row["experiment_status"] == "draft"
    # PR #699 default objective_function — proves the experiments row
    # is being joined, not just experiment_variants.
    assert row["experiment_objective_function"] == "views_7d"


# ---------------------------------------------------------------------------
# 3. Scorecard increments posts_attempted for the matched variant.
#
# After inserting one tagged capability_outcomes row, scorecard view
# must show posts_attempted=1 for that variant + 0 for any sibling.
# ---------------------------------------------------------------------------
async def test_scorecard_counts_attempted_post_after_runner_insert(
    test_txn,
) -> None:
    exp_id = await test_txn.fetchval(
        """
        INSERT INTO experiments (key, niche_slug, status)
        VALUES ($1, 'glad-labs', 'draft')
        RETURNING id
        """,
        f"glad-labs/scorecard-{uuid.uuid4().hex[:8]}",
    )
    matched_variant_id = await test_txn.fetchval(
        """
        INSERT INTO experiment_variants (experiment_id, label, writer_model)
        VALUES ($1, 'matched', 'qwen3.6:latest')
        RETURNING id
        """,
        exp_id,
    )
    await test_txn.execute(
        """
        INSERT INTO experiment_variants (experiment_id, label)
        VALUES ($1, 'sibling')
        """,
        exp_id,
    )

    task_id = f"phase1-scorecard-{uuid.uuid4().hex[:8]}"
    await test_txn.execute(
        """
        INSERT INTO capability_outcomes
          (task_id, template_slug, node_name, ok, variant_id, niche_slug)
        VALUES ($1, 'canonical_blog', 'generate_content', TRUE, $2, 'glad-labs')
        """,
        task_id, matched_variant_id,
    )

    rows = {
        r["variant_label"]: r
        for r in await test_txn.fetch(
            """
            SELECT variant_label, posts_attempted, posts_approved
            FROM experiment_variant_scorecard_v1
            WHERE experiment_id = $1
            """,
            exp_id,
        )
    }
    assert rows["matched"]["posts_attempted"] == 1, (
        f"scorecard did not register the tagged row; "
        f"got {rows['matched']['posts_attempted']}"
    )
    assert rows["sibling"]["posts_attempted"] == 0, (
        "sibling variant must not show an attempted post"
    )
    # No publish, so posts_approved stays 0 on both.
    assert rows["matched"]["posts_approved"] == 0
    assert rows["sibling"]["posts_approved"] == 0


# ---------------------------------------------------------------------------
# Helper — adapt a single asyncpg connection (test_txn) to the
# ``pool.acquire()`` shape pick_variant expects.
# ---------------------------------------------------------------------------


class _ConnAcquire:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_a):
        return False


class _ConnAsPool:
    """Bridge a single asyncpg.Connection to the pool.acquire() shape.

    The integration suite hands tests an in-transaction connection
    (rollback at teardown). The runner's ``pool.acquire()`` semantics
    work fine against a single connection — we never touch the pool's
    own pooling behavior.
    """

    def __init__(self, conn) -> None:
        self._conn = conn

    def acquire(self) -> _ConnAcquire:
        return _ConnAcquire(self._conn)
