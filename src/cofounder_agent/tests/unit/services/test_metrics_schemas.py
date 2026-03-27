"""
Unit tests for metrics_schemas.py

Tests field validation and model behaviour for metrics schemas.
"""

import pytest
from pydantic import ValidationError

from schemas.metrics_schemas import CostMetric, CostsResponse, HealthMetrics, PerformanceMetrics


@pytest.mark.unit
class TestCostMetric:
    def test_valid(self):
        metric = CostMetric(
            model_name="gpt-4",
            provider="openai",
            tokens_used=1500,
            cost_usd=0.045,
            timestamp="2026-01-01T10:00:00Z",
        )
        assert metric.model_name == "gpt-4"
        assert metric.cost_usd == 0.045

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            CostMetric(  # type: ignore[call-arg]
                provider="openai",
                tokens_used=1500,
                cost_usd=0.045,
                timestamp="2026-01-01T10:00:00Z",
            )


@pytest.mark.unit
class TestCostsResponse:
    def test_valid(self):
        resp = CostsResponse(
            total_cost=1.23,
            total_tokens=50000,
            by_model=[{"model": "gpt-4", "cost": 0.90}],
            by_provider={"openai": 0.90, "anthropic": 0.33},
            period="2026-01",
            updated_at="2026-01-31T23:59:59Z",
        )
        assert resp.total_cost == 1.23
        assert resp.total_tokens == 50000


@pytest.mark.unit
class TestHealthMetrics:
    def test_valid(self):
        metrics = HealthMetrics(
            status="healthy",
            uptime_seconds=86400.0,
            active_tasks=3,
            completed_tasks=100,
            failed_tasks=2,
            api_version="3.0.43",
        )
        assert metrics.status == "healthy"
        assert metrics.active_tasks == 3


@pytest.mark.unit
class TestPerformanceMetrics:
    def test_valid(self):
        metrics = PerformanceMetrics(
            avg_response_time_ms=245.5,
            requests_per_minute=12.3,
            error_rate=0.02,
            cache_hit_rate=0.75,
        )
        assert metrics.avg_response_time_ms == 245.5
        assert metrics.cache_hit_rate == 0.75
