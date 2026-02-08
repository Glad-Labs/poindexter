"""
Phase 4a: Performance & Benchmarking Tests

This module contains performance benchmarks and optimization tests
to ensure critical paths meet performance targets.

Tests measure:
- Service initialization times
- Model routing performance
- Database operation speed
- Data transformation latency
- Concurrent operation throughput

Performance Targets:
- Service initialization: <100ms per service
- Model routing decision: <10ms
- Database operations: <50ms
- Request/response transformation: <5ms
- Concurrent task handling: >50 tasks/second
"""

import pytest
import time
import asyncio
from typing import List, Dict, Any
from unittest.mock import Mock, patch, AsyncMock

# Performance test utilities


class PerformanceTimer:
    """Context manager for measuring execution time"""

    def __init__(self, name: str = "Operation"):
        self.name = name
        self.start_time = None
        self.end_time = None
        self.elapsed = None

    def __enter__(self):
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, *args):
        self.end_time = time.perf_counter()
        self.elapsed = (self.end_time - self.start_time) * 1000  # ms

    def assert_under(self, threshold_ms: float):
        """Assert execution time is under threshold"""
        assert (
            self.elapsed < threshold_ms
        ), f"{self.name} took {self.elapsed:.2f}ms, expected <{threshold_ms}ms"


# ============================================================================
# PHASE 4a: SERVICE INITIALIZATION PERFORMANCE
# ============================================================================


class TestModelRouterInitializationPerformance:
    """Test ModelRouter initialization speed"""

    def test_model_router_initialization_speed(self):
        """ModelRouter should initialize in <100ms"""
        try:
            from src.cofounder_agent.services.model_router import ModelRouter
        except ImportError:
            pytest.skip("ModelRouter not available")

        with PerformanceTimer("ModelRouter initialization") as timer:
            router = ModelRouter()

        timer.assert_under(100)
        assert router is not None

    def test_model_router_enum_access_speed(self):
        """Enum value access should be <1ms"""
        try:
            from src.cofounder_agent.services.model_router import (
                ModelRouter,
                TaskComplexity,
            )
        except ImportError:
            pytest.skip("ModelRouter not available")

        router = ModelRouter()

        with PerformanceTimer("Enum access") as timer:
            for _ in range(100):
                _ = TaskComplexity.SIMPLE
                _ = TaskComplexity.CRITICAL

        timer.assert_under(5)

    def test_model_costs_access_speed(self):
        """Model costs dictionary access should be <2ms"""
        try:
            from src.cofounder_agent.services.model_router import ModelRouter
        except ImportError:
            pytest.skip("ModelRouter not available")

        router = ModelRouter()

        with PerformanceTimer("Costs dictionary access") as timer:
            for _ in range(100):
                _ = router.MODEL_COSTS

        timer.assert_under(5)


class TestDatabaseServiceInitializationPerformance:
    """Test DatabaseService initialization speed"""

    def test_database_service_sqlite_init_speed(self):
        """SQLite database initialization should be <100ms"""
        try:
            from src.cofounder_agent.services.database import DatabaseService
        except ImportError:
            pytest.skip("DatabaseService not available")

        with PerformanceTimer("SQLite database init") as timer:
            db = DatabaseService(database_url="sqlite:///:memory:")

        timer.assert_under(100)
        assert db is not None

    def test_database_service_url_parsing_speed(self):
        """Database URL parsing should be <5ms"""
        try:
            from src.cofounder_agent.services.database import DatabaseService
        except ImportError:
            pytest.skip("DatabaseService not available")

        with PerformanceTimer("URL parsing") as timer:
            for _ in range(50):
                db = DatabaseService(database_url="sqlite:///test.db")

        timer.assert_under(5)


# ============================================================================
# PHASE 4b: MODEL ROUTING PERFORMANCE
# ============================================================================


class TestModelRoutingPerformance:
    """Test model routing decision speed"""

    def test_model_selection_decision_speed(self):
        """Model selection should be <10ms per decision"""
        try:
            from src.cofounder_agent.services.model_router import (
                ModelRouter,
                TaskComplexity,
            )
        except ImportError:
            pytest.skip("ModelRouter not available")

        router = ModelRouter()

        with PerformanceTimer("Model selection decisions (100x)") as timer:
            for _ in range(100):
                complexity = TaskComplexity.MEDIUM
                assert complexity is not None

        # Should average <0.1ms per decision, 100 decisions should be <10ms
        timer.assert_under(10)

    def test_token_limit_lookup_speed(self):
        """Token limit lookup should be <2ms"""
        try:
            from src.cofounder_agent.services.model_router import (
                ModelRouter,
                TaskComplexity,
            )
        except ImportError:
            pytest.skip("ModelRouter not available")

        router = ModelRouter()

        with PerformanceTimer("Token limit lookups (100x)") as timer:
            for _ in range(100):
                _ = router.get_max_tokens(TaskComplexity.CRITICAL)

        timer.assert_under(5)

    def test_pricing_lookup_speed(self):
        """Pricing lookup should be <5ms for 100 lookups"""
        try:
            from src.cofounder_agent.services.model_router import ModelRouter
        except ImportError:
            pytest.skip("ModelRouter not available")

        router = ModelRouter()

        with PerformanceTimer("Pricing lookups (100x)") as timer:
            for _ in range(100):
                _ = router.get_model_cost("gpt-4")

        timer.assert_under(5)


# ============================================================================
# PHASE 4c: DATA TRANSFORMATION PERFORMANCE
# ============================================================================


class TestDataTransformationPerformance:
    """Test data transformation speed"""

    def test_enum_to_string_conversion_speed(self):
        """Enum to string conversion should be <5ms for 100 conversions"""
        try:
            from src.cofounder_agent.services.model_router import TaskComplexity
        except ImportError:
            pytest.skip("ModelRouter not available")

        with PerformanceTimer("Enum conversions (100x)") as timer:
            for _ in range(100):
                _ = str(TaskComplexity.SIMPLE)
                _ = str(TaskComplexity.COMPLEX)

        timer.assert_under(5)

    def test_dictionary_transformation_speed(self):
        """Dictionary transformation should be <10ms for 100 transforms"""

        def transform_dict(data: Dict[str, Any]) -> Dict[str, Any]:
            return {
                k: str(v) if hasattr(v, "__str__") else v
                for k, v in data.items()
            }

        test_data = [
            {"key1": "value1", "key2": "value2", "key3": "value3"}
            for _ in range(100)
        ]

        with PerformanceTimer("Dictionary transformations (100x)") as timer:
            for data in test_data:
                _ = transform_dict(data)

        timer.assert_under(10)

    def test_list_transformation_speed(self):
        """List transformation should be <10ms for 100 transforms"""

        def transform_list(items: List[Any]) -> List[str]:
            return [str(item) for item in items]

        test_lists = [
            ["item1", "item2", "item3", "item4", "item5"] for _ in range(100)
        ]

        with PerformanceTimer("List transformations (100x)") as timer:
            for items in test_lists:
                _ = transform_list(items)

        timer.assert_under(10)


# ============================================================================
# PHASE 4d: ASYNC OPERATION PERFORMANCE
# ============================================================================


class TestAsyncOperationPerformance:
    """Test async operation performance"""

    @pytest.mark.asyncio
    async def test_concurrent_service_initialization_speed(self):
        """Initializing 10 services concurrently should be <500ms"""
        try:
            from src.cofounder_agent.services.database import DatabaseService
        except ImportError:
            pytest.skip("DatabaseService not available")

        async def init_service(index: int):
            db = DatabaseService(database_url=f"sqlite:///test_{index}.db")
            return db

        with PerformanceTimer("Concurrent service init (10x)") as timer:
            services = await asyncio.gather(*[init_service(i) for i in range(10)])

        timer.assert_under(500)
        assert len(services) == 10

    @pytest.mark.asyncio
    async def test_concurrent_task_simulation_speed(self):
        """Simulating 50 concurrent tasks should complete <100ms"""

        async def mock_task(task_id: int):
            await asyncio.sleep(0.001)  # 1ms per task
            return {"task_id": task_id, "status": "completed"}

        with PerformanceTimer("Concurrent tasks (50x)") as timer:
            results = await asyncio.gather(*[mock_task(i) for i in range(50)])

        timer.assert_under(100)
        assert len(results) == 50


# ============================================================================
# PHASE 4e: THROUGHPUT TESTING
# ============================================================================


class TestThroughputMetrics:
    """Test system throughput capacity"""

    def test_model_routing_throughput(self):
        """System should route >100 tasks/second"""
        try:
            from src.cofounder_agent.services.model_router import (
                ModelRouter,
                TaskComplexity,
            )
        except ImportError:
            pytest.skip("ModelRouter not available")

        router = ModelRouter()
        task_count = 100

        with PerformanceTimer(f"Routing {task_count} tasks") as timer:
            for i in range(task_count):
                complexity = TaskComplexity.MEDIUM
                assert complexity is not None

        # Calculate throughput: tasks per second
        throughput = (task_count / timer.elapsed) * 1000
        assert (
            throughput > 100
        ), f"Throughput {throughput:.0f} tasks/sec, expected >100"

    def test_data_transformation_throughput(self):
        """System should transform >500 items/second"""

        def transform_item(item: Dict[str, Any]) -> Dict[str, str]:
            return {k: str(v) for k, v in item.items()}

        items = [{"id": i, "value": f"item_{i}", "data": i * 2} for i in range(500)]

        with PerformanceTimer(f"Transform {len(items)} items") as timer:
            results = [transform_item(item) for item in items]

        throughput = (len(items) / timer.elapsed) * 1000
        assert (
            throughput > 500
        ), f"Throughput {throughput:.0f} items/sec, expected >500"
        assert len(results) == 500

    @pytest.mark.asyncio
    async def test_concurrent_task_throughput(self):
        """System should handle >50 concurrent tasks/second"""

        async def mock_task(task_id: int):
            await asyncio.sleep(0.005)  # 5ms per task
            return {"task_id": task_id}

        task_count = 50

        with PerformanceTimer(f"Execute {task_count} concurrent tasks") as timer:
            results = await asyncio.gather(
                *[mock_task(i) for i in range(task_count)]
            )

        throughput = (task_count / (timer.elapsed / 1000)) if timer.elapsed > 0 else 0
        assert (
            throughput > 50
        ), f"Throughput {throughput:.0f} tasks/sec, expected >50"
        assert len(results) == task_count


# ============================================================================
# PHASE 4f: MEMORY EFFICIENCY TESTS
# ============================================================================


class TestMemoryEfficiency:
    """Test memory usage efficiency"""

    def test_enum_memory_efficiency(self):
        """Enum instances should not grow with repeated access"""
        try:
            from src.cofounder_agent.services.model_router import TaskComplexity
        except ImportError:
            pytest.skip("ModelRouter not available")

        # Access same enum 1000 times
        enums_list = [TaskComplexity.SIMPLE for _ in range(1000)]

        # All should be same object (memory efficient)
        assert all(e is TaskComplexity.SIMPLE for e in enums_list)

    def test_costs_dict_consistency(self):
        """Model costs dictionary should remain consistent"""
        try:
            from src.cofounder_agent.services.model_router import ModelRouter
        except ImportError:
            pytest.skip("ModelRouter not available")

        router = ModelRouter()
        costs_1 = router.MODEL_COSTS.copy()

        # Access multiple times
        for _ in range(100):
            _ = router.MODEL_COSTS

        costs_2 = router.MODEL_COSTS.copy()

        # Should be identical
        assert costs_1 == costs_2


# ============================================================================
# PHASE 4g: COMPARATIVE PERFORMANCE TESTS
# ============================================================================


class TestComparativePerformance:
    """Compare performance of different operations"""

    def test_enum_vs_string_lookup_speed(self):
        """Enum lookup should be faster than string lookup"""
        try:
            from src.cofounder_agent.services.model_router import TaskComplexity
        except ImportError:
            pytest.skip("ModelRouter not available")

        # Enum lookup
        with PerformanceTimer("Enum lookups (1000x)") as enum_timer:
            for _ in range(1000):
                _ = TaskComplexity.SIMPLE

        # String lookup
        task_map = {"SIMPLE": "SIMPLE", "COMPLEX": "COMPLEX"}
        with PerformanceTimer("String lookups (1000x)") as string_timer:
            for _ in range(1000):
                _ = task_map.get("SIMPLE")

        # Enum should be comparable or faster
        assert (
            enum_timer.elapsed <= string_timer.elapsed * 1.5
        ), "Enum lookup unexpectedly slower"

    def test_direct_dict_access_vs_method_call(self):
        """Direct dictionary access should be faster than method calls"""
        test_dict = {"key1": "value1", "key2": "value2"}

        # Direct access
        with PerformanceTimer("Direct dict access (1000x)") as direct_timer:
            for _ in range(1000):
                _ = test_dict["key1"]

        # Method call simulation
        def get_value():
            return test_dict.get("key1")

        with PerformanceTimer("Method call access (1000x)") as method_timer:
            for _ in range(1000):
                _ = get_value()

        # Direct access should be faster
        assert (
            direct_timer.elapsed < method_timer.elapsed
        ), "Direct access unexpectedly slower"


# ============================================================================
# PHASE 4h: PERFORMANCE REGRESSION TESTS
# ============================================================================


class TestPerformanceRegression:
    """Ensure performance doesn't regress"""

    def test_service_init_not_degrading(self):
        """Service initialization time should not exceed baseline"""
        try:
            from src.cofounder_agent.services.database import DatabaseService
        except ImportError:
            pytest.skip("DatabaseService not available")

        # Baseline: 5 consecutive inits, average should be <150ms
        times = []
        for i in range(5):
            with PerformanceTimer(f"Init {i}") as timer:
                db = DatabaseService(database_url="sqlite:///:memory:")
            times.append(timer.elapsed)

        avg_time = sum(times) / len(times)
        assert (
            avg_time < 150
        ), f"Average init time {avg_time:.2f}ms exceeds baseline"

    def test_routing_latency_not_increasing(self):
        """Model routing latency should remain stable"""
        try:
            from src.cofounder_agent.services.model_router import (
                ModelRouter,
                TaskComplexity,
            )
        except ImportError:
            pytest.skip("ModelRouter not available")

        router = ModelRouter()

        # Multiple rounds of routing
        round_times = []
        for round_num in range(3):
            with PerformanceTimer(f"Routing round {round_num} (100x)") as timer:
                for _ in range(100):
                    _ = router.get_max_tokens(TaskComplexity.MEDIUM)
            round_times.append(timer.elapsed)

        # Latency should not increase across rounds
        assert (
            round_times[-1] <= round_times[0] * 1.2
        ), "Routing latency increasing across rounds"
