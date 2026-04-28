"""
Unit tests for ModelRouter service.

Tests deterministic routing logic, complexity assessment, token limits,
metrics tracking, and budget-based model selection — no LLM or DB calls.
"""

from types import SimpleNamespace

import pytest

from services.model_router import (
    MAX_TOKENS_BY_TASK,
    ModelRouter,
    TaskComplexity,
    get_model_for_phase,
    get_model_router,
    initialize_model_router,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# Phase H step 5 (GH#95): ModelRouter ctor requires site_config.
def _fake_sc():
    return SimpleNamespace(
        get=lambda _k, _d=None: _d if _d is not None else "",
        get_int=lambda _k, _d=0: _d,
        get_float=lambda _k, _d=0.0: _d,
        get_bool=lambda _k, _d=False: _d,
    )


@pytest.fixture
def router() -> ModelRouter:
    """Fresh ModelRouter with Ollama enabled (default policy)."""
    return ModelRouter(default_model="ollama/qwen3:8b", use_ollama=True, site_config=_fake_sc())


@pytest.fixture
def ollama_router() -> ModelRouter:
    """ModelRouter with Ollama enabled (alias for clarity in older test names)."""
    return ModelRouter(default_model="ollama/qwen3:8b", use_ollama=True, site_config=_fake_sc())


# ---------------------------------------------------------------------------
# _assess_complexity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAssessComplexity:
    def test_summarize_is_simple(self, router):
        result = router._assess_complexity("summarize", {})
        assert result == TaskComplexity.SIMPLE

    def test_analyze_is_medium(self, router):
        result = router._assess_complexity("analyze", {})
        assert result == TaskComplexity.MEDIUM

    def test_create_is_complex(self, router):
        result = router._assess_complexity("create", {})
        assert result == TaskComplexity.COMPLEX

    def test_legal_is_critical(self, router):
        result = router._assess_complexity("legal", {})
        assert result == TaskComplexity.CRITICAL

    def test_compliance_is_critical(self, router):
        result = router._assess_complexity("compliance", {})
        assert result == TaskComplexity.CRITICAL

    def test_security_is_critical(self, router):
        result = router._assess_complexity("security audit", {})
        assert result == TaskComplexity.CRITICAL

    def test_requires_reasoning_overrides_to_complex(self, router):
        result = router._assess_complexity("summarize", {"requires_reasoning": True})
        assert result == TaskComplexity.COMPLEX

    def test_large_max_tokens_overrides_to_complex(self, router):
        result = router._assess_complexity("summarize", {"max_tokens": 2001})
        assert result == TaskComplexity.COMPLEX

    def test_max_tokens_at_boundary_does_not_escalate(self, router):
        result = router._assess_complexity("summarize", {"max_tokens": 2000})
        assert result == TaskComplexity.SIMPLE

    def test_unknown_task_defaults_to_medium(self, router):
        result = router._assess_complexity("frobulate", {})
        assert result == TaskComplexity.MEDIUM

    def test_case_insensitive_matching(self, router):
        result = router._assess_complexity("SUMMARIZE", {})
        assert result == TaskComplexity.SIMPLE


# ---------------------------------------------------------------------------
# route_request — model selection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRouteRequest:
    def test_simple_task_returns_ollama_model(self, router):
        model, cost, complexity = router.route_request("summarize")
        assert complexity == TaskComplexity.SIMPLE
        assert model.startswith("ollama/")

    def test_critical_priority_context_overrides_complexity(self, router):
        model, cost, complexity = router.route_request("summarize", {"priority": "critical"})
        assert complexity == TaskComplexity.CRITICAL

    def test_force_premium_escalates_to_complex(self, router):
        model, cost, complexity = router.route_request("summarize", {"force_premium": True})
        assert complexity == TaskComplexity.COMPLEX

    def test_ollama_router_returns_ollama_model(self, ollama_router):
        model, cost, complexity = ollama_router.route_request("summarize")
        assert model.startswith("ollama/")

    def test_returns_tuple_of_three(self, router):
        result = router.route_request("analyze")
        assert len(result) == 3

    def test_estimated_cost_non_negative(self, router):
        _, cost, _ = router.route_request("create", estimated_tokens=500)
        assert cost >= 0.0

    def test_ollama_cost_is_zero(self, router):
        _, cost, _ = router.route_request("summarize", estimated_tokens=1000)
        assert cost == 0.0

    def test_total_requests_incremented(self, router):
        router.route_request("summarize")
        router.route_request("analyze")
        assert router.metrics["total_requests"] == 2

    def test_ollama_uses_tracked(self, ollama_router):
        ollama_router.route_request("summarize")
        assert ollama_router.metrics["ollama_uses"] == 1

    def test_complexity_maps_to_expected_models(self, router):
        """Each complexity level maps to the expected Ollama model."""
        model_s, _, _ = router.route_request("summarize")
        assert model_s == "ollama/qwen3:8b"

        model_m, _, _ = router.route_request("analyze")
        assert model_m == "ollama/gemma3:27b"

        model_c, _, _ = router.route_request("create")
        assert model_c == "ollama/qwen3.5:35b"

        model_cr, _, _ = router.route_request("legal")
        assert model_cr == "ollama/qwen3.5:122b"


# ---------------------------------------------------------------------------
# get_max_tokens
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMaxTokens:
    def test_known_task_returns_expected_limit(self, router):
        assert router.get_max_tokens("summarize") == MAX_TOKENS_BY_TASK["summarize"]
        assert router.get_max_tokens("create") == MAX_TOKENS_BY_TASK["create"]

    def test_unknown_task_returns_default(self, router):
        assert router.get_max_tokens("frobulate") == MAX_TOKENS_BY_TASK["default"]

    def test_max_tokens_in_context_overrides(self, router):
        result = router.get_max_tokens("summarize", {"max_tokens": 9999})
        assert result == 9999

    def test_override_tokens_in_context_overrides(self, router):
        result = router.get_max_tokens("create", {"override_tokens": 2000})
        assert result == 2000

    def test_max_tokens_takes_precedence_over_override_tokens(self, router):
        result = router.get_max_tokens("create", {"max_tokens": 100, "override_tokens": 999})
        assert result == 100

    def test_partial_match_in_task_type(self, router):
        assert router.get_max_tokens("auto-summarize") == MAX_TOKENS_BY_TASK["summarize"]


# ---------------------------------------------------------------------------
# get_model_cost
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetModelCost:
    def test_ollama_model_is_free(self, router):
        cost = router.get_model_cost("ollama/qwen3:8b")
        assert cost == 0.0

    def test_unknown_model_falls_back_to_default(self, router):
        """Pre-#199 returned 0.0 for unknown models — that was wrong, it
        silently treated paid cloud models as free. Post-#199 LiteLLM
        cost_lookup is the source of truth: known models get accurate
        prices, unknown cloud models fall back to ``DEFAULT_COST_PER_1K``
        (currently $0.005/1K) which is intentionally non-zero so a
        misconfigured model doesn't slip through cost_guard.
        """
        from services.cost_lookup import DEFAULT_COST_PER_1K
        cost = router.get_model_cost("unknown-model-xyz")
        assert cost == DEFAULT_COST_PER_1K


# ---------------------------------------------------------------------------
# get_metrics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMetrics:
    def test_fresh_router_has_zero_metrics(self, router):
        metrics = router.get_metrics()
        assert metrics["total_requests"] == 0
        assert metrics["budget_model_uses"] == 0
        assert metrics["estimated_cost_actual"] == 0.0

    def test_metrics_after_requests(self, router):
        router.route_request("summarize")
        router.route_request("list")
        metrics = router.get_metrics()
        assert metrics["total_requests"] == 2
        assert metrics["ollama_uses"] == 2


# ---------------------------------------------------------------------------
# reset_metrics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResetMetrics:
    def test_reset_clears_all_counters(self, router):
        router.route_request("summarize")
        router.reset_metrics()
        assert router.metrics["total_requests"] == 0
        assert router.metrics["budget_model_uses"] == 0
        assert router.metrics["estimated_cost_actual"] == 0.0


# ---------------------------------------------------------------------------
# recommend_model_for_budget
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecommendModelForBudget:
    def test_returns_cheapest_model_within_budget(self, router):
        model = router.recommend_model_for_budget(remaining_budget=1.0, estimated_tokens=1000)
        assert model is not None

    def test_returns_model_when_budget_is_zero(self, router):
        # Ollama free models cost 0.0 so one should always be returned
        model = router.recommend_model_for_budget(remaining_budget=0.0, estimated_tokens=1000)
        assert model is not None

    def test_returns_none_for_negative_budget(self, router):
        # 0.0 <= -0.01 is False -> should return None
        model = router.recommend_model_for_budget(remaining_budget=-0.01, estimated_tokens=1000)
        assert model is None

    def test_ample_budget_returns_a_model(self, router):
        model = router.recommend_model_for_budget(remaining_budget=100.0, estimated_tokens=1000)
        assert model is not None


# ---------------------------------------------------------------------------
# get_model_for_phase (module-level helper)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetModelForPhase:
    def test_explicit_selection_returned_as_is(self):
        selections = {"draft": "ollama/custom:7b"}
        result = get_model_for_phase("draft", selections, "balanced")
        assert result == "ollama/custom:7b"

    def test_auto_selection_falls_back_to_default(self):
        selections = {"draft": "auto"}
        result = get_model_for_phase("draft", selections, "balanced")
        assert result == "ollama/qwen3.5:35b"

    def test_empty_selections_uses_quality_preference(self):
        result = get_model_for_phase("draft", {}, "quality")
        assert result == "ollama/qwen3.5:35b"

    def test_unknown_quality_preference_falls_back_to_balanced(self):
        result = get_model_for_phase("draft", {}, "nonexistent_tier")
        assert result == "ollama/qwen3.5:35b"

    def test_none_quality_preference_defaults_to_balanced(self):
        result = get_model_for_phase("draft", {}, None)  # type: ignore[arg-type]
        assert result == "ollama/qwen3.5:35b"

    def test_unknown_phase_returns_fallback(self):
        result = get_model_for_phase("unknown_phase", {}, "balanced")
        assert result == "ollama/qwen3:8b"

    def test_all_phases_covered_for_all_tiers(self):
        phases = ["research", "outline", "draft", "assess", "refine", "finalize"]
        for tier in ("fast", "balanced", "quality"):
            for phase in phases:
                result = get_model_for_phase(phase, {}, tier)
                assert isinstance(result, str)
                assert len(result) > 0


# ---------------------------------------------------------------------------
# Global singleton helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGlobalSingleton:
    def test_initialize_returns_model_router_instance(self):
        instance = initialize_model_router(site_config=_fake_sc())
        assert isinstance(instance, ModelRouter)

    def test_get_model_router_returns_same_instance(self):
        instance = initialize_model_router(site_config=_fake_sc())
        retrieved = get_model_router()
        assert retrieved is instance


# ---------------------------------------------------------------------------
# Provider failure tracking (#428)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderFailureTracking:
    def test_record_failure_increments_count(self, router):
        router.record_provider_failure("ollama")
        health = router.get_provider_health()
        assert health["ollama"]["consecutive_failures"] == 1

    def test_record_success_resets_count(self, router):
        for _ in range(3):
            router.record_provider_failure("ollama")
        router.record_provider_success("ollama")
        health = router.get_provider_health()
        assert health["ollama"]["consecutive_failures"] == 0

    def test_critical_threshold_at_five_failures(self, router, caplog):
        import logging

        with caplog.at_level(logging.CRITICAL):
            for _ in range(5):
                router.record_provider_failure("ollama")
        assert any("5 consecutive" in r.message for r in caplog.records)

    def test_get_provider_health_empty_on_fresh_router(self, router):
        assert router.get_provider_health() == {}

    def test_multiple_providers_tracked_independently(self, router):
        router.record_provider_failure("ollama")
        router.record_provider_failure("ollama")
        router.record_provider_failure("huggingface")
        health = router.get_provider_health()
        assert health["ollama"]["consecutive_failures"] == 2
        assert health["huggingface"]["consecutive_failures"] == 1

    def test_record_success_on_clean_provider_no_log(self, router, caplog):
        """Calling record_success on a provider with 0 failures should not log recovery."""
        import logging
        with caplog.at_level(logging.INFO, logger="services.model_router"):
            router.record_provider_success("ollama")
        # No "recovered" log should appear
        assert not any("recovered" in r.message for r in caplog.records)

    def test_record_success_logs_recovery_after_failures(self, router, caplog):
        import logging
        router.record_provider_failure("ollama")
        router.record_provider_failure("ollama")
        with caplog.at_level(logging.INFO, logger="services.model_router"):
            router.record_provider_success("ollama")
        assert any("recovered" in r.message for r in caplog.records)

    def test_failure_count_persists_across_calls(self, router):
        for _ in range(3):
            router.record_provider_failure("anthropic")
        router.record_provider_failure("anthropic")
        assert router._provider_consecutive_failures["anthropic"] == 4


# ---------------------------------------------------------------------------
# seed_spend_from_db
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSeedSpendFromDb:
    @pytest.mark.asyncio
    async def test_seeds_spend_from_pool(self, router):
        from unittest.mock import AsyncMock

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"total": 42.50})

        await router.seed_spend_from_db(pool)

        assert router._session_cloud_spend == 42.50

    @pytest.mark.asyncio
    async def test_zero_spend_when_no_rows(self, router):
        from unittest.mock import AsyncMock

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"total": 0})

        await router.seed_spend_from_db(pool)
        assert router._session_cloud_spend == 0.0

    @pytest.mark.asyncio
    async def test_db_error_keeps_zero_spend(self, router):
        from unittest.mock import AsyncMock

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))

        await router.seed_spend_from_db(pool)
        # Should not crash, spend remains 0
        assert router._session_cloud_spend == 0.0

    @pytest.mark.asyncio
    async def test_spend_over_limit_logs_critical(self, router, caplog):
        import logging
        from unittest.mock import AsyncMock

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"total": 99999.0})

        with caplog.at_level(logging.CRITICAL, logger="services.model_router"):
            await router.seed_spend_from_db(pool)

        assert router._budget_exceeded_logged is True
        assert any("BUDGET_EXCEEDED" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_spend_under_limit_does_not_set_exceeded_flag(self, router):
        from unittest.mock import AsyncMock

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"total": 1.0})

        await router.seed_spend_from_db(pool)
        assert router._budget_exceeded_logged is False

    @pytest.mark.asyncio
    async def test_query_filters_to_current_month(self, router):
        from unittest.mock import AsyncMock

        captured = {}

        async def _capture(sql):
            captured["sql"] = sql
            return {"total": 0}

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=_capture)
        await router.seed_spend_from_db(pool)

        assert "date_trunc('month', NOW())" in captured["sql"]
        assert "cost_logs" in captured["sql"]


# ---------------------------------------------------------------------------
# __init__ edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModelRouterInit:
    def test_use_ollama_explicit_true(self):
        r = ModelRouter(use_ollama=True, site_config=_fake_sc())
        assert r.use_ollama is True

    def test_use_ollama_explicit_false(self):
        r = ModelRouter(use_ollama=False, site_config=_fake_sc())
        assert r.use_ollama is False

    def test_use_ollama_none_reads_site_config(self):
        from unittest.mock import MagicMock
        mock_cfg = MagicMock()
        mock_cfg.get.return_value = "true"
        r = ModelRouter(use_ollama=None, site_config=mock_cfg)
        assert r.use_ollama is True

    def test_use_ollama_none_site_config_exception_defaults_false(self):
        from unittest.mock import MagicMock
        mock_cfg = MagicMock()
        mock_cfg.get.side_effect = RuntimeError("config down")
        r = ModelRouter(use_ollama=None, site_config=mock_cfg)
        assert r.use_ollama is False

    def test_default_model_stored(self):
        r = ModelRouter(default_model="ollama/custom:7b", use_ollama=True, site_config=_fake_sc())
        assert r.default_model == "ollama/custom:7b"

    def test_metrics_initialized_to_zero(self):
        r = ModelRouter(use_ollama=True, site_config=_fake_sc())
        assert r.metrics["total_requests"] == 0
        assert r.metrics["ollama_uses"] == 0
        assert r.metrics["estimated_cost_actual"] == 0.0

    def test_session_cloud_spend_starts_zero(self):
        r = ModelRouter(use_ollama=True, site_config=_fake_sc())
        assert r._session_cloud_spend == 0.0
        assert r._budget_exceeded_logged is False

    def test_provider_failures_starts_empty(self):
        r = ModelRouter(use_ollama=True, site_config=_fake_sc())
        assert r._provider_consecutive_failures == {}

    def test_failure_threshold_is_five(self):
        r = ModelRouter(use_ollama=True, site_config=_fake_sc())
        assert r._FAILURE_ALERT_THRESHOLD == 5
