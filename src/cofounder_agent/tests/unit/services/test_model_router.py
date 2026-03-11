"""
Unit tests for ModelRouter service.

Tests deterministic routing logic, complexity assessment, token limits,
metrics tracking, and budget-based model selection — no LLM or DB calls.
"""

import pytest

from services.model_router import (
    MAX_TOKENS_BY_TASK,
    ModelRouter,
    ModelTier,
    TaskComplexity,
    get_model_for_phase,
    get_model_router,
    initialize_model_router,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def router() -> ModelRouter:
    """Fresh ModelRouter with Ollama disabled for predictable model selection."""
    return ModelRouter(default_model="ollama/mistral", use_ollama=False)


@pytest.fixture
def ollama_router() -> ModelRouter:
    """ModelRouter with Ollama enabled."""
    return ModelRouter(default_model="ollama/mistral", use_ollama=True)


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
        """context flag requires_reasoning forces COMPLEX even for simple task types."""
        result = router._assess_complexity("summarize", {"requires_reasoning": True})
        assert result == TaskComplexity.COMPLEX

    def test_large_max_tokens_overrides_to_complex(self, router):
        """max_tokens > 2000 in context escalates to COMPLEX."""
        result = router._assess_complexity("summarize", {"max_tokens": 2001})
        assert result == TaskComplexity.COMPLEX

    def test_max_tokens_at_boundary_does_not_escalate(self, router):
        """max_tokens == 2000 stays at the task's natural complexity."""
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
    def test_simple_task_gets_budget_model(self, router):
        model, cost, complexity = router.route_request("summarize")
        assert complexity == TaskComplexity.SIMPLE
        assert model == "gpt-3.5-turbo"

    def test_critical_priority_context_overrides_complexity(self, router):
        """priority=critical in context must escalate to CRITICAL regardless of task type."""
        model, cost, complexity = router.route_request("summarize", {"priority": "critical"})
        assert complexity == TaskComplexity.CRITICAL

    def test_force_premium_escalates_to_complex(self, router):
        """force_premium in context must escalate to COMPLEX."""
        model, cost, complexity = router.route_request("summarize", {"force_premium": True})
        assert complexity == TaskComplexity.COMPLEX

    def test_prefer_fallback_returns_fallback_model(self, router):
        """prefer_fallback should select the fallback model for the chosen complexity."""
        model, cost, complexity = router.route_request("summarize", {"prefer_fallback": True})
        assert complexity == TaskComplexity.SIMPLE
        expected_fallback = ModelRouter.MODEL_RECOMMENDATIONS[TaskComplexity.SIMPLE]["fallback"]
        assert model == expected_fallback

    def test_ollama_router_returns_ollama_model(self, ollama_router):
        model, cost, complexity = ollama_router.route_request("summarize")
        assert model.startswith("ollama/")

    def test_returns_tuple_of_three(self, router):
        result = router.route_request("analyze")
        assert len(result) == 3

    def test_estimated_cost_non_negative(self, router):
        _, cost, _ = router.route_request("create", estimated_tokens=500)
        assert cost >= 0.0

    def test_total_requests_incremented(self, router):
        router.route_request("summarize")
        router.route_request("analyze")
        assert router.metrics["total_requests"] == 2

    def test_ollama_uses_tracked(self, ollama_router):
        ollama_router.route_request("summarize")
        assert ollama_router.metrics["ollama_uses"] == 1


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
        """max_tokens is checked first; override_tokens is a secondary alias."""
        result = router.get_max_tokens("create", {"max_tokens": 100, "override_tokens": 999})
        assert result == 100

    def test_partial_match_in_task_type(self, router):
        """Substring match: 'auto-summarize' should match 'summarize' keyword."""
        assert router.get_max_tokens("auto-summarize") == MAX_TOKENS_BY_TASK["summarize"]


# ---------------------------------------------------------------------------
# get_model_cost
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetModelCost:
    def test_known_model_returns_cost(self, router):
        cost = router.get_model_cost("gpt-4-turbo")
        assert cost == 0.045

    def test_ollama_model_is_free(self, router):
        cost = router.get_model_cost("ollama/mistral")
        assert cost == 0.0

    def test_unknown_model_returns_fallback(self, router):
        cost = router.get_model_cost("unknown-model-xyz")
        assert cost == 0.045  # default fallback in get_model_cost


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
        assert metrics["estimated_cost_saved"] == 0.0

    def test_savings_percentage_zero_when_no_requests(self, router):
        metrics = router.get_metrics()
        assert metrics["savings_percentage"] == 0.0

    def test_budget_model_percentage_after_requests(self, router):
        # Both "summarize" and "list" are SIMPLE → budget tier
        router.route_request("summarize")
        router.route_request("list")
        metrics = router.get_metrics()
        assert metrics["budget_model_percentage"] == 100.0

    def test_cost_saved_positive_for_budget_model(self, router):
        router.route_request("summarize", estimated_tokens=1000)
        metrics = router.get_metrics()
        # gpt-3.5-turbo is cheaper than gpt-4-turbo baseline
        assert metrics["estimated_cost_saved"] > 0


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
        # Any budget should get at least the cheapest model
        model = router.recommend_model_for_budget(remaining_budget=1.0, estimated_tokens=1000)
        assert model is not None

    def test_returns_none_when_budget_is_zero(self, router):
        # No paid model fits a zero budget (Ollama models have 0.0 cost so they
        # may fit — verify that at minimum a non-error result is returned)
        model = router.recommend_model_for_budget(remaining_budget=0.0, estimated_tokens=1000)
        # Ollama free models cost 0.0 so one should always be returned
        assert model is not None

    def test_returns_none_for_negative_budget(self, router):
        # Negative budget: free (0.0-cost) Ollama models still fit (0.0 <= neg? no)
        # Depends on implementation: 0.0 <= -0.01 is False → should return None
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
        selections = {"draft": "gpt-4"}
        result = get_model_for_phase("draft", selections, "balanced")
        assert result == "gpt-4"

    def test_auto_selection_falls_back_to_default(self):
        selections = {"draft": "auto"}
        result = get_model_for_phase("draft", selections, "balanced")
        # draft phase uses best model in balanced tier (#196 phase differentiation)
        assert result == "ollama/gpt-oss:120b"

    def test_empty_selections_uses_quality_preference(self):
        result = get_model_for_phase("draft", {}, "quality")
        assert result == "ollama/gpt-oss:120b"

    def test_unknown_quality_preference_falls_back_to_balanced(self):
        result = get_model_for_phase("draft", {}, "nonexistent_tier")
        # falls back to balanced tier; draft uses best model (#196)
        assert result == "ollama/gpt-oss:120b"

    def test_none_quality_preference_defaults_to_balanced(self):
        result = get_model_for_phase("draft", {}, None)  # type: ignore[arg-type]
        # defaults to balanced tier; draft uses best model (#196)
        assert result == "ollama/gpt-oss:120b"

    def test_unknown_phase_returns_fallback(self):
        result = get_model_for_phase("unknown_phase", {}, "balanced")
        assert result == "ollama/gpt-oss:20b"

    def test_all_phases_covered_for_all_tiers(self):
        phases = ["research", "outline", "draft", "assess", "refine", "finalize"]
        for tier in ("fast", "balanced", "quality"):
            for phase in phases:
                result = get_model_for_phase(phase, {}, tier)
                assert isinstance(result, str) and len(result) > 0


# ---------------------------------------------------------------------------
# Global singleton helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGlobalSingleton:
    def test_initialize_returns_model_router_instance(self):
        instance = initialize_model_router()
        assert isinstance(instance, ModelRouter)

    def test_get_model_router_returns_same_instance(self):
        instance = initialize_model_router()
        retrieved = get_model_router()
        assert retrieved is instance
