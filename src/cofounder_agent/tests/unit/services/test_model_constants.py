"""
Unit tests for model_constants.py

Verifies that MODEL_COSTS, MODEL_FAMILIES, PROVIDER_ICONS, and
DEFAULT_MODEL_COST contain the expected structure and values.
All tests are pure data-inspection — no I/O, no mocks.
"""

import pytest

from services.model_constants import DEFAULT_MODEL_COST, MODEL_COSTS, MODEL_FAMILIES, PROVIDER_ICONS

# ---------------------------------------------------------------------------
# MODEL_COSTS
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModelCosts:
    def test_is_dict(self):
        assert isinstance(MODEL_COSTS, dict)

    def test_all_values_are_non_negative_floats(self):
        for model, cost in MODEL_COSTS.items():
            assert isinstance(cost, (int, float)), f"{model} cost is not numeric"
            assert cost >= 0, f"{model} cost {cost} is negative"

    def test_ollama_models_are_free(self):
        ollama_keys = [k for k in MODEL_COSTS if k.startswith("ollama/")]
        assert len(ollama_keys) > 0, "Expected at least one ollama model"
        for key in ollama_keys:
            assert MODEL_COSTS[key] == 0.0, f"{key} should be free (0.0)"

    def test_ollama_qwen_models_present(self):
        qwen_keys = [k for k in MODEL_COSTS if "qwen" in k]
        assert len(qwen_keys) > 0, "Expected at least one Qwen model"

    def test_ollama_gemma_models_present(self):
        gemma_keys = [k for k in MODEL_COSTS if "gemma" in k]
        assert len(gemma_keys) > 0, "Expected at least one Gemma model"

    def test_all_models_are_free(self):
        """Ollama-only policy: all models should be free (0.0)."""
        for model, cost in MODEL_COSTS.items():
            assert cost == 0.0, f"{model} should be free but costs {cost}"


# ---------------------------------------------------------------------------
# DEFAULT_MODEL_COST
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDefaultModelCost:
    def test_is_positive_float(self):
        assert isinstance(DEFAULT_MODEL_COST, float)
        assert DEFAULT_MODEL_COST > 0

    def test_is_reasonable_value(self):
        # Should be in a sane range (not free, not absurdly expensive)
        assert 0 < DEFAULT_MODEL_COST < 1.0


# ---------------------------------------------------------------------------
# PROVIDER_ICONS
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderIcons:
    def test_is_dict(self):
        assert isinstance(PROVIDER_ICONS, dict)

    def test_all_values_are_strings(self):
        for provider, icon in PROVIDER_ICONS.items():
            assert isinstance(icon, str), f"{provider} icon is not a string"
            assert len(icon) > 0, f"{provider} icon is empty"

    def test_known_providers_present(self):
        for provider in ("ollama",):
            assert provider in PROVIDER_ICONS, f"Missing icon for {provider}"


# ---------------------------------------------------------------------------
# MODEL_FAMILIES
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModelFamilies:
    def test_is_dict(self):
        assert isinstance(MODEL_FAMILIES, dict)

    def test_all_values_are_lists(self):
        for provider, models in MODEL_FAMILIES.items():
            assert isinstance(models, list), f"{provider} families is not a list"

    def test_all_model_lists_non_empty(self):
        for provider, models in MODEL_FAMILIES.items():
            assert len(models) > 0, f"{provider} has an empty model family list"

    def test_all_model_names_are_strings(self):
        for provider, models in MODEL_FAMILIES.items():
            for model in models:
                assert isinstance(model, str), f"{model} in {provider} is not a string"

    def test_known_providers_present(self):
        for provider in ("ollama",):
            assert provider in MODEL_FAMILIES, f"Missing family entry for {provider}"
