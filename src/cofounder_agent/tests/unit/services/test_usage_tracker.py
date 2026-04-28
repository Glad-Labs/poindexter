"""
Unit tests for services/usage_tracker.py

Tests UsageMetrics and UsageTracker:
- start_operation creates active operation
- add_tokens accumulates correctly
- end_operation moves to history and calculates cost/duration
- get_operation_metrics searches active and completed
- get_summary aggregates correctly with optional filters
- _group_by groups by field and calculates averages
- get_usage_tracker returns the same singleton

All tests are pure synchronous — no DB, network, or async calls.
"""

import pytest

from services.usage_tracker import UsageMetrics, UsageTracker, get_usage_tracker

# ---------------------------------------------------------------------------
# UsageMetrics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUsageMetrics:
    def test_initial_state(self):
        m = UsageMetrics(
            operation_id="op-1",
            operation_type="chat",
            model_name="gpt-4",
            model_provider="openai",
        )
        assert m.input_tokens == 0
        assert m.output_tokens == 0
        assert m.total_cost_usd == 0.0
        assert m.success is True
        assert m.error is None

    def test_complete_sets_duration_ms(self):
        m = UsageMetrics(
            operation_id="op-2",
            operation_type="generation",
            model_name="ollama/llama2",
            model_provider="ollama",
        )
        m.input_tokens = 100
        m.output_tokens = 50
        m.input_cost_usd = 0.03  # per 1K tokens
        m.output_cost_usd = 0.06  # per 1K tokens

        m.complete()

        # duration_ms should be >= 0
        assert m.duration_ms >= 0
        assert m.end_time is not None

    def test_complete_calculates_cost(self):
        m = UsageMetrics(
            operation_id="op-3",
            operation_type="chat",
            model_name="gpt-4",
            model_provider="openai",
        )
        m.input_tokens = 1000
        m.output_tokens = 500
        m.input_cost_usd = 0.03  # $0.03 per 1K input → $0.03 for 1K tokens
        m.output_cost_usd = 0.06  # $0.06 per 1K output → $0.03 for 500 tokens

        m.complete()

        expected_cost = (1000 / 1000.0) * 0.03 + (500 / 1000.0) * 0.06
        assert abs(m.total_cost_usd - expected_cost) < 1e-9

    def test_to_dict_returns_dict(self):
        m = UsageMetrics(
            operation_id="op-4",
            operation_type="research",
            model_name="claude-3-sonnet",
            model_provider="anthropic",
        )
        d = m.to_dict()
        assert isinstance(d, dict)
        assert d["operation_id"] == "op-4"
        assert d["model_name"] == "claude-3-sonnet"

    def test_zero_tokens_zero_cost(self):
        m = UsageMetrics(
            operation_id="op-5",
            operation_type="ping",
            model_name="ollama/phi",
            model_provider="ollama",
        )
        m.complete()
        assert m.total_cost_usd == 0.0


# ---------------------------------------------------------------------------
# UsageTracker — start_operation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUsageTrackerStartOperation:
    def setup_method(self):
        self.tracker = UsageTracker()

    def test_returns_usage_metrics_object(self):
        m = self.tracker.start_operation("op-a", "chat", "gpt-4", "openai")
        assert isinstance(m, UsageMetrics)

    def test_registers_in_active_operations(self):
        self.tracker.start_operation("op-b", "chat", "gpt-4", "openai")
        assert "op-b" in self.tracker.active_operations

    def test_metadata_is_stored(self):
        m = self.tracker.start_operation(
            "op-c", "chat", "gpt-4", "openai", metadata={"session": "s1"}
        )
        assert m.metadata == {"session": "s1"}

    def test_unknown_model_uses_default_pricing(self):
        """Pre-#199 fell back to $0 for any unknown model — that silently
        treated paid cloud calls as free. Post-#199 cost_lookup returns
        ``DEFAULT_COST_PER_1K`` for unknown non-local models so the
        cost path is conservative."""
        from services.cost_lookup import DEFAULT_COST_PER_1K
        m = self.tracker.start_operation("op-d", "chat", "unknown-model-xyz", "unknown")
        assert m.input_cost_usd == DEFAULT_COST_PER_1K
        assert m.output_cost_usd == DEFAULT_COST_PER_1K

    def test_local_ollama_route_is_free(self):
        """Local Ollama route always resolves to $0 — GPU electricity
        is tracked separately via the cost_logs ``electricity`` provider
        rows, not as per-token inference cost."""
        m = self.tracker.start_operation(
            "op-d-local", "chat", "ollama/qwen3.5:35b", "ollama",
        )
        assert m.input_cost_usd == 0.0
        assert m.output_cost_usd == 0.0


# ---------------------------------------------------------------------------
# UsageTracker — add_tokens
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUsageTrackerAddTokens:
    def setup_method(self):
        self.tracker = UsageTracker()

    def test_accumulates_tokens(self):
        self.tracker.start_operation("op-t1", "generation", "gpt-4", "openai")
        self.tracker.add_tokens("op-t1", input_tokens=100, output_tokens=50)
        self.tracker.add_tokens("op-t1", input_tokens=200, output_tokens=75)

        m = self.tracker.active_operations["op-t1"]
        assert m.input_tokens == 300
        assert m.output_tokens == 125

    def test_returns_false_for_unknown_operation(self):
        result = self.tracker.add_tokens("nonexistent", input_tokens=10)
        assert result is False

    def test_returns_true_for_known_operation(self):
        self.tracker.start_operation("op-t2", "chat", "gpt-3.5-turbo", "openai")
        result = self.tracker.add_tokens("op-t2", input_tokens=50)
        assert result is True


# ---------------------------------------------------------------------------
# UsageTracker — end_operation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUsageTrackerEndOperation:
    def setup_method(self):
        self.tracker = UsageTracker()

    def test_moves_to_completed(self):
        self.tracker.start_operation("op-e1", "chat", "gpt-4", "openai")
        self.tracker.end_operation("op-e1")
        assert "op-e1" not in self.tracker.active_operations
        assert any(m.operation_id == "op-e1" for m in self.tracker.completed_operations)

    def test_returns_metrics_on_success(self):
        self.tracker.start_operation("op-e2", "chat", "gpt-4", "openai")
        m = self.tracker.end_operation("op-e2", success=True)
        assert m is not None
        assert m.success is True

    def test_stores_error_message(self):
        self.tracker.start_operation("op-e3", "chat", "gpt-4", "openai")
        m = self.tracker.end_operation("op-e3", success=False, error="timeout")
        assert m is not None
        assert m.success is False
        assert m.error == "timeout"

    def test_returns_none_for_unknown_operation(self):
        result = self.tracker.end_operation("does-not-exist")
        assert result is None

    def test_duration_ms_is_populated(self):
        self.tracker.start_operation("op-e4", "chat", "gpt-4", "openai")
        m = self.tracker.end_operation("op-e4")
        assert m is not None
        assert m.duration_ms >= 0


# ---------------------------------------------------------------------------
# UsageTracker — get_operation_metrics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUsageTrackerGetMetrics:
    def setup_method(self):
        self.tracker = UsageTracker()

    def test_finds_active_operation(self):
        self.tracker.start_operation("op-m1", "chat", "gpt-4", "openai")
        m = self.tracker.get_operation_metrics("op-m1")
        assert m is not None
        assert m.operation_id == "op-m1"

    def test_finds_completed_operation(self):
        self.tracker.start_operation("op-m2", "chat", "gpt-4", "openai")
        self.tracker.end_operation("op-m2")
        m = self.tracker.get_operation_metrics("op-m2")
        assert m is not None
        assert m.operation_id == "op-m2"

    def test_returns_none_for_unknown(self):
        m = self.tracker.get_operation_metrics("ghost-op")
        assert m is None


# ---------------------------------------------------------------------------
# UsageTracker — get_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUsageTrackerGetSummary:
    def setup_method(self):
        self.tracker = UsageTracker()

    def _complete_op(
        self,
        op_id: str,
        op_type: str,
        model: str = "gpt-4",
        input_tokens: int = 100,
        output_tokens: int = 50,
    ):
        self.tracker.start_operation(op_id, op_type, model, "openai")
        self.tracker.add_tokens(op_id, input_tokens=input_tokens, output_tokens=output_tokens)
        return self.tracker.end_operation(op_id)

    def test_empty_tracker_returns_zeros(self):
        summary = self.tracker.get_summary()
        assert summary["count"] == 0
        assert summary["total_tokens"] == 0
        assert summary["total_cost_usd"] == 0

    def test_summary_counts_operations(self):
        self._complete_op("s1", "chat")
        self._complete_op("s2", "chat")
        summary = self.tracker.get_summary()
        assert summary["count"] == 2

    def test_summary_totals_tokens(self):
        self._complete_op("s3", "research", input_tokens=200, output_tokens=100)
        summary = self.tracker.get_summary()
        assert summary["total_tokens"] >= 300  # At least what we added

    def test_filter_by_operation_type(self):
        self._complete_op("s4", "chat")
        self._complete_op("s5", "generation")
        self._complete_op("s6", "generation")
        summary = self.tracker.get_summary(operation_type="generation")
        assert summary["count"] == 2

    def test_filter_by_model_name(self):
        self._complete_op("s7", "chat", model="gpt-4")
        self._complete_op("s8", "chat", model="gpt-3.5-turbo")
        summary = self.tracker.get_summary(model_name="gpt-4")
        assert summary["count"] == 1

    def test_success_rate_all_success(self):
        self._complete_op("s9", "chat")
        self._complete_op("s10", "chat")
        summary = self.tracker.get_summary()
        assert summary["success_rate"] == 100.0

    def test_summary_by_operation_groups_correctly(self):
        self._complete_op("s11", "chat")
        self._complete_op("s12", "generation")
        summary = self.tracker.get_summary()
        assert "chat" in summary["by_operation"]
        assert "generation" in summary["by_operation"]

    def test_limit_caps_operations(self):
        for i in range(10):
            self._complete_op(f"sl{i}", "chat")
        summary = self.tracker.get_summary(limit=3)
        assert summary["count"] <= 3


# ---------------------------------------------------------------------------
# get_usage_tracker singleton
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetUsageTrackerSingleton:
    def test_returns_same_instance(self):
        t1 = get_usage_tracker()
        t2 = get_usage_tracker()
        assert t1 is t2

    def test_returns_usage_tracker_instance(self):
        t = get_usage_tracker()
        assert isinstance(t, UsageTracker)
