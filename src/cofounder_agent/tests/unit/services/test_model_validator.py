"""
Unit tests for services/model_validator.py.

Tests cover:
- ModelValidator.__init__ — with and without available models
- ModelValidator.set_available_models — updates available models dict
- ModelValidator.is_model_available — runtime check, known model fallback, tagged model, unknown
- ModelValidator.validate_model_selection — empty name, None, unknown model, valid known model
- ModelValidator.validate_models_by_phase — all valid, invalid phase, invalid model, empty dict, non-dict
- ModelValidator.get_default_models_for_phase — valid phase, unknown phase
- ModelValidator.get_all_phases — returns correct set
- ModelValidator.estimate_cost_by_phase — free model (zero cost), paid model, custom tokens
- ModelValidator.recommend_models_for_quality_level — budget, balanced, quality, premium, unknown falls back

No external dependencies — purely synchronous logic.
"""

import pytest

from services.model_validator import ModelInfo, ModelValidator


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModelValidatorInit:
    def test_no_available_models_initializes_empty(self):
        validator = ModelValidator()
        assert validator.available_models == {}

    def test_with_available_models(self):
        models = {
            "my-model": ModelInfo(
                name="my-model", provider="openai", available=True, cost_per_token=0.00001
            )
        }
        validator = ModelValidator(available_models=models)
        assert "my-model" in validator.available_models


# ---------------------------------------------------------------------------
# set_available_models
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetAvailableModels:
    def test_updates_available_models(self):
        validator = ModelValidator()
        validator.set_available_models(["llama2", "mistral"])
        assert "llama2" in validator.available_models
        assert "mistral" in validator.available_models

    def test_each_entry_is_model_info(self):
        validator = ModelValidator()
        validator.set_available_models(["gpt-4"])
        model = validator.available_models["gpt-4"]
        assert isinstance(model, ModelInfo)
        assert model.available is True

    def test_replaces_existing_models(self):
        validator = ModelValidator()
        validator.set_available_models(["llama2"])
        validator.set_available_models(["mistral"])
        assert "llama2" not in validator.available_models
        assert "mistral" in validator.available_models


# ---------------------------------------------------------------------------
# is_model_available
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsModelAvailable:
    def test_runtime_available_model_returns_true(self):
        validator = ModelValidator()
        validator.set_available_models(["custom-model"])
        assert validator.is_model_available("custom-model") is True

    def test_unavailable_runtime_model_returns_false(self):
        unavailable = ModelInfo(
            name="bad-model", provider="openai", available=False, cost_per_token=0.0
        )
        validator = ModelValidator(available_models={"bad-model": unavailable})
        assert validator.is_model_available("bad-model") is False

    def test_known_model_not_in_runtime_returns_true(self):
        validator = ModelValidator()
        # No runtime models; should fall back to KNOWN_MODELS
        assert validator.is_model_available("llama2") is True

    def test_tagged_known_model_returns_true(self):
        validator = ModelValidator()
        # "llama2:13b" base is "llama2" which is in KNOWN_MODELS
        assert validator.is_model_available("llama2:13b") is True

    def test_unknown_model_returns_false(self):
        validator = ModelValidator()
        assert validator.is_model_available("does-not-exist-v99") is False

    def test_empty_string_returns_false(self):
        validator = ModelValidator()
        assert validator.is_model_available("") is False


# ---------------------------------------------------------------------------
# validate_model_selection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateModelSelection:
    def test_valid_known_model_returns_true(self):
        validator = ModelValidator()
        is_valid, error = validator.validate_model_selection("llama2")
        assert is_valid is True
        assert error is None

    def test_empty_string_returns_false(self):
        validator = ModelValidator()
        is_valid, error = validator.validate_model_selection("")
        assert is_valid is False
        assert error is not None

    def test_none_returns_false(self):
        validator = ModelValidator()
        is_valid, error = validator.validate_model_selection(None)  # type: ignore[arg-type]
        assert is_valid is False
        assert error is not None

    def test_unknown_model_returns_false_with_message(self):
        validator = ModelValidator()
        is_valid, error = validator.validate_model_selection("unknownai-9000")
        assert is_valid is False
        assert error is not None and "not available" in error

    def test_strips_whitespace(self):
        validator = ModelValidator()
        is_valid, _ = validator.validate_model_selection("  llama2  ")
        assert is_valid is True

    def test_lowercases_model_name(self):
        validator = ModelValidator()
        is_valid, _ = validator.validate_model_selection("LLAMA2")
        assert is_valid is True


# ---------------------------------------------------------------------------
# validate_models_by_phase
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateModelsByPhase:
    def test_all_valid_returns_true_no_errors(self):
        validator = ModelValidator()
        models = {"research": "llama2", "draft": "mistral"}
        is_valid, errors = validator.validate_models_by_phase(models)
        assert is_valid is True
        assert errors == {}

    def test_empty_dict_returns_true(self):
        validator = ModelValidator()
        is_valid, errors = validator.validate_models_by_phase({})
        assert is_valid is True
        assert errors == {}

    def test_invalid_phase_name_returned_in_errors(self):
        validator = ModelValidator()
        models = {"invalid_phase": "llama2"}
        is_valid, errors = validator.validate_models_by_phase(models)
        assert is_valid is False
        assert "invalid_phase" in errors

    def test_invalid_model_for_valid_phase_returned_in_errors(self):
        validator = ModelValidator()
        models = {"research": "nonexistent-ai"}
        is_valid, errors = validator.validate_models_by_phase(models)
        assert is_valid is False
        assert "research" in errors

    def test_non_dict_input_returns_false(self):
        validator = ModelValidator()
        is_valid, errors = validator.validate_models_by_phase("not-a-dict")  # type: ignore[arg-type]
        assert is_valid is False
        assert "format" in errors

    def test_mixed_valid_and_invalid_phases(self):
        validator = ModelValidator()
        models = {
            "research": "llama2",  # valid
            "bad_phase": "llama2",  # invalid phase
            "draft": "fake-model",  # invalid model
        }
        is_valid, errors = validator.validate_models_by_phase(models)
        assert is_valid is False
        assert "bad_phase" in errors
        assert "draft" in errors
        assert "research" not in errors


# ---------------------------------------------------------------------------
# get_default_models_for_phase
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDefaultModelsForPhase:
    def test_returns_default_for_research(self):
        validator = ModelValidator()
        result = validator.get_default_models_for_phase("research")
        assert result == "llama2"

    def test_returns_default_for_draft(self):
        validator = ModelValidator()
        result = validator.get_default_models_for_phase("draft")
        assert result == "mistral"

    def test_unknown_phase_returns_none(self):
        validator = ModelValidator()
        result = validator.get_default_models_for_phase("nonexistent_phase")
        assert result is None

    def test_all_pipeline_phases_have_defaults(self):
        validator = ModelValidator()
        for phase in validator.PIPELINE_PHASES:
            result = validator.get_default_models_for_phase(phase)
            assert result is not None, f"Phase '{phase}' has no default model"


# ---------------------------------------------------------------------------
# get_all_phases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAllPhases:
    def test_returns_set_of_all_phases(self):
        validator = ModelValidator()
        phases = validator.get_all_phases()
        assert "research" in phases
        assert "draft" in phases
        assert "assess" in phases
        assert "refine" in phases
        assert "finalize" in phases
        assert "outline" in phases

    def test_returns_copy_not_reference(self):
        """Modifying the returned set should not affect the class."""
        validator = ModelValidator()
        phases = validator.get_all_phases()
        phases.add("bad_phase")
        # Original should still be unchanged
        assert "bad_phase" not in validator.PIPELINE_PHASES


# ---------------------------------------------------------------------------
# estimate_cost_by_phase
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEstimateCostByPhase:
    def test_free_models_return_zero_cost(self):
        validator = ModelValidator()
        # llama2 is an Ollama model with cost 0.0
        models = {"research": "llama2", "draft": "llama2"}
        cost = validator.estimate_cost_by_phase(models)
        assert cost == 0.0

    def test_paid_model_returns_nonzero_cost(self):
        validator = ModelValidator()
        models = {"draft": "gpt-4"}
        cost = validator.estimate_cost_by_phase(models)
        assert cost > 0.0

    def test_custom_tokens_affects_cost(self):
        validator = ModelValidator()
        models = {"draft": "gpt-4"}
        cost_1000 = validator.estimate_cost_by_phase(models, {"draft": 1000})
        cost_2000 = validator.estimate_cost_by_phase(models, {"draft": 2000})
        assert cost_2000 == pytest.approx(cost_1000 * 2, rel=1e-3)

    def test_runtime_available_model_uses_model_info_cost(self):
        """Models in available_models use ModelInfo.cost_per_token."""
        model_info = ModelInfo(
            name="custom-model", provider="openai", available=True, cost_per_token=0.001
        )
        validator = ModelValidator(available_models={"custom-model": model_info})
        cost = validator.estimate_cost_by_phase({"draft": "custom-model"}, {"draft": 100})
        assert cost == pytest.approx(0.001 * 100, rel=1e-6)

    def test_empty_phases_returns_zero(self):
        validator = ModelValidator()
        cost = validator.estimate_cost_by_phase({})
        assert cost == 0.0


# ---------------------------------------------------------------------------
# recommend_models_for_quality_level
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecommendModelsForQualityLevel:
    def test_budget_uses_llama2_everywhere(self):
        validator = ModelValidator()
        recs = validator.recommend_models_for_quality_level("budget")
        assert all(model == "llama2" for model in recs.values())

    def test_premium_includes_high_quality_models(self):
        validator = ModelValidator()
        recs = validator.recommend_models_for_quality_level("premium")
        # Premium uses claude-3-opus and gpt-4
        all_models = set(recs.values())
        assert "claude-3-opus" in all_models or "gpt-4" in all_models

    def test_quality_level_includes_all_phases(self):
        validator = ModelValidator()
        for level in ("budget", "balanced", "quality", "premium"):
            recs = validator.recommend_models_for_quality_level(level)
            for phase in validator.PIPELINE_PHASES:
                assert phase in recs, f"Phase '{phase}' missing from {level} recommendations"

    def test_unknown_quality_level_falls_back_to_balanced(self):
        validator = ModelValidator()
        balanced = validator.recommend_models_for_quality_level("balanced")
        unknown = validator.recommend_models_for_quality_level("super_ultra_premium")
        assert unknown == balanced
