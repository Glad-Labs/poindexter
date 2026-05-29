"""Phase 1 PR 5 — happy-path E2E for a two-variant experiment.

Closes the variant-experiments harness with full-stack coverage on top of the
schema (PR #699), runner + writer-atom hook (PR #702), CLI + winner-label
migration (PR #706), and Grafana panels (PR 4). This file exercises the whole
read/write loop against a real disposable Postgres:

1. Create a 2-variant active experiment on niche ``test-niche-e2e``.
2. Drive ``services.experiment_runner.pick_variant`` 200 times against it
   with a seeded ``random.Random`` (monkeypatched onto the runner's
   ``random.choice`` reference) so the test is deterministic. Assert the
   split is within ±5% of the 50/50 target — the runner promises uniform
   random over active variants and we want a flake-free pin on that
   contract.
3. For every pick, simulate the recorder by writing a
   ``capability_outcomes`` row stamped with the chosen ``variant_id``
   (matches what the writer-atom hook from PR 2 does in production via
   ``services.capability_outcomes.record_one``). Assert each row carries
   one of the two variants' ids.
4. Read ``lab_outcomes_v1`` filtered to our task_ids and assert every
   row has its ``variant_label`` + ``experiment_key`` populated — the
   LEFT JOIN chain PR 1 added must surface the experiment context inline.
5. Query ``experiment_variant_scorecard_v1`` for our experiment and
   assert the rollups match the seeded outcome distribution:
   - posts_attempted per variant ≈ pick count (within ±10),
   - posts_approved per variant matches the seeded approval distribution
     (we approve 60% of A's runs and 40% of B's via
     ``published_post_edit_metrics`` inserts),
   - approval_rate_pct reflects that within ±5pp,
   - avg_cost_per_post averages out to the seeded ``routing_outcomes``
     actual_cost values.

This pins the *behaviour* the operator reads off the scorecard. The
narrower unit tests in ``tests/unit/services/test_experiment_runner.py``
+ the migration-level integration tests in
``test_phase1_experiments_foundation.py`` + ``test_phase1_experiment_runner.py``
remain in force; this file just guarantees they compose end-to-end.

Run with:

    cd src/cofounder_agent && \\
      poetry run pytest tests/integration_db/test_phase1_two_variant_experiment_e2e.py -v

The fixtures come from ``tests/integration_db/conftest.py``; the whole
tier skips cleanly when no live Postgres is reachable.

Each test uses ``test_txn`` so every INSERT rolls back at teardown — no
cross-test leakage. Hard rule: no operator info in seeded data
(``niche='test-niche-e2e'``, fake variant labels, synthetic task ids).
"""

from __future__ import annotations

import random as _stdrandom
import uuid
from datetime import datetime, timezone

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


# Total invocations across the 2 variants. 200 is the value the task
# spec picked — large enough that ~50/50 sampling is tight under a
# fair coin (stddev ≈ 7.07 for binomial(n=200, p=0.5)) but small enough
# that the test stays fast against a single asyncpg connection in a
# transaction. With a deterministic seed (see _SAMPLING_SEED below) the
# split is reproducible at runtime so the ±5% tolerance never flakes.
_TOTAL_PICKS = 200
# Seed picked so the 200 picks land in a fair-ish split — verified
# locally to come out within the ±5% tolerance band. Hard-coding this
# is the difference between a "natural randomness, hope it's fair" test
# (flake risk) and an actual contract pin per
# ``feedback_no_flaky_tests``.
_SAMPLING_SEED = 20260529
# Sampling tolerance — ±5% of 50% per the task spec. With n=200 and a
# deterministic seed the split is exact; the tolerance just guards
# against a future change to the seed accidentally drifting the split.
_SAMPLING_TOLERANCE_PCT = 5.0


# ---------------------------------------------------------------------------
# Helper — bridge a single asyncpg.Connection (from test_txn) to the
# pool.acquire() shape pick_variant uses. Same trick as
# ``test_phase1_experiment_runner.py`` to avoid spinning up a real pool
# inside an already-open transaction.
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


# ---------------------------------------------------------------------------
# Helper — seed a 2-variant active experiment + return (exp_id, {label: vid}).
#
# The labels are 'arm-a' / 'arm-b' (not 'A'/'B') to make the scorecard
# rows easy to grep in test failures without ambiguating against real
# operator-curated experiments that often use 'A'/'B'.
# ---------------------------------------------------------------------------
async def _seed_active_2_variant_experiment(
    conn,
    *,
    niche: str = "test-niche-e2e",
) -> tuple[str, dict[str, str]]:
    exp_id = await conn.fetchval(
        """
        INSERT INTO experiments (key, niche_slug, status, activated_at)
        VALUES ($1, $2, 'active', now())
        RETURNING id
        """,
        f"{niche}/e2e-{uuid.uuid4().hex[:8]}",
        niche,
    )
    variant_ids: dict[str, str] = {}
    for label, model in (
        ("arm-a", "test-model-a:42b"),
        ("arm-b", "test-model-b:42b"),
    ):
        vid = await conn.fetchval(
            """
            INSERT INTO experiment_variants
              (experiment_id, label, writer_model)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            exp_id, label, model,
        )
        variant_ids[label] = str(vid)
    return str(exp_id), variant_ids


# ---------------------------------------------------------------------------
# 1. 200 picks land ~50/50 between the two variants.
#
# The runner uses ``random.choice`` on the module-level ``random``. We
# monkeypatch ``services.experiment_runner.random`` with a seeded
# ``random.Random`` so the picks are deterministic. Asserts:
#   - exactly _TOTAL_PICKS variants returned (no None — active exp + active variants),
#   - both arms picked,
#   - each arm within ±5% of 50%.
# ---------------------------------------------------------------------------
async def test_two_variant_sampling_is_uniform_within_tolerance(
    test_txn, monkeypatch,
) -> None:
    from services import experiment_runner
    from services.experiment_runner import pick_variant

    _exp_id, variant_ids = await _seed_active_2_variant_experiment(test_txn)
    pool = _ConnAsPool(test_txn)

    # Inject a seeded RNG so the 200 picks are reproducible — no flakes.
    seeded = _stdrandom.Random(_SAMPLING_SEED)
    monkeypatch.setattr(experiment_runner, "random", seeded)

    counts: dict[str, int] = {"arm-a": 0, "arm-b": 0}
    for i in range(_TOTAL_PICKS):
        result = await pick_variant(
            pool, "test-niche-e2e", task_id=f"e2e-task-{i:03d}",
        )
        assert result is not None, (
            f"pick_variant returned None on pick {i} — "
            "active experiment + active variants should always assign"
        )
        assert result.variant_label in counts, (
            f"pick_variant returned unexpected label {result.variant_label!r} "
            f"(expected arm-a or arm-b)"
        )
        assert result.variant_id == variant_ids[result.variant_label]
        counts[result.variant_label] += 1

    # Both arms must be picked at least once.
    assert counts["arm-a"] > 0, "arm-a never picked across 200 invocations"
    assert counts["arm-b"] > 0, "arm-b never picked across 200 invocations"
    # Total adds up.
    assert counts["arm-a"] + counts["arm-b"] == _TOTAL_PICKS

    # ±5% of 50% → arm-a between 90 and 110, arm-b between 90 and 110.
    pct_a = 100.0 * counts["arm-a"] / _TOTAL_PICKS
    pct_b = 100.0 * counts["arm-b"] / _TOTAL_PICKS
    assert abs(pct_a - 50.0) <= _SAMPLING_TOLERANCE_PCT, (
        f"arm-a allocation {pct_a:.1f}% is outside the ±{_SAMPLING_TOLERANCE_PCT}% "
        f"tolerance band around 50% (raw count: {counts['arm-a']}/{_TOTAL_PICKS}; "
        f"seed={_SAMPLING_SEED})"
    )
    assert abs(pct_b - 50.0) <= _SAMPLING_TOLERANCE_PCT, (
        f"arm-b allocation {pct_b:.1f}% is outside the ±{_SAMPLING_TOLERANCE_PCT}% "
        f"tolerance band around 50% (raw count: {counts['arm-b']}/{_TOTAL_PICKS}; "
        f"seed={_SAMPLING_SEED})"
    )


# ---------------------------------------------------------------------------
# 2 + 3. capability_outcomes rows carry the right variant_id, and
#         lab_outcomes_v1 surfaces the variant + experiment context for
#         every one of them.
#
# Drives 200 picks + writes one capability_outcomes row per pick stamped
# with the chosen variant_id (simulates what the writer-atom hook +
# ``capability_outcomes.record_one`` do in production). Then reads back
# through ``lab_outcomes_v1`` to prove the LEFT JOINs surface the
# variant_label + experiment_key on every row.
# ---------------------------------------------------------------------------
async def test_capability_outcomes_and_lab_view_propagate_variant_id(
    test_txn, monkeypatch,
) -> None:
    from services import experiment_runner
    from services.experiment_runner import pick_variant

    exp_id, variant_ids = await _seed_active_2_variant_experiment(test_txn)
    pool = _ConnAsPool(test_txn)

    seeded = _stdrandom.Random(_SAMPLING_SEED)
    monkeypatch.setattr(experiment_runner, "random", seeded)

    # Tag every row with a unique run prefix so the SELECTs below can
    # filter to just THIS test's rows without seeing leaks from any
    # other concurrently-running test (test_txn rollback covers
    # cleanup, but the read assertions still need the filter to be
    # precise — txn isolation is enough but explicit is safer).
    run_prefix = f"e2e-{uuid.uuid4().hex[:8]}"
    task_ids: list[tuple[str, str]] = []  # (task_id, expected_variant_id)

    for i in range(_TOTAL_PICKS):
        result = await pick_variant(
            pool, "test-niche-e2e", task_id=f"{run_prefix}-{i:03d}",
        )
        assert result is not None
        task_id = f"{run_prefix}-{i:03d}"
        task_ids.append((task_id, result.variant_id))

        await test_txn.execute(
            """
            INSERT INTO capability_outcomes
              (task_id, template_slug, node_name, ok, variant_id,
               niche_slug, model_used)
            VALUES ($1, 'canonical_blog', 'generate_content',
                    TRUE, $2, 'test-niche-e2e', $3)
            """,
            task_id,
            result.variant_id,
            result.writer_model,
        )

    # ---- Assertion 1 — capability_outcomes rows carry the right variant_id ----
    rows = await test_txn.fetch(
        """
        SELECT task_id, variant_id::text AS variant_id
        FROM capability_outcomes
        WHERE task_id LIKE $1
        ORDER BY task_id
        """,
        f"{run_prefix}-%",
    )
    assert len(rows) == _TOTAL_PICKS, (
        f"expected {_TOTAL_PICKS} capability_outcomes rows for run "
        f"{run_prefix!r}; got {len(rows)}"
    )
    valid_variants = set(variant_ids.values())
    by_task = {r["task_id"]: r["variant_id"] for r in rows}
    for task_id, expected_vid in task_ids:
        assert task_id in by_task, (
            f"capability_outcomes row missing for task {task_id!r}"
        )
        assert by_task[task_id] == expected_vid, (
            f"capability_outcomes.variant_id mismatch for {task_id!r}: "
            f"expected {expected_vid!r}, got {by_task[task_id]!r}"
        )
        assert by_task[task_id] in valid_variants, (
            f"capability_outcomes.variant_id {by_task[task_id]!r} is not one "
            f"of the seeded variant ids {valid_variants}"
        )

    # ---- Assertion 2 — lab_outcomes_v1 surfaces variant_label + experiment_key ----
    view_rows = await test_txn.fetch(
        """
        SELECT task_id, variant_label, experiment_key, experiment_status,
               experiment_objective_function
        FROM lab_outcomes_v1
        WHERE task_id LIKE $1
        """,
        f"{run_prefix}-%",
    )
    assert len(view_rows) == _TOTAL_PICKS, (
        f"lab_outcomes_v1 dropped some rows — expected {_TOTAL_PICKS}, got {len(view_rows)}"
    )
    valid_labels = {"arm-a", "arm-b"}
    for r in view_rows:
        assert r["variant_label"] in valid_labels, (
            f"lab_outcomes_v1 row for {r['task_id']!r} has unexpected "
            f"variant_label {r['variant_label']!r}"
        )
        assert r["experiment_key"], (
            f"lab_outcomes_v1 row for {r['task_id']!r} missing experiment_key — "
            "LEFT JOIN to experiments table broken"
        )
        assert r["experiment_key"].startswith("test-niche-e2e/"), (
            f"experiment_key {r['experiment_key']!r} doesn't match the seeded "
            "test-niche-e2e prefix — wrong experiment row joined"
        )
        assert r["experiment_status"] == "active"
        # PR #699 default — proves the experiments row was actually joined.
        assert r["experiment_objective_function"] == "views_7d"


# ---------------------------------------------------------------------------
# 4. Scorecard correctness — seeded approval + cost distribution shows up.
#
# Drives 200 picks, writes capability_outcomes + routing_outcomes +
# published_post_edit_metrics rows so the scorecard view has data to
# roll up. Then asserts the view's aggregates match the seeded inputs
# within tight tolerances.
#
# Seed plan:
#   - 60% of arm-a picks → approved (write a published_post_edit_metrics
#     row with approver='test-operator')
#   - 40% of arm-b picks → approved
#   - every pick has a routing_outcomes row with actual_cost=0.01
#     (so avg_cost_per_post is exactly 0.01 for each variant)
#
# Assertions:
#   - posts_attempted per variant matches the actual pick count for that variant,
#   - posts_approved per variant matches the seeded approval count
#     (computed from the actual pick counts × the per-arm approval rate),
#   - approval_rate_pct matches within ±2pp,
#   - avg_cost_per_post is 0.01 within numeric tolerance.
# ---------------------------------------------------------------------------
async def test_scorecard_correctness_for_seeded_outcome_distribution(
    test_txn, monkeypatch,
) -> None:
    from services import experiment_runner
    from services.experiment_runner import pick_variant

    exp_uuid, variant_ids = await _seed_active_2_variant_experiment(test_txn)
    pool = _ConnAsPool(test_txn)

    seeded = _stdrandom.Random(_SAMPLING_SEED)
    monkeypatch.setattr(experiment_runner, "random", seeded)

    # Seeded approval rate per arm — documented for readers; the actual
    # approval decision below uses ``position_in_arm`` modular arithmetic
    # (3-of-5 = 60% for arm-a, 2-of-5 = 40% for arm-b) so the final
    # approval count is exact regardless of which arm a given pick
    # lands on. Per the design doc, the scorecard is the surface the
    # operator reads to decide which variant to ship — so the
    # approval-rate assertion is the load-bearing one.
    # Target approval rates: arm-a → 60%, arm-b → 40%.
    actual_cost_per_post = 0.0125  # arbitrary but distinct from any default

    run_prefix = f"score-{uuid.uuid4().hex[:8]}"
    # Track per-arm pick counts AND the per-arm approval counts so the
    # assertions below can compute the expected scorecard values
    # exactly (no "approximately N approves" — we know exactly which
    # rows got an approver).
    arm_picks: dict[str, list[str]] = {"arm-a": [], "arm-b": []}
    approved_count: dict[str, int] = {"arm-a": 0, "arm-b": 0}

    for i in range(_TOTAL_PICKS):
        task_id = f"{run_prefix}-{i:03d}"
        result = await pick_variant(
            pool, "test-niche-e2e", task_id=task_id,
        )
        assert result is not None
        arm = result.variant_label
        arm_picks[arm].append(task_id)

        # capability_outcomes — the writer atom's row
        await test_txn.execute(
            """
            INSERT INTO capability_outcomes
              (task_id, template_slug, node_name, ok, variant_id,
               niche_slug, model_used, quality_score, elapsed_ms)
            VALUES ($1, 'canonical_blog', 'generate_content',
                    TRUE, $2, 'test-niche-e2e', $3, 85.0, 1500)
            """,
            task_id, result.variant_id, result.writer_model,
        )
        # routing_outcomes — the dispatcher row (where the cost lives)
        await test_txn.execute(
            """
            INSERT INTO routing_outcomes
              (task_id, task_type, task_category, worker_id, model_used,
               compute_tier, estimated_cost, actual_cost, quality_score,
               duration_ms, success, niche_slug)
            VALUES ($1, 'content_generation', 'tech', 'test-worker',
                    $2, 'standard', $3, $3, 85.0, 1500, TRUE,
                    'test-niche-e2e')
            """,
            task_id, result.writer_model, actual_cost_per_post,
        )

        # Approve ``target_approval_pct[arm]`` percent of this arm's picks.
        # We approve the first N picks of the arm so the math is exact:
        # for arm-a with 60% target, the first int(0.6 * pick_count)
        # picks get approved. Using a per-arm index keeps this
        # independent of which arm the run lands on next.
        position_in_arm = len(arm_picks[arm])
        # We DON'T know the final pick count for this arm yet — so we
        # decide approval the simple way: every pick whose
        # ``position_in_arm`` falls under ``target_approval_pct[arm] / 100``
        # of the running pick count. That's not exact but it's stable.
        # Better: approve based on position % 5 < target/20 — gives an
        # exact rate without needing to know the final count. For 60%:
        # position % 5 in {0,1,2} (3/5 = 60%). For 40%: position % 5
        # in {0,1} (2/5 = 40%).
        if arm == "arm-a":
            should_approve = (position_in_arm - 1) % 5 < 3
        else:  # arm-b
            should_approve = (position_in_arm - 1) % 5 < 2
        if should_approve:
            approved_count[arm] += 1
            await test_txn.execute(
                """
                INSERT INTO published_post_edit_metrics
                  (task_id, niche_slug, category, approver,
                   pre_approve_hash, post_approve_hash,
                   char_diff_count, line_diff_count,
                   pre_approve_len, post_approve_len,
                   approve_method, approved_at, model_used,
                   prompt_template_key, prompt_template_version)
                VALUES ($1, 'test-niche-e2e', 'test', 'test-operator',
                        'pre-stub', 'post-stub',
                        10, 1, 1000, 1010,
                        'manual', $2, $3, NULL, NULL)
                """,
                task_id, datetime.now(timezone.utc), result.writer_model,
            )

    # ---- Read the scorecard ----
    rows = {
        r["variant_label"]: r
        for r in await test_txn.fetch(
            """
            SELECT variant_label, posts_attempted, posts_approved,
                   approval_rate_pct, avg_cost_per_post, total_cost
            FROM experiment_variant_scorecard_v1
            WHERE experiment_id = $1::uuid
            ORDER BY variant_label
            """,
            exp_uuid,
        )
    }
    assert set(rows) == {"arm-a", "arm-b"}, (
        f"scorecard should return both variants; got {set(rows)}"
    )

    for arm in ("arm-a", "arm-b"):
        expected_attempts = len(arm_picks[arm])
        expected_approves = approved_count[arm]
        actual_attempts = rows[arm]["posts_attempted"]
        actual_approves = rows[arm]["posts_approved"]
        assert actual_attempts == expected_attempts, (
            f"{arm}: scorecard posts_attempted={actual_attempts}, expected "
            f"{expected_attempts} (from {len(arm_picks[arm])} pick_variant calls)"
        )
        assert actual_approves == expected_approves, (
            f"{arm}: scorecard posts_approved={actual_approves}, expected "
            f"{expected_approves} (seeded {expected_approves}/{expected_attempts} "
            "approves)"
        )
        # approval_rate_pct — within ±2pp of the per-arm exact rate.
        actual_rate = float(rows[arm]["approval_rate_pct"])
        expected_rate = 100.0 * expected_approves / expected_attempts
        assert abs(actual_rate - expected_rate) <= 2.0, (
            f"{arm}: scorecard approval_rate_pct={actual_rate:.1f}, expected "
            f"~{expected_rate:.1f} (±2pp) given {expected_approves}/"
            f"{expected_attempts} approves"
        )
        # avg_cost_per_post — exactly the seeded value.
        actual_cost = float(rows[arm]["avg_cost_per_post"])
        assert abs(actual_cost - actual_cost_per_post) < 1e-6, (
            f"{arm}: scorecard avg_cost_per_post={actual_cost}, "
            f"expected ≈{actual_cost_per_post} (every routing_outcomes row was "
            "seeded with this exact cost)"
        )
        # total_cost — pick_count × per-post cost.
        actual_total = float(rows[arm]["total_cost"])
        expected_total = expected_attempts * actual_cost_per_post
        assert abs(actual_total - expected_total) < 1e-6, (
            f"{arm}: scorecard total_cost={actual_total}, expected "
            f"≈{expected_total} ({expected_attempts}×{actual_cost_per_post})"
        )
