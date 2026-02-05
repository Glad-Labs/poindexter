"""
Metrics Service for Glad Labs AI Co-Founder

This module provides centralized metrics collection and reporting.
"""

from typing import Dict, Any, Optional

# Import configuration
from config import get_config

# Get configuration
config = get_config()


class MetricsService:
    """Centralized metrics collection service."""
    
    def __init__(self):
        self._metrics: Dict[str, Any] = {}
    
    async def get_metrics(self) -> Dict[str, Any]:
        """Get aggregated task and system metrics."""
        # This would typically query the database for metrics
        # For now, we return mock metrics
        return {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "pending_tasks": 0,
            "success_rate": 0.0,
            "avg_execution_time": 0.0,
            "total_cost": 0.0,
        }
    
    def update_metrics(self, **kwargs) -> None:
        """Update metrics with new values."""
        self._metrics.update(kwargs)
    
    def get_metric(self, key: str) -> Any:
        """Get a specific metric value."""
        return self._metrics.get(key)


# Global metrics service instance
metrics_service = MetricsService()


def get_metrics_service() -> MetricsService:
    """Get the global metrics service instance."""
    return metrics_service