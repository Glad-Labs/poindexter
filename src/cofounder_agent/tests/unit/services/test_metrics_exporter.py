"""Unit tests for services/metrics_exporter.py.

Verifies that refresh_metrics() populates every gauge/histogram even
when individual queries fail, and that the new Gitea #238 metrics
(postgres latency, ollama model count, embeddings-missing-posts,
approval queue) respond to the expected DB shapes.

All DB + HTTP I/O is mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_gauges():
    """Zero out the single-value Gauges between tests. Histograms
    accumulate across the session since prometheus_client doesn't
    expose a clean reset; tests assert ``> 0`` rather than a specific
    value, so accumulation is fine."""
    from services import metrics_exporter as mx

    for g in (
        mx.WORKER_UP,
        mx.POSTGRES_CONNECTED,
        mx.OLLAMA_REACHABLE,
        mx.OLLAMA_MODEL_COUNT,
        mx.EMBEDDINGS_MISSING_POSTS,
        mx.APPROVAL_QUEUE_LENGTH,
    ):
        g.set(0)
    yield


def _make_pool(fetchval_responses, fetch_responses):
    """Build an AsyncMock pool whose acquire() context returns a conn
    that serves up fetchval/fetch responses from the provided queues."""
    pool = MagicMock()

    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=fetchval_responses)
    conn.fetch = AsyncMock(side_effect=fetch_responses)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


@pytest.mark.unit
@pytest.mark.asyncio
class TestRefreshMetrics:
    async def test_worker_up_always_set(self):
        from services import metrics_exporter as mx

        pool, _ = _make_pool([1, 0, 0], [[], []])
        with patch(
            "services.metrics_exporter.httpx.AsyncClient"
        ) as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("no ollama")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        # _value is the prometheus_client Gauge internal counter.
        assert mx.WORKER_UP._value.get() == 1  # type: ignore[attr-defined]

    async def test_postgres_latency_recorded_on_success(self):
        from services import metrics_exporter as mx

        # Capture the current observation count so we can assert that
        # refresh_metrics observed exactly one more sample. Reading via
        # collect() avoids poking at private attributes that vary across
        # prometheus_client versions.
        def _latency_count() -> float:
            families = list(mx.POSTGRES_QUERY_LATENCY.collect())
            for sample in families[0].samples:
                if sample.name.endswith("_count"):
                    return sample.value
            return 0.0

        before = _latency_count()

        pool, _ = _make_pool([1, 0, 0], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        after = _latency_count()
        assert after == before + 1
        assert mx.POSTGRES_CONNECTED._value.get() == 1  # type: ignore[attr-defined]

    async def test_postgres_connected_zero_on_query_failure(self):
        from services import metrics_exporter as mx

        # SELECT 1 raises — downstream queries still get to run via
        # their own try/except.
        pool = MagicMock()
        conn = MagicMock()
        conn.fetchval = AsyncMock(side_effect=RuntimeError("conn dead"))
        conn.fetch = AsyncMock(return_value=[])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=ctx)

        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.POSTGRES_CONNECTED._value.get() == 0  # type: ignore[attr-defined]

    async def test_ollama_model_count_set_from_api_tags(self):
        from services import metrics_exporter as mx

        pool, _ = _make_pool([1, 0, 0], [[], []])

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json = MagicMock(
            return_value={"models": [{"name": "gemma3:27b"}, {"name": "nomic-embed-text"}]}
        )
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        fake_ctx = MagicMock()
        fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
        fake_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("services.metrics_exporter.httpx.AsyncClient", return_value=fake_ctx):
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.OLLAMA_REACHABLE._value.get() == 1  # type: ignore[attr-defined]
        assert mx.OLLAMA_MODEL_COUNT._value.get() == 2  # type: ignore[attr-defined]

    async def test_ollama_model_count_zero_when_tags_empty(self):
        """"Ollama up but no models" — the scenario Gitea #238 flagged."""
        from services import metrics_exporter as mx

        pool, _ = _make_pool([1, 0, 0], [[], []])

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json = MagicMock(return_value={"models": []})
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        fake_ctx = MagicMock()
        fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
        fake_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("services.metrics_exporter.httpx.AsyncClient", return_value=fake_ctx):
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.OLLAMA_REACHABLE._value.get() == 1  # type: ignore[attr-defined]
        assert mx.OLLAMA_MODEL_COUNT._value.get() == 0  # type: ignore[attr-defined]

    async def test_embeddings_missing_posts_reflects_gap(self):
        from services import metrics_exporter as mx

        # fetchval queue: SELECT 1 → 1, embeddings-gap → 5, queue → 2.
        pool, _ = _make_pool([1, 5, 2], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.EMBEDDINGS_MISSING_POSTS._value.get() == 5  # type: ignore[attr-defined]

    async def test_approval_queue_length_reflects_count(self):
        from services import metrics_exporter as mx

        pool, _ = _make_pool([1, 0, 7], [[], []])
        with patch("services.metrics_exporter.httpx.AsyncClient") as mock_http_cls:
            mock_http_cls.return_value.__aenter__.side_effect = Exception("skip")
            await mx.refresh_metrics(pool, "http://localhost:11434")

        assert mx.APPROVAL_QUEUE_LENGTH._value.get() == 7  # type: ignore[attr-defined]


@pytest.mark.unit
def test_render_exposition_returns_text_format():
    """Smoke check: /metrics endpoint returns bytes + the standard
    Prometheus text content-type so Alertmanager can scrape it."""
    from services.metrics_exporter import render_exposition

    body, content_type = render_exposition()
    assert isinstance(body, bytes)
    assert "text/plain" in content_type
