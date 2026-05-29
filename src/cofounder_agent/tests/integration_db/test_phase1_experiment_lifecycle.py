"""Phase 1 PR 5 — experiment state-machine E2E.

Tests the ``draft → active → concluded`` lifecycle of an experiment
against a real disposable Postgres. The CLI integration tests in PR 3
(``tests/unit/services/test_experiments_cli.py``) cover the operator UX
on top of these calls; this file pins the SQL-layer contract those
calls rely on:

1. A draft experiment is ignored by ``pick_variant`` (only active
   experiments serve variants — production traffic stays on niche
   defaults until the operator activates).
2. Activating a draft (UPDATE status='active', activated_at) makes
   ``pick_variant`` start returning that experiment's variants.
3. A SECOND active experiment for the same niche fails with a clean
   ``asyncpg.exceptions.UniqueViolationError`` from the partial unique
   index ``idx_experiments_one_active_per_niche`` (PR 1). The
   one-active-per-niche invariant is enforced at the SQL layer, not
   relied on at the application layer.
4. Concluding an experiment (UPDATE status='concluded', concluded_at,
   winner_variant_label) makes ``pick_variant`` stop serving its
   variants — production traffic returns to niche defaults.
5. After conclusion, a NEW experiment can be activated for the same
   niche without violating the unique index (the index is partial on
   ``WHERE status = 'active'``).
6. ``winner_variant_label`` persists alongside ``concluded_at`` (PR 3
   migration ``20260529_012228_phase1_experiments_winner_label``).

These are pure DB-call paths — no CLI parsing, no FastAPI routes — so a
regression that breaks the lifecycle invariant (e.g. a future migration
that drops the partial unique index, or a runner change that starts
honouring draft experiments) trips a focused test in this file
instead of hiding behind the CLI layer.

Run with:

    cd src/cofounder_agent && \\
      poetry run pytest tests/integration_db/test_phase1_experiment_lifecycle.py -v

Each test uses ``test_txn`` so every INSERT rolls back at teardown —
no cross-test leakage. Hard rule: no operator info in seeded data
(``niche='test-niche-lifecycle'``, fake variant labels).
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


_NICHE = "test-niche-lifecycle"


# ---------------------------------------------------------------------------
# Helpers — bridge a single asyncpg.Connection (from test_txn) to the
# pool.acquire() shape pick_variant uses. Same pattern as the sibling
# integration files.
# ---------------------------------------------------------------------------


class _ConnAcquire:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_a):
        return False


class _ConnAsPool:
    def __init__(self, conn) -> None:
        self._conn = conn

    def acquire(self) -> _ConnAcquire:
        return _ConnAcquire(self._conn)


async def _seed_2_variant_experiment(
    conn,
    *,
    niche: str = _NICHE,
    status: str = "draft",
    key_suffix: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Insert one experiment + two variants. Returns
    ``(experiment_id, {label: variant_id})`` with both as strings.

    The status defaults to ``draft`` because that's the lifecycle's
    starting state; tests that want ``active`` pass ``status='active'``
    + the UPDATE landing later in the test would be redundant.
    """
    suffix = key_suffix or uuid.uuid4().hex[:8]
    activated_at_clause = "now()" if status == "active" else "NULL"
    exp_id = await conn.fetchval(
        f"""
        INSERT INTO experiments (key, niche_slug, status, activated_at)
        VALUES ($1, $2, $3, {activated_at_clause})
        RETURNING id
        """,
        f"{niche}/lifecycle-{suffix}",
        niche,
        status,
    )
    variant_ids: dict[str, str] = {}
    for label in ("arm-a", "arm-b"):
        vid = await conn.fetchval(
            """
            INSERT INTO experiment_variants
              (experiment_id, label, writer_model)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            exp_id, label, f"test-model-{label}:42b",
        )
        variant_ids[label] = str(vid)
    return str(exp_id), variant_ids


# ---------------------------------------------------------------------------
# 1. draft experiment is ignored by pick_variant.
#
# Even with active variants, a draft experiment must not serve traffic —
# the runner filters on ``experiments.status = 'active'`` AND
# ``experiment_variants.active = TRUE``, so a draft is invisible. The
# production path stays on niche defaults (the runner returns None).
# ---------------------------------------------------------------------------
async def test_draft_experiment_returns_none_from_pick_variant(test_txn) -> None:
    from services.experiment_runner import pick_variant

    _exp_id, _vids = await _seed_2_variant_experiment(test_txn, status="draft")
    pool = _ConnAsPool(test_txn)

    result = await pick_variant(pool, _NICHE, task_id="lifecycle-task-draft")
    assert result is None, (
        f"pick_variant returned {result!r} for a draft experiment — "
        "only status='active' rows must serve variants"
    )


# ---------------------------------------------------------------------------
# 2. Activating a draft flips pick_variant from None → ExperimentVariant.
#
# Pre-UPDATE: pick_variant returns None.
# UPDATE status='active', activated_at: pick_variant returns one of the variants.
# Same connection, same niche — only the row mutation changes behaviour.
# ---------------------------------------------------------------------------
async def test_activating_draft_makes_pick_variant_start_serving(
    test_txn,
) -> None:
    from services.experiment_runner import ExperimentVariant, pick_variant

    exp_id, variant_ids = await _seed_2_variant_experiment(
        test_txn, status="draft",
    )
    pool = _ConnAsPool(test_txn)

    # Pre-activation: no variant served.
    pre = await pick_variant(pool, _NICHE, task_id="lifecycle-task-pre")
    assert pre is None

    # Activate the experiment.
    await test_txn.execute(
        """
        UPDATE experiments
           SET status = 'active', activated_at = now()
         WHERE id = $1::uuid
        """,
        exp_id,
    )

    # Post-activation: one of the variants gets picked.
    post = await pick_variant(pool, _NICHE, task_id="lifecycle-task-post")
    assert isinstance(post, ExperimentVariant), (
        f"pick_variant returned {post!r} after activation — "
        "expected ExperimentVariant once status='active'"
    )
    assert post.variant_label in ("arm-a", "arm-b")
    assert post.variant_id in variant_ids.values()
    assert post.experiment_id == exp_id


# ---------------------------------------------------------------------------
# 3. Second active experiment for the same niche fails with a unique-constraint violation.
#
# The partial unique index ``idx_experiments_one_active_per_niche`` (PR
# #699) enforces "one active per niche" at the SQL layer. A deliberate
# attempt to flip a second experiment for the same niche to active must
# raise UniqueViolationError so the operator sees a clean error instead
# of silently producing two actives that fight over traffic.
# ---------------------------------------------------------------------------
async def test_second_active_experiment_for_same_niche_violates_unique_index(
    test_txn,
) -> None:
    # Two draft experiments on the same niche — both inserts succeed
    # (the partial index only fires for status='active').
    exp_a, _ = await _seed_2_variant_experiment(
        test_txn, status="draft", key_suffix="alpha",
    )
    exp_b, _ = await _seed_2_variant_experiment(
        test_txn, status="draft", key_suffix="beta",
    )

    # First → active is fine.
    await test_txn.execute(
        """
        UPDATE experiments
           SET status = 'active', activated_at = now()
         WHERE id = $1::uuid
        """,
        exp_a,
    )

    # Second → active must violate the partial unique index. The runner
    # never relies on application-layer "did anyone else activate
    # first?" checks — the SQL invariant is the contract.
    with pytest.raises(asyncpg.exceptions.UniqueViolationError) as excinfo:
        await test_txn.execute(
            """
            UPDATE experiments
               SET status = 'active', activated_at = now()
             WHERE id = $1::uuid
            """,
            exp_b,
        )
    # The error must mention the partial unique index by name so an
    # operator scanning logs knows which constraint fired.
    assert "idx_experiments_one_active_per_niche" in str(excinfo.value), (
        f"UniqueViolationError didn't reference the partial unique index "
        f"name: {excinfo.value!r}"
    )


# ---------------------------------------------------------------------------
# 4. Concluding an active experiment stops it from serving variants.
#
# Pre-conclude: pick_variant returns an ExperimentVariant.
# UPDATE status='concluded' + winner_variant_label + concluded_at:
#   pick_variant returns None — production traffic returns to defaults.
# Also asserts the winner_variant_label persists (PR 3 migration).
# ---------------------------------------------------------------------------
async def test_concluding_active_experiment_stops_serving_and_persists_winner(
    test_txn,
) -> None:
    from services.experiment_runner import ExperimentVariant, pick_variant

    exp_id, _vids = await _seed_2_variant_experiment(
        test_txn, status="active",
    )
    pool = _ConnAsPool(test_txn)

    pre = await pick_variant(pool, _NICHE, task_id="lifecycle-task-pre-conclude")
    assert isinstance(pre, ExperimentVariant)

    # Conclude with a winner pointer + free-text note (mirrors what the
    # PR 3 CLI's `poindexter experiments conclude` writes).
    await test_txn.execute(
        """
        UPDATE experiments
           SET status                 = 'concluded',
               concluded_at           = now(),
               winner_variant_label   = 'arm-a',
               conclusion_note        = 'arm-a won — test E2E'
         WHERE id = $1::uuid
        """,
        exp_id,
    )

    # Now no variant is served — production path returns None.
    post = await pick_variant(
        pool, _NICHE, task_id="lifecycle-task-post-conclude",
    )
    assert post is None, (
        f"pick_variant returned {post!r} for a concluded experiment — "
        "only status='active' rows must serve variants"
    )

    # The winner pointer + note + concluded_at all persisted in one
    # UPDATE — the CLI writes both columns atomically and they must
    # round-trip.
    row = await test_txn.fetchrow(
        """
        SELECT status, winner_variant_label, conclusion_note,
               concluded_at IS NOT NULL AS has_concluded_at
        FROM experiments
        WHERE id = $1::uuid
        """,
        exp_id,
    )
    assert row["status"] == "concluded"
    assert row["winner_variant_label"] == "arm-a"
    assert row["conclusion_note"] == "arm-a won — test E2E"
    assert row["has_concluded_at"] is True


# ---------------------------------------------------------------------------
# 5. Activating a new experiment on the same niche works once the old one is concluded.
#
# The partial unique index is filtered on ``WHERE status = 'active'`` —
# so a concluded row no longer counts toward the niche's active quota.
# This test composes scenario 3 + 4: concluding releases the slot, and
# the next activation must succeed without raising.
# ---------------------------------------------------------------------------
async def test_new_experiment_activates_after_old_one_concluded(
    test_txn,
) -> None:
    from services.experiment_runner import ExperimentVariant, pick_variant

    # Old experiment — straight to active.
    old_id, _ = await _seed_2_variant_experiment(
        test_txn, status="active", key_suffix="old",
    )
    # New experiment — draft until the old one is concluded.
    new_id, new_variant_ids = await _seed_2_variant_experiment(
        test_txn, status="draft", key_suffix="new",
    )
    pool = _ConnAsPool(test_txn)

    # Sanity: while the old one is still active, the new one can't be
    # promoted — that's the scenario 3 contract. We wrap the failing
    # UPDATE in a SAVEPOINT so the outer test_txn transaction stays
    # usable; without the savepoint, Postgres aborts the outer txn on
    # the constraint violation and every subsequent statement fails
    # with "current transaction is aborted, commands ignored until end
    # of transaction block".
    sp = test_txn.transaction()
    await sp.start()
    try:
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await test_txn.execute(
                """
                UPDATE experiments
                   SET status = 'active', activated_at = now()
                 WHERE id = $1::uuid
                """,
                new_id,
            )
    finally:
        await sp.rollback()

    # Conclude the old one — slot frees up.
    await test_txn.execute(
        """
        UPDATE experiments
           SET status               = 'concluded',
               concluded_at         = now(),
               winner_variant_label = 'arm-a'
         WHERE id = $1::uuid
        """,
        old_id,
    )

    # Now the new one can be promoted — slot is free.
    await test_txn.execute(
        """
        UPDATE experiments
           SET status = 'active', activated_at = now()
         WHERE id = $1::uuid
        """,
        new_id,
    )

    # And the runner serves THE NEW experiment's variants (not the old
    # concluded one's). The old experiment's variants are still in the
    # table — the filter on e.status='active' is what hides them.
    result = await pick_variant(
        pool, _NICHE, task_id="lifecycle-task-new-active",
    )
    assert isinstance(result, ExperimentVariant)
    assert result.experiment_id == new_id, (
        f"pick_variant returned experiment_id={result.experiment_id!r}, "
        f"expected the freshly-activated new experiment id {new_id!r} "
        "(old experiment is concluded but its variants must not leak)"
    )
    assert result.variant_id in new_variant_ids.values()
