"""
Metrics Collection Service (Sprint 5)

Collects and stores performance metrics for task execution:
- Phase execution times (research, draft, assess, refine, finalize, publish)
- LLM API call metrics (tokens, costs, duration)
- Error rates and retry attempts
- Queue wait times
- Resource utilization

Metrics are stored in the admin_logs table with structured JSONB format.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from config import get_config

logger = logging.getLogger(__name__)
config = get_config()


class TaskMetrics:
    """Collects metrics for a single task execution"""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.start_time = datetime.now(timezone.utc).isoformat()
        self.phases: Dict[str, Dict[str, Any]] = {}
        self.llm_calls: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self.queue_wait_ms = 0
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.total_cost_usd = 0.0

    def record_phase_start(self, phase_name: str) -> float:
        """Record phase start time. Returns start_time timestamp for later calculation."""
        return time.time()

    def record_phase_end(
        self,
        phase_name: str,
        start_time: float,
        status: str = "success",
        error: Optional[str] = None,
    ):
        """Record phase completion with duration."""
        duration_ms = (time.time() - start_time) * 1000

        self.phases[phase_name] = {
            "duration_ms": round(duration_ms, 2),
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if error:
            self.phases[phase_name]["error"] = error
            self.record_error(phase=phase_name, error_type="PhaseError", error_message=error)

        logger.debug(f"📊 [METRICS] Phase '{phase_name}' completed in {duration_ms:.0f}ms")

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
        error: Optional[str] = None,
    ):
        """Record an LLM API call with token usage and cost."""
        self.llm_calls.append(
            {
                "llm_call_id": str(uuid4()),
                "phase": phase,
                "model": model,
                "provider": provider,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "total_tokens": tokens_in + tokens_out,
                "cost_usd": round(cost_usd, 6),
                "duration_ms": round(duration_ms, 2),
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        if error:
            self.llm_calls[-1]["error"] = error

        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        self.total_cost_usd += cost_usd

        logger.debug(
            f"📊 [METRICS] LLM call ({provider}/{model}): {tokens_in}→{tokens_out} tokens, "
            f"${cost_usd:.4f}, {duration_ms:.0f}ms"
        )

    def record_error(self, phase: str, error_type: str, error_message: str, retry_count: int = 0):
        """Record an error that occurred during task execution."""
        self.errors.append(
            {
                "error_id": str(uuid4()),
                "phase": phase,
                "error_type": error_type,
                "error_message": error_message,
                "retry_count": retry_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        logger.warning(
            f"⚠️ [METRICS] Error {error_type} in '{phase}': {error_message} (retries: {retry_count})"
        )

    def record_queue_wait(self, wait_ms: float):
        """Record how long the task waited in queue before execution."""
        self.queue_wait_ms = round(wait_ms, 2)
        logger.debug(f"📊 [METRICS] Queue wait: {wait_ms:.0f}ms")

    def get_total_duration_ms(self) -> float:
        """Calculate total execution time including queue wait."""
        phase_duration = sum(phase.get("duration_ms", 0) for phase in self.phases.values())
        return self.queue_wait_ms + phase_duration

    def get_phase_breakdown(self) -> Dict[str, float]:
        """Get duration for each phase."""
        return {
            phase: phase_data.get("duration_ms", 0) for phase, phase_data in self.phases.items()
        }

    def get_error_count(self) -> int:
        """Get total number of errors."""
        return len(self.errors)

    def get_error_rate(self) -> float:
        """Get error rate as percentage (0.0 to 1.0)."""
        total_llm_calls = len(self.llm_calls)
        if total_llm_calls == 0:
            return 0.0
        error_calls = sum(1 for call in self.llm_calls if call.get("status") == "error")
        return error_calls / total_llm_calls

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary for storage."""
        return {
            "task_id": self.task_id,
            "start_time": self.start_time,
            "end_time": datetime.now(timezone.utc).isoformat(),
            "total_duration_ms": round(self.get_total_duration_ms(), 2),
            "queue_wait_ms": self.queue_wait_ms,
            "phase_breakdown": self.get_phase_breakdown(),
            "phases": self.phases,
            "llm_calls": self.llm_calls,
            "llm_stats": {
                "total_calls": len(self.llm_calls),
                "successful_calls": sum(
                    1 for call in self.llm_calls if call.get("status") == "success"
                ),
                "failed_calls": sum(1 for call in self.llm_calls if call.get("status") == "error"),
                "total_tokens_in": self.total_tokens_in,
                "total_tokens_out": self.total_tokens_out,
                "total_tokens": self.total_tokens_in + self.total_tokens_out,
                "total_cost_usd": round(self.total_cost_usd, 6),
                "avg_cost_per_call": (
                    round(self.total_cost_usd / len(self.llm_calls), 6) if self.llm_calls else 0.0
                ),
                "error_rate": round(self.get_error_rate(), 4),
            },
            "errors": self.errors,
            "error_count": self.get_error_count(),
        }


class MetricsService:
    """Service for storing and retrieving task execution metrics."""

    def __init__(self, database_service=None):
        self.database_service = database_service
        self._metrics: Dict[str, Any] = {}

    async def get_metrics(self) -> Dict[str, Any]:
        """Get aggregated task and system metrics."""
        return {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "pending_tasks": 0,
            "success_rate": 0.0,
            "avg_execution_time": 0.0,
            "total_cost": 0.0,
        }

    async def save_metrics(self, metrics: TaskMetrics) -> bool:
        """Save task execution metrics to database."""
        try:
            metrics_dict = metrics.to_dict()

            # Log to admin_logs table
            if self.database_service and hasattr(self.database_service, "log"):
                log_entry = {
                    "user_id": None,
                    "action": "task_execution_metrics",
                    "resource_type": "task",
                    "resource_id": metrics.task_id,
                    "details": {
                        "total_duration_ms": metrics_dict["total_duration_ms"],
                        "phase_count": len(metrics.phases),
                        "llm_call_count": len(metrics.llm_calls),
                        "error_count": metrics.get_error_count(),
                    },
                    "metric_type": "task_execution",
                    "metric_value": metrics_dict["total_duration_ms"],
                    "metric_context": metrics_dict,
                    "status": "completed",
                }
                await self.database_service.log(**log_entry)

            logger.info(
                f"✅ [METRICS] Saved metrics for task {metrics.task_id}: "
                f"{metrics_dict['total_duration_ms']:.0f}ms, "
                f"{len(metrics.llm_calls)} LLM calls, "
                f"${metrics_dict['llm_stats']['total_cost_usd']:.4f}"
            )

            return True
        except Exception as e:
            logger.error(f"❌ Failed to save metrics: {str(e)}", exc_info=True)
            return False

    def update_metrics(self, **kwargs) -> None:
        """Update metrics with new values."""
        self._metrics.update(kwargs)

    def get_metric(self, key: str) -> Any:
        """Get a specific metric value."""
        return self._metrics.get(key)


# Global metrics service instance
metrics_service: Optional[MetricsService] = None


def get_metrics_service(database_service=None) -> MetricsService:
    """Get or create the global metrics service instance."""
    global metrics_service
    if metrics_service is None:
        metrics_service = MetricsService(database_service)
    return metrics_service
