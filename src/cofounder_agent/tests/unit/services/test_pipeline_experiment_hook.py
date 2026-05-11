"""Unit tests for ``services/pipeline_experiment_hook.py``.

Wired via ``content_router_service.process_content_generation_task`` —
when ``app_settings.active_pipeline_experiment_key`` is set, the hook
assigns the task to a variant and threads the assignment through to
finalize (which calls ``record_pipeline_outcome``).

Rewritten 2026-05-11 after migration
``20260510_065631_drop_experiments_tables.py`` dropped the SQL-backed
``experiments`` / ``experiment_assignments`` tables. The A/B harness now
talks to Langfuse via ``services.langfuse_experiments``; these tests
mock that service at the import boundary the hook uses — no real
Postgres, no Langfuse server.

Contract pinned per test:

- ``assign_pipeline_variant`` no-ops cleanly without DB / task_id /
  active-setting / running experiment, propagates service failures as
  no-ops (error isolation), forwards the right kwargs, merges
  ``variant.config['writer_model']`` into ``models_by_phase`` only when
  not already pinned, and honours service-side stickiness.
- ``record_pipeline_outcome`` no-ops on empty/missing assignment,
  forwards the right kwargs on the happy path, and swallows service
  exceptions so finalize never crashes on a Langfuse blip.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.pipeline_experiment_hook import (
    assign_pipeline_variant,
    record_pipeline_outcome,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _stub_database_service(active_experiment_key: Any) -> SimpleNamespace:
    """Duck-typed ``database_service``. ``.pool.acquire()`` yields a conn
    whose ``.fetchval(...)`` returns ``active_experiment_key`` — what
    ``SELECT value FROM app_settings WHERE key=$1 AND is_active=TRUE``
    would yield. Pass ``None`` for the "setting unset" case."""
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=active_experiment_key)
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return SimpleNamespace(pool=pool)


def _stub_db_with_failing_pool() -> SimpleNamespace:
    """``database_service`` whose ``pool.acquire().__aenter__`` raises —
    simulates a transient pool failure mid-pipeline."""
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("pool down"))
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return SimpleNamespace(pool=pool)


class _StubSiteConfig:
    """The hook passes ``site_config`` straight through to the (mocked)
    Langfuse service — no contract to satisfy here."""

    def get(self, key: str, default: Any = None) -> Any:
        return default


def _make_mocked_service(
    *,
    assign_return: str | None = None,
    assign_side_effect: Exception | None = None,
    record_outcome_side_effect: Exception | None = None,
    dataset_variants: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Stand-in for ``LangfuseExperimentService(...)``.

    Mocks the three call sites the hook touches:
      - ``await svc.assign(experiment_key=, subject_id=)`` → variant_key
      - ``await svc.record_outcome(experiment_key=, subject_id=, metrics=)``
      - ``svc._get_client().get_dataset(experiment_key).metadata['variants']``
        — read by ``_get_variant_config`` to look up the variant config.
    """
    svc = MagicMock()
    if assign_side_effect is not None:
        svc.assign = AsyncMock(side_effect=assign_side_effect)
    else:
        svc.assign = AsyncMock(return_value=assign_return)
    if record_outcome_side_effect is not None:
        svc.record_outcome = AsyncMock(side_effect=record_outcome_side_effect)
    else:
        svc.record_outcome = AsyncMock(return_value=True)

    dataset = SimpleNamespace(metadata={"variants": dataset_variants or []})
    client = MagicMock()
    client.get_dataset = MagicMock(return_value=dataset)
    svc._get_client = MagicMock(return_value=client)
    return svc


_LF_PATCH_TARGET = "services.langfuse_experiments.LangfuseExperimentService"


# ---------------------------------------------------------------------------
# assign_pipeline_variant
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio(loop_scope="session")
class TestAssignPipelineVariant:
    async def test_no_op_when_no_database_service(self):
        """No DB handle = inert hook; models_by_phase untouched."""
        models: dict[str, str] = {}
        result = await assign_pipeline_variant(
            task_id="task-1",
            database_service=None,
            site_config=_StubSiteConfig(),
            models_by_phase=models,
        )
        assert result == {"experiment_key": None, "variant_key": None}
        assert models == {}

    async def test_no_op_when_no_task_id(self):
        """Empty task_id = no subject; setting lookup must be skipped."""
        db = _stub_database_service(active_experiment_key="should-not-be-read")
        models: dict[str, str] = {}
        result = await assign_pipeline_variant(
            task_id="",
            database_service=db,
            site_config=_StubSiteConfig(),
            models_by_phase=models,
        )
        assert result == {"experiment_key": None, "variant_key": None}
        assert models == {}
        db.pool.acquire.assert_not_called()

    async def test_no_op_when_setting_unset(self):
        """Setting unset (fetchval=None) = no experiment active;
        LangfuseExperimentService must NOT be constructed."""
        db = _stub_database_service(active_experiment_key=None)
        models: dict[str, str] = {}
        with patch(_LF_PATCH_TARGET) as svc_ctor:
            result = await assign_pipeline_variant(
                task_id="task-2",
                database_service=db,
                site_config=_StubSiteConfig(),
                models_by_phase=models,
            )
        assert result == {"experiment_key": None, "variant_key": None}
        assert models == {}
        svc_ctor.assert_not_called()

    async def test_no_op_when_setting_blank(self):
        """Whitespace-only setting = treated as unset (operator typed
        a space into the admin UI)."""
        db = _stub_database_service(active_experiment_key="   ")
        models: dict[str, str] = {}
        with patch(_LF_PATCH_TARGET) as svc_ctor:
            result = await assign_pipeline_variant(
                task_id="task-3",
                database_service=db,
                site_config=_StubSiteConfig(),
                models_by_phase=models,
            )
        assert result == {"experiment_key": None, "variant_key": None}
        assert models == {}
        svc_ctor.assert_not_called()

    async def test_assigns_variant_when_experiment_running(self):
        """Happy path: setting set → service returns variant_key →
        variant.config['writer_model'] flows into models_by_phase."""
        db = _stub_database_service(active_experiment_key="writer_test_run")
        mocked_svc = _make_mocked_service(
            assign_return="fast",
            dataset_variants=[
                {"key": "control", "weight": 50, "config": {}},
                {"key": "fast", "weight": 50, "config": {"writer_model": "tiny:1b"}},
            ],
        )
        models: dict[str, str] = {}
        with patch(_LF_PATCH_TARGET, return_value=mocked_svc):
            result = await assign_pipeline_variant(
                task_id="task-deterministic-1",
                database_service=db,
                site_config=_StubSiteConfig(),
                models_by_phase=models,
            )
        assert result == {
            "experiment_key": "writer_test_run",
            "variant_key": "fast",
        }
        assert models == {"writer": "tiny:1b"}
        # Signature contract — defends against arg-name drift on the
        # service side (e.g. accidentally passing task_id= instead of
        # subject_id=).
        mocked_svc.assign.assert_awaited_once_with(
            experiment_key="writer_test_run",
            subject_id="task-deterministic-1",
        )

    async def test_assigns_variant_with_no_writer_model_leaves_models_untouched(self):
        """Variant whose config has no ``writer_model`` key must NOT
        touch models_by_phase. Pins that the merge is gated on key
        presence, not just on having any variant_key."""
        db = _stub_database_service(active_experiment_key="control_only_arm")
        mocked_svc = _make_mocked_service(
            assign_return="control",
            dataset_variants=[
                {"key": "control", "weight": 50, "config": {}},
                {"key": "fast", "weight": 50, "config": {"writer_model": "tiny:1b"}},
            ],
        )
        models: dict[str, str] = {}
        with patch(_LF_PATCH_TARGET, return_value=mocked_svc):
            result = await assign_pipeline_variant(
                task_id="task-control-arm",
                database_service=db,
                site_config=_StubSiteConfig(),
                models_by_phase=models,
            )
        assert result["variant_key"] == "control"
        assert "writer" not in models

    async def test_does_not_trample_explicit_writer_override(self):
        """Explicit per-task ``models_by_phase['writer']`` pin survives
        the variant config merge. Critical for debugging: an engineer
        reproducing a failure pins a model; the A/B harness must not
        silently swap it out underneath them."""
        db = _stub_database_service(active_experiment_key="dont_trample_test")
        mocked_svc = _make_mocked_service(
            assign_return="fast",
            dataset_variants=[
                {"key": "fast", "weight": 99, "config": {"writer_model": "tiny:1b"}},
                {"key": "control", "weight": 1, "config": {}},
            ],
        )
        models: dict[str, str] = {"writer": "explicit:99b"}
        with patch(_LF_PATCH_TARGET, return_value=mocked_svc):
            await assign_pipeline_variant(
                task_id="task-explicit-1",
                database_service=db,
                site_config=_StubSiteConfig(),
                models_by_phase=models,
            )
        assert models["writer"] == "explicit:99b"

    async def test_assignment_is_sticky(self):
        """Same subject_id → same variant_key on repeated assigns.

        Service-level stickiness lives in
        ``LangfuseExperimentService._hash_subject`` (tested separately
        in ``test_langfuse_experiments.py``). The hook's contract: if
        the service returns the same variant twice, the hook returns
        it twice and applies the variant config both times. No hook-
        level caching, so we assert the service is hit on every call."""
        db = _stub_database_service(active_experiment_key="sticky_test")
        mocked_svc = _make_mocked_service(
            assign_return="a",
            dataset_variants=[
                {"key": "a", "weight": 50, "config": {}},
                {"key": "b", "weight": 50, "config": {}},
            ],
        )
        with patch(_LF_PATCH_TARGET, return_value=mocked_svc):
            first = await assign_pipeline_variant(
                task_id="sticky-task",
                database_service=db,
                site_config=_StubSiteConfig(),
                models_by_phase={},
            )
            second = await assign_pipeline_variant(
                task_id="sticky-task",
                database_service=db,
                site_config=_StubSiteConfig(),
                models_by_phase={},
            )
        assert first == second
        assert first["variant_key"] == "a"
        assert mocked_svc.assign.await_count == 2
        for call in mocked_svc.assign.await_args_list:
            assert call.kwargs["subject_id"] == "sticky-task"
            assert call.kwargs["experiment_key"] == "sticky_test"

    async def test_no_op_when_service_returns_no_variant(self):
        """``svc.assign`` returns None when the dataset is paused /
        unknown / has <2 variants. Hook surfaces the no-op shape so
        the pipeline falls back to default config."""
        db = _stub_database_service(active_experiment_key="paused_exp")
        mocked_svc = _make_mocked_service(assign_return=None)
        models: dict[str, str] = {}
        with patch(_LF_PATCH_TARGET, return_value=mocked_svc):
            result = await assign_pipeline_variant(
                task_id="task-paused",
                database_service=db,
                site_config=_StubSiteConfig(),
                models_by_phase=models,
            )
        assert result == {"experiment_key": None, "variant_key": None}
        assert models == {}

    async def test_assign_returns_no_op_when_service_raises(self):
        """Error-isolation: a Langfuse outage / SDK exception in
        ``svc.assign`` must NOT halt the pipeline. Hook swallows +
        logs + returns the no-op shape. Pre-existing models_by_phase
        entries must survive."""
        db = _stub_database_service(active_experiment_key="will_blow_up")
        mocked_svc = _make_mocked_service(
            assign_side_effect=RuntimeError("langfuse 503"),
        )
        models: dict[str, str] = {"writer": "explicit:34b"}
        with patch(_LF_PATCH_TARGET, return_value=mocked_svc):
            result = await assign_pipeline_variant(
                task_id="task-isolation",
                database_service=db,
                site_config=_StubSiteConfig(),
                models_by_phase=models,
            )
        assert result == {"experiment_key": None, "variant_key": None}
        assert models == {"writer": "explicit:34b"}

    async def test_assign_returns_no_op_when_setting_lookup_raises(self):
        """If the app_settings fetch raises (DB blip mid-pipeline),
        the hook treats the experiment as inactive. The Langfuse
        service must never be constructed in this path."""
        db = _stub_db_with_failing_pool()
        with patch(_LF_PATCH_TARGET) as svc_ctor:
            result = await assign_pipeline_variant(
                task_id="task-db-blip",
                database_service=db,
                site_config=_StubSiteConfig(),
                models_by_phase={},
            )
        assert result == {"experiment_key": None, "variant_key": None}
        svc_ctor.assert_not_called()


# ---------------------------------------------------------------------------
# record_pipeline_outcome
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio(loop_scope="session")
class TestRecordPipelineOutcome:
    async def test_no_op_when_assignment_empty(self):
        """Empty assignment dict = no-op. Must not raise even with
        database_service=None."""
        await record_pipeline_outcome(
            assignment={},
            task_id="t1",
            database_service=None,
            site_config=_StubSiteConfig(),
            metrics={"score": 90.0},
        )

    async def test_no_op_when_experiment_key_missing(self):
        """Assignment without experiment_key (the common case — no
        experiment was active) = no-op."""
        await record_pipeline_outcome(
            assignment={"experiment_key": None, "variant_key": None},
            task_id="t1",
            database_service=None,
            site_config=_StubSiteConfig(),
            metrics={"score": 90.0},
        )

    async def test_no_op_when_database_service_none(self):
        """Assignment carries an experiment_key but database_service is
        None — defensive guard, still a no-op."""
        await record_pipeline_outcome(
            assignment={"experiment_key": "exp", "variant_key": "v"},
            task_id="t1",
            database_service=None,
            site_config=_StubSiteConfig(),
            metrics={"score": 90.0},
        )

    async def test_writes_metrics_to_assignment_row(self):
        """Happy path: record_outcome called with the contract kwargs:
        ``experiment_key=`` from the assignment, ``subject_id=`` from
        task_id, ``metrics=`` from the caller — preserved 1:1 (the
        hook must not mutate the dict)."""
        db = _stub_database_service(active_experiment_key="outcome_test")
        mocked_svc = _make_mocked_service()
        metrics = {"quality_score": 88.5, "status": "success"}
        with patch(_LF_PATCH_TARGET, return_value=mocked_svc):
            await record_pipeline_outcome(
                assignment={"experiment_key": "outcome_test", "variant_key": "only"},
                task_id="outcome-task",
                database_service=db,
                site_config=_StubSiteConfig(),
                metrics=metrics,
            )
        mocked_svc.record_outcome.assert_awaited_once_with(
            experiment_key="outcome_test",
            subject_id="outcome-task",
            metrics=metrics,
        )
        assert metrics == {"quality_score": 88.5, "status": "success"}

    async def test_record_outcome_swallows_service_exception(self):
        """Error-isolation: Langfuse blowing up mid-record must not
        raise out of the hook. Finalize is past the value-producing
        stages; bookkeeping failure is acceptable but a crash isn't."""
        db = _stub_database_service(active_experiment_key="outcome_test")
        mocked_svc = _make_mocked_service(
            record_outcome_side_effect=RuntimeError("langfuse 503"),
        )
        with patch(_LF_PATCH_TARGET, return_value=mocked_svc):
            await record_pipeline_outcome(
                assignment={"experiment_key": "outcome_test", "variant_key": "v"},
                task_id="outcome-task",
                database_service=db,
                site_config=_StubSiteConfig(),
                metrics={"quality_score": 88.5},
            )
        mocked_svc.record_outcome.assert_awaited_once()
