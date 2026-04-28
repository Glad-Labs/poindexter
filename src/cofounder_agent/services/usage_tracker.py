"""
Usage Tracking Service

Tracks token usage, execution duration, and costs across AI operations.

Features:
- Token counting for different model providers
- Duration tracking with millisecond precision
- Cost calculation based on model and token count
- Per-operation metrics storage
- Aggregation for analytics
"""

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from services.cost_lookup import get_model_cost_per_1k
from services.logger_config import get_logger

logger = get_logger(__name__)


@dataclass
class UsageMetrics:
    """Track metrics for a single operation"""

    operation_id: str
    operation_type: str  # "chat", "generation", "research", etc.
    model_name: str
    model_provider: str  # "ollama", "openai", "google", etc.

    # Token tracking
    input_tokens: int = 0
    output_tokens: int = 0

    # Duration tracking
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None

    # Cost tracking
    input_cost_usd: float = 0.0  # Per 1K input tokens
    output_cost_usd: float = 0.0  # Per 1K output tokens

    # Results
    total_cost_usd: float = 0.0
    duration_ms: int = 0
    success: bool = True
    error: str | None = None

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def complete(self):
        """Mark operation as complete and calculate metrics"""
        self.end_time = time.time()
        self.duration_ms = int((self.end_time - self.start_time) * 1000)

        # Calculate costs
        input_cost = (self.input_tokens / 1000.0) * self.input_cost_usd
        output_cost = (self.output_tokens / 1000.0) * self.output_cost_usd
        self.total_cost_usd = input_cost + output_cost

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage"""
        return asdict(self)


class UsageTracker:
    """Track and aggregate usage metrics.

    Per-token pricing comes from ``services.cost_lookup`` (LiteLLM-backed
    post-#199 Phase 1) — covers 2,600+ provider/model combos
    out-of-the-box, with a $0 fallback for local Ollama routes and a
    conservative default for unknown models.

    The legacy 3-entry ``MODEL_PRICING`` dict was deleted; it only
    matched ``model_name.lower().split('-')[0]`` which was brittle
    (e.g. "claude-haiku-4-5" matched "claude" → not in dict → $0,
    silently treating Anthropic calls as free).
    """

    def __init__(self):
        """Initialize usage tracker"""
        self.active_operations: dict[str, UsageMetrics] = {}
        self.completed_operations: list[UsageMetrics] = []
        logger.info("Usage tracker initialized")

    def start_operation(
        self,
        operation_id: str,
        operation_type: str,
        model_name: str,
        model_provider: str = "ollama",
        metadata: dict[str, Any] | None = None,
    ) -> UsageMetrics:
        """
        Start tracking an operation.

        Args:
            operation_id: Unique operation identifier
            operation_type: Type of operation (chat, generation, etc.)
            model_name: Name of the model being used
            model_provider: Provider of the model
            metadata: Additional metadata

        Returns:
            UsageMetrics object to track this operation
        """
        input_cost, output_cost = get_model_cost_per_1k(model_name)

        metrics = UsageMetrics(
            operation_id=operation_id,
            operation_type=operation_type,
            model_name=model_name,
            model_provider=model_provider,
            input_cost_usd=input_cost,
            output_cost_usd=output_cost,
            metadata=metadata or {},
        )

        self.active_operations[operation_id] = metrics
        logger.debug("Started tracking operation: %s (%s)", operation_id, operation_type)

        return metrics

    def add_tokens(
        self,
        operation_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> bool:
        """
        Add token counts to an operation.

        Args:
            operation_id: Operation identifier
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            True if successful, False if operation not found
        """
        if operation_id not in self.active_operations:
            logger.warning("Operation not found: %s", operation_id)
            return False

        metrics = self.active_operations[operation_id]
        metrics.input_tokens += input_tokens
        metrics.output_tokens += output_tokens

        return True

    def end_operation(
        self,
        operation_id: str,
        success: bool = True,
        error: str | None = None,
    ) -> UsageMetrics | None:
        """
        Complete an operation and move to history.

        Args:
            operation_id: Operation identifier
            success: Whether operation succeeded
            error: Error message if failed

        Returns:
            Completed UsageMetrics or None if not found
        """
        if operation_id not in self.active_operations:
            logger.warning("Operation not found: %s", operation_id)
            return None

        metrics = self.active_operations.pop(operation_id)
        metrics.success = success
        metrics.error = error
        metrics.complete()

        self.completed_operations.append(metrics)

        logger.debug(
            "Completed operation: %s (%dms, %.4fUSD, %d tokens)",
            operation_id,
            metrics.duration_ms,
            metrics.total_cost_usd,
            metrics.input_tokens + metrics.output_tokens,
        )

        return metrics

    def get_operation_metrics(self, operation_id: str) -> UsageMetrics | None:
        """Get metrics for an operation (active or completed)"""
        if operation_id in self.active_operations:
            return self.active_operations[operation_id]

        for metrics in self.completed_operations:
            if metrics.operation_id == operation_id:
                return metrics

        return None

    def get_summary(
        self,
        operation_type: str | None = None,
        model_name: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        Get summary statistics.

        Args:
            operation_type: Filter by operation type
            model_name: Filter by model name
            limit: Number of recent operations to include

        Returns:
            Summary dictionary with aggregated metrics
        """
        operations = self.completed_operations[-limit:]

        if operation_type:
            operations = [o for o in operations if o.operation_type == operation_type]

        if model_name:
            operations = [o for o in operations if o.model_name == model_name]

        if not operations:
            return {
                "count": 0,
                "total_tokens": 0,
                "total_cost_usd": 0,
                "average_duration_ms": 0,
            }

        total_tokens = sum(o.input_tokens + o.output_tokens for o in operations)
        total_cost = sum(o.total_cost_usd for o in operations)
        avg_duration = sum(o.duration_ms for o in operations) / len(operations)
        success_count = sum(1 for o in operations if o.success)

        return {
            "count": len(operations),
            "success_count": success_count,
            "failure_count": len(operations) - success_count,
            "success_rate": (success_count / len(operations)) * 100,
            "total_tokens": total_tokens,
            "input_tokens": sum(o.input_tokens for o in operations),
            "output_tokens": sum(o.output_tokens for o in operations),
            "total_cost_usd": round(total_cost, 4),
            "average_cost_usd": round(total_cost / len(operations), 4),
            "average_duration_ms": int(avg_duration),
            "by_operation": self._group_by(operations, "operation_type"),
            "by_model": self._group_by(operations, "model_name"),
        }

    @staticmethod
    def _group_by(operations: list[UsageMetrics], key: str) -> dict[str, Any]:
        """Group operations by a field"""
        groups = {}

        for op in operations:
            group_key = getattr(op, key)
            if group_key not in groups:
                groups[group_key] = {
                    "count": 0,
                    "tokens": 0,
                    "cost_usd": 0,
                    "avg_duration_ms": 0,
                }

            groups[group_key]["count"] += 1
            groups[group_key]["tokens"] += op.input_tokens + op.output_tokens
            groups[group_key]["cost_usd"] += op.total_cost_usd

        # Calculate averages
        for group in groups.values():
            group["avg_cost_usd"] = round(group["cost_usd"] / group["count"], 4)

        return groups


# Global tracker instance
_tracker: UsageTracker | None = None


def get_usage_tracker() -> UsageTracker:
    """Get or create global usage tracker"""
    global _tracker
    if _tracker is None:
        _tracker = UsageTracker()
    return _tracker
