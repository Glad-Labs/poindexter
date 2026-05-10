"""Tests for the Langfuse-backed experiment harness (#202).

The legacy ``test_experiment_service.py`` was deleted alongside its
SQL-backed implementation; this file pins the new surface.
``LangfuseExperimentService`` is mocked at the Langfuse-client layer —
construction reads three settings from site_config, every method
delegates to a method on the client, and the deterministic
blake2b sticky-assignment math stays in Python. The tests assert on
the Python math + the client method dispatch shape.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.langfuse_experiments import (
    LangfuseExperimentService,
    _trace_id_for,
)


def _stub_site_config(creds: dict[str, str] | None = None) -> MagicMock:
    """A site_config double that returns Langfuse creds. ``creds=None``
    yields a fully-configured stub; pass ``{}`` to test the
    creds-missing path (the empty dict is intentional, not a default
    fallback)."""
    sc = MagicMock()
    if creds is None:
        creds = {
            "langfuse_host": "http://localhost:3010",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }
    sc.get.side_effect = lambda key, default=None: creds.get(key, default)
    return sc


@pytest.mark.unit
class TestStickyAssignmentMath:
    """blake2b → 0..99 bucket → variant pick. Identical math to the
    SQL-backed legacy service so live experiments mid-cutover keep
    routing subjects to the same variant."""

    def test_hash_subject_is_deterministic(self):
        a = LangfuseExperimentService._hash_subject("exp1", "subj-A")
        b = LangfuseExperimentService._hash_subject("exp1", "subj-A")
        assert a == b
        assert 0 <= a < 100

    def test_hash_differs_per_experiment(self):
        a = LangfuseExperimentService._hash_subject("exp1", "subj-A")
        b = LangfuseExperimentService._hash_subject("exp2", "subj-A")
        assert a != b  # statistically possible to collide; blake2b makes this unlikely

    def test_pick_variant_respects_weights(self):
        variants = [
            {"key": "control", "weight": 80, "config": {}},
            {"key": "variant_a", "weight": 20, "config": {}},
        ]
        # Bucket 0..79 → control, 80..99 → variant_a
        assert LangfuseExperimentService._pick_variant(variants, 0) == "control"
        assert LangfuseExperimentService._pick_variant(variants, 79) == "control"
        assert LangfuseExperimentService._pick_variant(variants, 80) == "variant_a"
        assert LangfuseExperimentService._pick_variant(variants, 99) == "variant_a"

    def test_pick_variant_overflow_falls_back_to_last(self):
        variants = [
            {"key": "a", "weight": 50, "config": {}},
            {"key": "b", "weight": 50, "config": {}},
        ]
        # Bucket 100 (impossible under our hash) → last variant
        assert LangfuseExperimentService._pick_variant(variants, 100) == "b"


@pytest.mark.unit
class TestTraceIdMath:
    """``_trace_id_for`` makes assignments idempotent across workers."""

    def test_deterministic(self):
        a = _trace_id_for("exp1", "task-123")
        b = _trace_id_for("exp1", "task-123")
        assert a == b

    def test_differs_per_subject(self):
        assert _trace_id_for("exp1", "a") != _trace_id_for("exp1", "b")

    def test_differs_per_experiment(self):
        assert _trace_id_for("exp1", "x") != _trace_id_for("exp2", "x")


@pytest.mark.unit
class TestVariantValidation:
    """``_validate_variants`` must reject malformed configs at create()
    time so the operator can't ship a broken experiment."""

    def test_rejects_too_few_variants(self):
        with pytest.raises(ValueError, match=">= 2 entries"):
            LangfuseExperimentService._validate_variants([
                {"key": "only", "weight": 100, "config": {}},
            ])

    def test_rejects_duplicate_keys(self):
        with pytest.raises(ValueError, match="duplicate variant key"):
            LangfuseExperimentService._validate_variants([
                {"key": "x", "weight": 50, "config": {}},
                {"key": "x", "weight": 50, "config": {}},
            ])

    def test_rejects_weight_sum_outside_slack(self):
        with pytest.raises(ValueError, match="weights must sum"):
            LangfuseExperimentService._validate_variants([
                {"key": "a", "weight": 30, "config": {}},
                {"key": "b", "weight": 30, "config": {}},
            ])

    def test_accepts_99_or_101_via_slack(self):
        """Three-way 33/33/34 splits land at 100 exactly; some operators
        hand-edit weights and end up at 99 or 101. The validator's
        [98, 102] slack lets that through without failing."""
        result = LangfuseExperimentService._validate_variants([
            {"key": "a", "weight": 33, "config": {}},
            {"key": "b", "weight": 33, "config": {}},
            {"key": "c", "weight": 33, "config": {}},
        ])
        assert len(result) == 3


@pytest.mark.unit
class TestClientLifecycle:
    """``_get_client`` builds a Langfuse client lazily + fails loud if
    creds are missing."""

    def test_missing_creds_raises_with_repair_hint(self):
        sc = _stub_site_config({})  # all creds empty
        svc = LangfuseExperimentService(site_config=sc, pool=None)
        with pytest.raises(RuntimeError, match="langfuse_host"):
            svc._get_client()

    def test_partial_creds_also_raises(self):
        sc = _stub_site_config({"langfuse_host": "http://localhost:3010"})
        svc = LangfuseExperimentService(site_config=sc, pool=None)
        with pytest.raises(RuntimeError, match="langfuse"):
            svc._get_client()

    def test_subsequent_failure_raises_consistently(self):
        """Once creds-missing fires once, subsequent calls also raise —
        avoids spamming the Langfuse host with init attempts on every
        method call."""
        sc = _stub_site_config({})
        svc = LangfuseExperimentService(site_config=sc, pool=None)
        with pytest.raises(RuntimeError):
            svc._get_client()
        with pytest.raises(RuntimeError):
            svc._get_client()

    def test_client_construction_with_full_creds(self):
        sc = _stub_site_config()
        svc = LangfuseExperimentService(site_config=sc, pool=None)
        with patch(
            "services.langfuse_experiments.Langfuse",
            return_value=MagicMock(),
        ) as lf_ctor:
            client = svc._get_client()
        assert client is not None
        lf_ctor.assert_called_once()
        ctor_kwargs = lf_ctor.call_args.kwargs
        assert ctor_kwargs["host"] == "http://localhost:3010"
        assert ctor_kwargs["public_key"] == "pk-test"
        assert ctor_kwargs["secret_key"] == "sk-test"


@pytest.mark.unit
class TestCreate:
    """``create`` builds a Langfuse Dataset + N Dataset Items."""

    @pytest.mark.asyncio
    async def test_create_calls_dataset_apis(self):
        sc = _stub_site_config()
        svc = LangfuseExperimentService(site_config=sc, pool=None)
        client = MagicMock()
        client.create_dataset.return_value = SimpleNamespace(id="ds-123")
        svc._client = client  # skip lazy init

        result = await svc.create(
            key="my-experiment",
            description="testing",
            variants=[
                {"key": "control", "weight": 50, "config": {"writer_model": "a"}},
                {"key": "test", "weight": 50, "config": {"writer_model": "b"}},
            ],
        )

        assert result == "ds-123"
        client.create_dataset.assert_called_once()
        ds_call = client.create_dataset.call_args.kwargs
        assert ds_call["name"] == "my-experiment"
        assert ds_call["metadata"]["_poindexter_kind"] == "ab_experiment"
        assert ds_call["metadata"]["status"] == "draft"
        # Two variants → two dataset items.
        assert client.create_dataset_item.call_count == 2

    @pytest.mark.asyncio
    async def test_create_rejects_invalid_variants(self):
        sc = _stub_site_config()
        svc = LangfuseExperimentService(site_config=sc, pool=None)
        svc._client = MagicMock()
        with pytest.raises(ValueError):
            await svc.create(
                key="my-experiment",
                description="testing",
                variants=[{"key": "only", "weight": 100, "config": {}}],
            )


@pytest.mark.unit
class TestAssign:
    """``assign`` returns the deterministic variant pick + writes a
    Langfuse trace with the deterministic id."""

    @pytest.mark.asyncio
    async def test_returns_none_when_dataset_missing(self):
        sc = _stub_site_config()
        svc = LangfuseExperimentService(site_config=sc, pool=None)
        client = MagicMock()
        client.get_dataset.side_effect = RuntimeError("dataset not found")
        svc._client = client

        result = await svc.assign(
            experiment_key="missing", subject_id="task-1",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_status_not_running(self):
        sc = _stub_site_config()
        svc = LangfuseExperimentService(site_config=sc, pool=None)
        client = MagicMock()
        client.get_dataset.return_value = SimpleNamespace(
            metadata={"status": "draft", "variants": [
                {"key": "a", "weight": 50, "config": {}},
                {"key": "b", "weight": 50, "config": {}},
            ]},
        )
        svc._client = client

        result = await svc.assign(experiment_key="exp1", subject_id="task-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_variant_when_running(self):
        sc = _stub_site_config()
        svc = LangfuseExperimentService(site_config=sc, pool=None)
        client = MagicMock()
        client.get_dataset.return_value = SimpleNamespace(
            metadata={"status": "running", "variants": [
                {"key": "control", "weight": 50, "config": {}},
                {"key": "treatment", "weight": 50, "config": {}},
            ]},
        )
        # start_as_current_span returns a context manager.
        client.start_as_current_span.return_value.__enter__ = MagicMock(
            return_value=MagicMock(),
        )
        client.start_as_current_span.return_value.__exit__ = MagicMock(
            return_value=False,
        )
        svc._client = client

        result = await svc.assign(experiment_key="exp1", subject_id="task-A")
        assert result in ("control", "treatment")
        # Trace was written for observability.
        client.start_as_current_span.assert_called_once()

    @pytest.mark.asyncio
    async def test_assign_is_sticky_across_calls(self):
        """Same subject → same variant. The blake2b math is deterministic
        so concurrent assigns from multiple workers always agree."""
        sc = _stub_site_config()
        svc = LangfuseExperimentService(site_config=sc, pool=None)
        client = MagicMock()
        client.get_dataset.return_value = SimpleNamespace(
            metadata={"status": "running", "variants": [
                {"key": "a", "weight": 50, "config": {}},
                {"key": "b", "weight": 50, "config": {}},
            ]},
        )
        client.start_as_current_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
        client.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
        svc._client = client

        first = await svc.assign(experiment_key="exp1", subject_id="task-X")
        second = await svc.assign(experiment_key="exp1", subject_id="task-X")
        assert first == second


@pytest.mark.unit
class TestRecordOutcome:
    """``record_outcome`` writes one Langfuse Score per metric."""

    @pytest.mark.asyncio
    async def test_numeric_metrics_become_scores(self):
        sc = _stub_site_config()
        svc = LangfuseExperimentService(site_config=sc, pool=None)
        client = MagicMock()
        svc._client = client

        ok = await svc.record_outcome(
            experiment_key="exp1",
            subject_id="task-1",
            metrics={"quality_score": 87.5, "duration_s": 412},
        )
        assert ok is True
        assert client.create_score.call_count == 2

    @pytest.mark.asyncio
    async def test_string_metrics_become_categorical_scores(self):
        sc = _stub_site_config()
        svc = LangfuseExperimentService(site_config=sc, pool=None)
        client = MagicMock()
        svc._client = client

        ok = await svc.record_outcome(
            experiment_key="exp1",
            subject_id="task-1",
            metrics={"winner_label": "treatment"},
        )
        assert ok is True
        client.create_score.assert_called_once()
        call_kwargs = client.create_score.call_args.kwargs
        assert call_kwargs["data_type"] == "CATEGORICAL"
        assert call_kwargs["value"] == "treatment"

    @pytest.mark.asyncio
    async def test_partial_failure_returns_false(self):
        """One score fails → method returns False but other scores
        still attempt to land. Caller knows something went wrong."""
        sc = _stub_site_config()
        svc = LangfuseExperimentService(site_config=sc, pool=None)
        client = MagicMock()
        # Second create_score raises.
        client.create_score.side_effect = [None, RuntimeError("boom"), None]
        svc._client = client

        ok = await svc.record_outcome(
            experiment_key="exp1",
            subject_id="task-1",
            metrics={"a": 1, "b": 2, "c": 3},
        )
        assert ok is False
        assert client.create_score.call_count == 3
