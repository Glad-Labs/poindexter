"""
Integration test fixtures.

Provides http_client, api_tester, test_data_factory, performance_timer,
concurrency_tester, and mock_llm_response fixtures used by test_api_integration.py.

Tests that require a running server are automatically skipped when the server
is not reachable (e.g. in CI without a live backend).
"""

import asyncio
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional

import pytest

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

BACKEND_URL = "http://localhost:8000"


# ---------------------------------------------------------------------------
# Skip helper – skip gracefully when backend is not running
# ---------------------------------------------------------------------------

def _skip_if_no_httpx():
    if not _HTTPX_AVAILABLE:
        pytest.skip("httpx not installed")


async def _backend_reachable() -> bool:
    if not _HTTPX_AVAILABLE:
        return False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{BACKEND_URL}/health")
            return r.status_code < 500
    except Exception:
        return False


# ---------------------------------------------------------------------------
# http_client
# ---------------------------------------------------------------------------

@pytest.fixture
async def http_client():
    """Async httpx client pointed at the local backend."""
    _skip_if_no_httpx()
    if not await _backend_reachable():
        pytest.skip("Backend not reachable – skipping integration test")

    async with httpx.AsyncClient(
        base_url=BACKEND_URL,
        timeout=30.0,
        headers={"Authorization": "Bearer dev-token"},
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# api_tester
# ---------------------------------------------------------------------------

class ApiTester:
    """Thin wrapper around httpx.AsyncClient with assertion helpers."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._response: Optional[Any] = None

    async def get(self, path: str, **kwargs: Any) -> Any:
        self._response = await self._client.get(path, **kwargs)
        return self._response

    async def post(self, path: str, **kwargs: Any) -> Any:
        self._response = await self._client.post(path, **kwargs)
        return self._response

    async def put(self, path: str, **kwargs: Any) -> Any:
        self._response = await self._client.put(path, **kwargs)
        return self._response

    async def patch(self, path: str, **kwargs: Any) -> Any:
        self._response = await self._client.patch(path, **kwargs)
        return self._response

    async def delete(self, path: str, **kwargs: Any) -> Any:
        self._response = await self._client.delete(path, **kwargs)
        return self._response

    def assert_status(self, expected: int) -> None:
        assert self._response is not None, "No request has been made yet"
        assert self._response.status_code == expected, (
            f"Expected {expected}, got {self._response.status_code}: "
            f"{self._response.text[:200]}"
        )

    def get_json(self) -> Any:
        assert self._response is not None, "No request has been made yet"
        return self._response.json()


@pytest.fixture
async def api_tester(http_client: Any) -> ApiTester:
    """ApiTester instance backed by the live http_client."""
    return ApiTester(http_client)


# ---------------------------------------------------------------------------
# test_data_factory
# ---------------------------------------------------------------------------

class TestDataFactory:
    """Creates test data via the live API."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._created_ids: List[str] = []

    async def create_task(
        self,
        title: str = "Test Task",
        description: str = "Created by test_data_factory",
        status: str = "pending",
        priority: Any = 1,
        **extra: Any,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "task_name": title,
            "topic": description or title,
            "status": status,
            **extra,
        }
        response = await self._client.post("/api/tasks", json=payload)
        if response.status_code in (200, 201):
            data = response.json()
            task_id = data.get("id") or data.get("task_id")
            if task_id:
                self._created_ids.append(str(task_id))
            return data
        # Return a minimal dict so assertion logic can continue
        return {"_error": response.status_code, "_text": response.text[:200]}

    async def create_multiple_tasks(
        self, count: int = 5, **kwargs: Any
    ) -> List[Dict[str, Any]]:
        return [
            await self.create_task(title=f"Bulk Task {i}", **kwargs)
            for i in range(count)
        ]


@pytest.fixture
async def test_data_factory(http_client: Any) -> TestDataFactory:
    """TestDataFactory backed by the live http_client."""
    return TestDataFactory(http_client)


# ---------------------------------------------------------------------------
# performance_timer
# ---------------------------------------------------------------------------

class _Timer:
    def __init__(self) -> None:
        self.duration: Optional[float] = None
        self._start: float = 0.0

    def __enter__(self) -> "_Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_: Any) -> None:
        self.duration = (time.perf_counter() - self._start) * 1000  # ms


@pytest.fixture
def performance_timer() -> Callable[[], _Timer]:
    """Returns a callable that yields a context-manager timer (duration in ms)."""

    def _factory() -> _Timer:
        return _Timer()

    return _factory


# ---------------------------------------------------------------------------
# concurrency_tester
# ---------------------------------------------------------------------------

class ConcurrencyTester:
    """Runs coroutines concurrently and collects results / stats."""

    async def run_concurrent(
        self, fn: Callable, args_list: List[Any]
    ) -> List[Any]:
        """
        Run *fn* for each set of args in *args_list* concurrently.
        *fn* is called as `fn(*args)` where each element of args_list is a tuple.
        """
        async def _call(args: Any) -> Any:
            if isinstance(args, (list, tuple)):
                return await fn(*args)
            return await fn(args)

        return await asyncio.gather(*[_call(a) for a in args_list], return_exceptions=True)

    async def stress_test(
        self,
        fn: Callable,
        iterations: int = 20,
        concurrent_workers: int = 5,
    ) -> Dict[str, Any]:
        """Run *fn* *iterations* times with *concurrent_workers* in parallel."""
        results: List[Any] = []

        async def _timed_call() -> Dict[str, Any]:
            start = time.perf_counter()
            try:
                await fn()
                return {"success": True, "duration": (time.perf_counter() - start) * 1000}
            except Exception as exc:
                return {"success": False, "duration": (time.perf_counter() - start) * 1000, "error": str(exc)}

        # Run in batches of concurrent_workers
        for batch_start in range(0, iterations, concurrent_workers):
            batch_size = min(concurrent_workers, iterations - batch_start)
            batch = await asyncio.gather(*[_timed_call() for _ in range(batch_size)])
            results.extend(batch)

        total = len(results)
        successes = sum(1 for r in results if r.get("success"))
        durations = [r["duration"] for r in results]
        return {
            "total": total,
            "success": successes,
            "failure": total - successes,
            "success_rate": (successes / total * 100) if total else 0.0,
            "avg_duration": sum(durations) / len(durations) if durations else 0.0,
        }


@pytest.fixture
def concurrency_tester() -> ConcurrencyTester:
    """ConcurrencyTester fixture."""
    return ConcurrencyTester()


# ---------------------------------------------------------------------------
# mock_llm_response
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_llm_response() -> Callable[..., Dict[str, Any]]:
    """Returns a factory for mock LLM response dicts."""

    def _factory(
        content: str = "Mock response",
        tokens: int = 100,
        model: str = "mock-model",
        **extra: Any,
    ) -> Dict[str, Any]:
        return {"content": content, "tokens": tokens, "model": model, **extra}

    return _factory
