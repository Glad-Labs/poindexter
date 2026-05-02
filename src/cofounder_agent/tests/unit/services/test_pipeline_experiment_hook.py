"""Unit tests for ``services/pipeline_experiment_hook.py``.

Wired via ``content_router_service.process_content_generation_task`` —
when ``app_settings.active_pipeline_experiment_key`` is set, the hook
assigns the task to a variant and threads the assignment through to
finalize (which calls record_pipeline_outcome).

Coverage:

- No-op when no experiment is active (the common case).
- No-op when database_service is None.
- Assigns the task and applies ``writer_model`` override into
  ``models_by_phase``.
- Doesn't trample an explicit ``models_by_phase["writer"]`` set by the
  API caller.
- ``record_pipeline_outcome`` is a no-op when assignment carries no
  experiment_key.
- ``record_pipeline_outcome`` writes metrics into the assignment row.

Tests use the ``db_pool`` fixture (real Postgres against a disposable
test database that the conftest creates per session). We avoid the
row-faker pattern per Glad-Labs/poindexter#27's "don't hand-roll
asyncpg row mocks" guidance — a parallel agent is migrating other
suites away from that pattern.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from services.pipeline_experiment_hook import (
    assign_pipeline_variant,
    record_pipeline_outcome,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _StubDatabaseService:
    """Mimic ``DatabaseService`` for the hook — only ``.pool`` is read."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool


class _StubSiteConfig:
    """The hook only passes site_config through to ExperimentService.
    The service doesn't currently consult it, so a no-op stub is fine.
    """

    def get(self, key: str, default: Any = None) -> Any:
        return default


async def _wipe_experiments(pool):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM experiment_assignments")
        await conn.execute("DELETE FROM experiments")
        await conn.execute(
            "DELETE FROM app_settings WHERE key = 'active_pipeline_experiment_key'"
        )


async def _set_active_experiment(pool, key: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
            VALUES ($1, $2, 'experiments', 'test seed', FALSE, TRUE)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, is_active = TRUE
            """,
            "active_pipeline_experiment_key", key,
        )


async def _create_running_experiment(
    pool,
    *,
    key: str,
    variants: list[dict[str, Any]],
) -> str:
    """Insert a running experiment row directly. Returns the new id."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO experiments
                (key, description, status, variants, assignment_field, started_at)
            VALUES ($1, $2, 'running', $3::jsonb, 'task_id', NOW())
            RETURNING id::text AS id
            """,
            key, f"test experiment {key}", json.dumps(variants),
        )
    return str(row["id"])


# ---------------------------------------------------------------------------
# assign_pipeline_variant
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio(loop_scope="session")
class TestAssignPipelineVariant:
    async def test_no_op_when_no_database_service(self):
        models: dict[str, str] = {}
        result = await assign_pipeline_variant(
            task_id="task-1",
            database_service=None,
            site_config=_StubSiteConfig(),
            models_by_phase=models,
        )
        assert result == {"experiment_key": None, "variant_key": None}
        assert models == {}

    async def test_no_op_when_no_task_id(self, db_pool):
        await _wipe_experiments(db_pool)
        models: dict[str, str] = {}
        result = await assign_pipeline_variant(
            task_id="",
            database_service=_StubDatabaseService(db_pool),
            site_config=_StubSiteConfig(),
            models_by_phase=models,
        )
        assert result == {"experiment_key": None, "variant_key": None}
        assert models == {}

    async def test_no_op_when_setting_unset(self, db_pool):
        await _wipe_experiments(db_pool)
        models: dict[str, str] = {}
        result = await assign_pipeline_variant(
            task_id="task-2",
            database_service=_StubDatabaseService(db_pool),
            site_config=_StubSiteConfig(),
            models_by_phase=models,
        )
        assert result["experiment_key"] is None

    async def test_no_op_when_setting_blank(self, db_pool):
        await _wipe_experiments(db_pool)
        await _set_active_experiment(db_pool, "")
        models: dict[str, str] = {}
        result = await assign_pipeline_variant(
            task_id="task-3",
            database_service=_StubDatabaseService(db_pool),
            site_config=_StubSiteConfig(),
            models_by_phase=models,
        )
        assert result == {"experiment_key": None, "variant_key": None}

    async def test_assigns_variant_when_experiment_running(self, db_pool):
        await _wipe_experiments(db_pool)
        await _create_running_experiment(
            db_pool,
            key="writer_test_run",
            variants=[
                {"key": "control", "weight": 50, "config": {}},
                {"key": "fast", "weight": 50, "config": {"writer_model": "tiny:1b"}},
            ],
        )
        await _set_active_experiment(db_pool, "writer_test_run")

        models: dict[str, str] = {}
        result = await assign_pipeline_variant(
            task_id="task-deterministic-1",
            database_service=_StubDatabaseService(db_pool),
            site_config=_StubSiteConfig(),
            models_by_phase=models,
        )
        assert result["experiment_key"] == "writer_test_run"
        assert result["variant_key"] in {"control", "fast"}

        # If we got the "fast" arm, the writer override should be applied.
        if result["variant_key"] == "fast":
            assert models.get("writer") == "tiny:1b"
        else:
            assert "writer" not in models

    async def test_does_not_trample_explicit_writer_override(self, db_pool):
        """If the API caller already pinned a writer model, the experiment
        must not overwrite it — the explicit override wins.
        """
        await _wipe_experiments(db_pool)
        await _create_running_experiment(
            db_pool,
            key="dont_trample_test",
            variants=[
                # 100/0 split so the variant_key is deterministic.
                {"key": "fast", "weight": 99, "config": {"writer_model": "tiny:1b"}},
                {"key": "control", "weight": 1, "config": {}},
            ],
        )
        await _set_active_experiment(db_pool, "dont_trample_test")

        # Pre-set models["writer"] — this must survive.
        models: dict[str, str] = {"writer": "explicit:99b"}
        await assign_pipeline_variant(
            task_id="task-explicit-1",
            database_service=_StubDatabaseService(db_pool),
            site_config=_StubSiteConfig(),
            models_by_phase=models,
        )
        assert models["writer"] == "explicit:99b"

    async def test_assignment_is_sticky(self, db_pool):
        """Same subject → same variant on repeated assign() calls."""
        await _wipe_experiments(db_pool)
        await _create_running_experiment(
            db_pool,
            key="sticky_test",
            variants=[
                {"key": "a", "weight": 50, "config": {}},
                {"key": "b", "weight": 50, "config": {}},
            ],
        )
        await _set_active_experiment(db_pool, "sticky_test")

        # Two assigns with the same task_id must return the same variant.
        first = await assign_pipeline_variant(
            task_id="sticky-task",
            database_service=_StubDatabaseService(db_pool),
            site_config=_StubSiteConfig(),
            models_by_phase={},
        )
        second = await assign_pipeline_variant(
            task_id="sticky-task",
            database_service=_StubDatabaseService(db_pool),
            site_config=_StubSiteConfig(),
            models_by_phase={},
        )
        assert first["variant_key"] == second["variant_key"]


# ---------------------------------------------------------------------------
# record_pipeline_outcome
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio(loop_scope="session")
class TestRecordPipelineOutcome:
    async def test_no_op_when_assignment_empty(self):
        # No DB call should happen — and the helper should never raise.
        await record_pipeline_outcome(
            assignment={},
            task_id="t1",
            database_service=None,
            site_config=_StubSiteConfig(),
            metrics={"score": 90.0},
        )

    async def test_no_op_when_experiment_key_missing(self):
        await record_pipeline_outcome(
            assignment={"experiment_key": None, "variant_key": None},
            task_id="t1",
            database_service=None,
            site_config=_StubSiteConfig(),
            metrics={"score": 90.0},
        )

    async def test_writes_metrics_to_assignment_row(self, db_pool):
        await _wipe_experiments(db_pool)
        await _create_running_experiment(
            db_pool,
            key="outcome_test",
            variants=[
                {"key": "only", "weight": 99, "config": {}},
                {"key": "other", "weight": 1, "config": {}},
            ],
        )
        await _set_active_experiment(db_pool, "outcome_test")

        # Assign first so there's a row to update.
        result = await assign_pipeline_variant(
            task_id="outcome-task",
            database_service=_StubDatabaseService(db_pool),
            site_config=_StubSiteConfig(),
            models_by_phase={},
        )
        assert result["experiment_key"] == "outcome_test"

        await record_pipeline_outcome(
            assignment=result,
            task_id="outcome-task",
            database_service=_StubDatabaseService(db_pool),
            site_config=_StubSiteConfig(),
            metrics={"quality_score": 88.5, "status": "success"},
        )

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT a.metrics
                  FROM experiment_assignments a
                  JOIN experiments e ON e.id = a.experiment_id
                 WHERE e.key = $1 AND a.subject_id = $2
                """,
                "outcome_test", "outcome-task",
            )
        assert row is not None
        # asyncpg may return jsonb as str or as already-decoded dict.
        metrics = row["metrics"]
        if isinstance(metrics, str):
            metrics = json.loads(metrics)
        assert metrics["quality_score"] == 88.5
        assert metrics["status"] == "success"
