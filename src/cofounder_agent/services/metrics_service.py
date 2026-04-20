"""
Metrics Service for Poindexter (the AI cofounder pipeline).

This module provides centralized metrics collection and reporting.
"""

import time
from datetime import datetime, timezone
from typing import Any

# Import configuration
from config import get_config
from services.logger_config import get_logger

# Get configuration
config = get_config()
logger = get_logger(__name__)


class TaskMetrics:
    """Per-task metrics: phases, LLM calls, errors, and timing."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.start_time: float = time.time()
        self.end_time: float | None = None
        self.queue_wait_ms: float = 0.0
        self.phases: dict[str, Any] = {}
        self.llm_calls: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.total_tokens_in: int = 0
        self.total_tokens_out: int = 0
        self.total_cost_usd: float = 0.0

    # --- Phase tracking ---

    def record_phase_start(self, phase: str) -> float:
        """Record start of a phase; return start timestamp (monotonic)."""
        ts = time.monotonic()
        self.phases.setdefault(phase, {})["_start"] = ts
        return ts

    def record_phase_end(
        self,
        phase: str,
        start_ts: float,
        status: str = "success",
        error: str | None = None,
    ) -> None:
        """Record end of a phase."""
        duration_ms = (time.monotonic() - start_ts) * 1000
        entry = self.phases.setdefault(phase, {})
        entry["duration_ms"] = duration_ms
        entry["status"] = status
        if error:
            entry["error"] = error
            self.errors.append(
                {"phase": phase, "error_type": "phase_error", "message": error, "retry_count": 0}
            )

    # --- LLM call tracking ---

    def record_llm_call(
        self,
        phase: str,
        model: str,
        provider: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        duration_ms: float,
        status: str = "success",
        error: str | None = None,
    ) -> None:
        """Record a single LLM API call."""
        entry: dict[str, Any] = {
            "phase": phase,
            "model": model,
            "provider": provider,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "total_tokens": tokens_in + tokens_out,
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
            "status": status,
        }
        if error:
            entry["error"] = error
        self.llm_calls.append(entry)
        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        self.total_cost_usd += cost_usd

    # --- Error tracking ---

    def record_error(
        self,
        phase: str,
        error_type: str,
        message: str,
        retry_count: int = 0,
    ) -> None:
        """Record a non-LLM error."""
        self.errors.append(
            {
                "phase": phase,
                "error_type": error_type,
                "message": message,
                "retry_count": retry_count,
            }
        )

    def get_error_count(self) -> int:
        return len(self.errors)

    # --- Duration helpers ---

    def record_queue_wait(self, duration_ms: float) -> None:
        self.queue_wait_ms = duration_ms

    def get_total_duration_ms(self) -> float:
        phase_total = sum(p.get("duration_ms", 0) for p in self.phases.values())
        return self.queue_wait_ms + phase_total

    def get_phase_breakdown(self) -> dict[str, float]:
        return {name: data.get("duration_ms", 0) for name, data in self.phases.items()}

    # --- Error rate ---

    def get_error_rate(self) -> float:
        if not self.llm_calls:
            return 0.0
        error_calls = sum(1 for c in self.llm_calls if c.get("status") == "error")
        return error_calls / len(self.llm_calls)

    # --- Serialization ---

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "start_time": datetime.fromtimestamp(self.start_time, tz=timezone.utc).isoformat(),
            "end_time": (
                datetime.fromtimestamp(self.end_time, tz=timezone.utc).isoformat()
                if self.end_time
                else None
            ),
            "total_duration_ms": self.get_total_duration_ms(),
            "queue_wait_ms": self.queue_wait_ms,
            "phases": self.phases,
            "llm_calls": self.llm_calls,
            "llm_stats": {
                "total_calls": len(self.llm_calls),
                "total_tokens_in": self.total_tokens_in,
                "total_tokens_out": self.total_tokens_out,
                "total_cost_usd": self.total_cost_usd,
                "error_rate": self.get_error_rate(),
            },
            "errors": self.errors,
            "error_count": self.get_error_count(),
        }


class MetricsService:
    """Centralized metrics collection service."""

    def __init__(self, database_service: Any | None = None) -> None:
        self._metrics: dict[str, Any] = {}
        self._database_service = database_service

    async def save_metrics(self, task_metrics: "TaskMetrics") -> bool:
        """Persist task metrics. Returns True on success, False on failure."""
        if self._database_service is None:
            return True
        try:
            if hasattr(self._database_service, "log"):
                await self._database_service.log(task_metrics.to_dict())
            return True
        except Exception as e:
            logger.error("[save_metrics] Failed to persist metrics: %s", e, exc_info=True)
            return False

    async def get_metrics(self) -> dict[str, Any]:
        """Get aggregated task and system metrics from the database.

        Delegates to DatabaseService.get_metrics() when a database_service is
        wired in. Returns zero-value defaults when no database is available
        (e.g., unit tests or bare instantiation).
        """
        _zero = {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "pending_tasks": 0,
            "success_rate": 0.0,
            "avg_execution_time": 0.0,
            "total_cost": 0.0,
        }
        if self._database_service is None:
            return _zero
        try:
            db_metrics = await self._database_service.get_metrics()
            # DatabaseService.get_metrics() returns a MetricsResponse Pydantic model
            # or a plain dict; normalize to the expected flat dict shape.
            if hasattr(db_metrics, "model_dump"):
                raw = db_metrics.model_dump()
            elif hasattr(db_metrics, "dict"):
                raw = db_metrics.dict()
            elif isinstance(db_metrics, dict):
                raw = db_metrics
            else:
                return _zero
            # Map camelCase DB keys to snake_case surface API
            return {
                "total_tasks": raw.get("totalTasks", raw.get("total_tasks", 0)),
                "completed_tasks": raw.get("completedTasks", raw.get("completed_tasks", 0)),
                "failed_tasks": raw.get("failedTasks", raw.get("failed_tasks", 0)),
                "pending_tasks": raw.get("pendingTasks", raw.get("pending_tasks", 0)),
                "success_rate": raw.get("successRate", raw.get("success_rate", 0.0)),
                "avg_execution_time": raw.get(
                    "avgExecutionTime", raw.get("avg_execution_time", 0.0)
                ),
                "total_cost": raw.get("totalCost", raw.get("total_cost", 0.0)),
            }
        except Exception as e:
            logger.error(
                "[get_metrics] Failed to retrieve metrics from database: %s", e, exc_info=True
            )
            return _zero

    def update_metrics(self, **kwargs) -> None:
        """Update metrics with new values."""
        self._metrics.update(kwargs)

    def get_metric(self, key: str) -> Any:
        """Get a specific metric value."""
        return self._metrics.get(key)


# Global metrics service instance
metrics_service = MetricsService()


def get_metrics_service() -> MetricsService:
    """Get the global metrics service instance (singleton)."""
    global metrics_service
    if metrics_service is None:
        metrics_service = MetricsService()
    return metrics_service
