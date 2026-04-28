"""Tests for services/cost_lookup.py — LiteLLM-backed cost lookups (#199 Phase 1)."""

from __future__ import annotations

import pytest

from services.cost_lookup import (
    DEFAULT_COST_PER_1K,
    estimate_cost,
    get_model_cost,
    get_model_cost_per_1k,
)


@pytest.mark.unit
class TestGetModelCostPer1K:
    def test_known_anthropic_model_returns_real_pricing(self):
        # claude-haiku-4-5 in LiteLLM table → $0.001/1K input, $0.005/1K output.
        ipt, opt = get_model_cost_per_1k("claude-haiku-4-5")
        assert ipt > 0.0
        assert opt > ipt  # output is more expensive than input

    def test_local_ollama_route_returns_zero(self):
        ipt, opt = get_model_cost_per_1k("ollama/qwen3.5:35b")
        assert ipt == 0.0
        assert opt == 0.0

    def test_local_route_with_unknown_tag_still_returns_zero(self):
        # Custom local model not in LiteLLM table; our heuristic catches it.
        ipt, opt = get_model_cost_per_1k("ollama/glm-4.7-5090:latest")
        assert ipt == 0.0
        assert opt == 0.0

    def test_unknown_cloud_model_falls_back_to_default(self):
        ipt, opt = get_model_cost_per_1k("totally-fake-provider/foo-9000")
        assert ipt == DEFAULT_COST_PER_1K
        assert opt == DEFAULT_COST_PER_1K

    def test_empty_model_returns_default(self):
        ipt, opt = get_model_cost_per_1k("")
        assert ipt == DEFAULT_COST_PER_1K
        assert opt == DEFAULT_COST_PER_1K

    def test_provider_prefix_stripped_when_litellm_keys_without_it(self):
        # LiteLLM's table may have "claude-haiku-4-5" without the
        # "anthropic/" prefix — our lookup strips and retries.
        ipt, opt = get_model_cost_per_1k("anthropic/claude-haiku-4-5")
        assert ipt > 0.0
        assert opt > 0.0


@pytest.mark.unit
class TestGetModelCostBackwardCompat:
    def test_returns_max_of_input_output(self):
        # The single-number shim is conservative (uses max).
        cost = get_model_cost("claude-haiku-4-5")
        ipt, opt = get_model_cost_per_1k("claude-haiku-4-5")
        assert cost == max(ipt, opt)

    def test_local_returns_zero(self):
        assert get_model_cost("ollama/qwen3:8b") == 0.0


@pytest.mark.unit
class TestEstimateCost:
    def test_calculates_blended_cost(self):
        # 1000 prompt tokens + 500 completion tokens
        cost = estimate_cost("claude-haiku-4-5", prompt_tokens=1000, completion_tokens=500)
        assert cost > 0.0
        # Sanity: rough order of magnitude (~$0.001 prompt + ~$0.0025 completion ≈ $0.0035)
        assert 0.0001 < cost < 0.1

    def test_local_route_is_zero(self):
        cost = estimate_cost("ollama/qwen3:8b", prompt_tokens=10000, completion_tokens=5000)
        assert cost == 0.0

    def test_zero_tokens_is_zero(self):
        cost = estimate_cost("claude-haiku-4-5", prompt_tokens=0, completion_tokens=0)
        assert cost == 0.0
