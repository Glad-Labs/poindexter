"""Unit tests for services/gpu_registry.py — VRAM pool auto-detection."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gpu_registry import GPURegistry
from services.site_config import SiteConfig


def _sc() -> SiteConfig:
    return SiteConfig(initial_config={"gpu_metrics_prometheus_url": "http://prometheus:9090"})


def _mock_client(*, value: str | None = None, status: int = 200, raise_exc: Exception | None = None):
    """Fake httpx.AsyncClient whose .get returns a Prometheus instant-vector."""
    resp = MagicMock()
    resp.status_code = status
    if value is None:
        resp.json = MagicMock(return_value={"data": {"result": []}})
    else:
        resp.json = MagicMock(return_value={"data": {"result": [{"value": [1782600000.0, value]}]}})
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=raise_exc) if raise_exc else AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_sums_and_converts_mib_to_gb():
    # 32607 + 24576 MiB summed by Prometheus = 57183 MiB -> /1024 = 55.84 GB
    client = _mock_client(value="57183")
    with patch("httpx.AsyncClient", return_value=client):
        total = await GPURegistry(site_config=_sc()).total_vram_gb()
    assert total == pytest.approx(57183 / 1024.0, abs=0.01)


@pytest.mark.asyncio
async def test_memoizes_first_success_no_requery():
    client = _mock_client(value="57183")
    reg = GPURegistry(site_config=_sc())
    with patch("httpx.AsyncClient", return_value=client):
        first = await reg.total_vram_gb()
        second = await reg.total_vram_gb()
    assert first == second
    assert client.get.await_count == 1  # cached; second call did not re-query


@pytest.mark.asyncio
async def test_empty_result_returns_none():
    client = _mock_client(value=None)
    with patch("httpx.AsyncClient", return_value=client):
        assert await GPURegistry(site_config=_sc()).total_vram_gb() is None


@pytest.mark.asyncio
async def test_http_error_returns_none():
    client = _mock_client(value="57183", status=503)
    with patch("httpx.AsyncClient", return_value=client):
        assert await GPURegistry(site_config=_sc()).total_vram_gb() is None


@pytest.mark.asyncio
async def test_exception_returns_none():
    client = _mock_client(raise_exc=RuntimeError("boom"))
    with patch("httpx.AsyncClient", return_value=client):
        assert await GPURegistry(site_config=_sc()).total_vram_gb() is None


@pytest.mark.asyncio
async def test_retries_after_failure_then_caches():
    reg = GPURegistry(site_config=_sc())
    fail = _mock_client(value=None)
    with patch("httpx.AsyncClient", return_value=fail):
        assert await reg.total_vram_gb() is None  # not cached
    ok = _mock_client(value="57183")
    with patch("httpx.AsyncClient", return_value=ok):
        assert await reg.total_vram_gb() == pytest.approx(57183 / 1024.0, abs=0.01)


# ---------------------------------------------------------------------------
# free_gb — per-card free VRAM with a ≤15s memo (poindexter#914 P1, Task B2)
# ---------------------------------------------------------------------------


def _mock_client_rows(rows: list[dict], *, status: int = 200):
    """Fake httpx.AsyncClient whose .get returns an arbitrary result list."""
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value={"data": {"result": rows}})
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_free_gb_reads_scalar_in_gb():
    # Prometheus evaluates (total-used)/1024 server-side; sample is already GB.
    client = _mock_client(value="13.5")
    with patch("httpx.AsyncClient", return_value=client):
        free = await GPURegistry(site_config=_sc()).free_gb(0)
    assert free == pytest.approx(13.5)
    # The query pins the card by label — never an unindexed result[0] read.
    query = client.get.await_args.kwargs.get("params", {}).get("query", "")
    assert 'gpu="0"' in query


@pytest.mark.asyncio
async def test_free_gb_memoizes_within_ttl_and_none_on_failure():
    reg = GPURegistry(site_config=_sc())
    ok = _mock_client(value="10.0")
    with patch("httpx.AsyncClient", return_value=ok):
        assert await reg.free_gb(0) == pytest.approx(10.0)
        assert await reg.free_gb(0) == pytest.approx(10.0)
    assert ok.get.await_count == 1  # second read served from the memo

    # A different card is its own memo slot → does query.
    fail = _mock_client(raise_exc=RuntimeError("prom down"))
    with patch("httpx.AsyncClient", return_value=fail):
        assert await reg.free_gb(1) is None  # failure → None, memo unpolluted


@pytest.mark.asyncio
async def test_free_gb_failure_is_none_not_zero():
    """None (skip fit gate) — never 0.0, which would hard-reject everything."""
    client = _mock_client(value=None)
    with patch("httpx.AsyncClient", return_value=client):
        assert await GPURegistry(site_config=_sc()).free_gb(0) is None


# ---------------------------------------------------------------------------
# evictable_ollama_gb — per-card per-process credit (never /api/ps totals)
# ---------------------------------------------------------------------------


def _proc_row(gpu: str, process: str, mib: str, pid: str = "100") -> dict:
    return {
        "metric": {"gpu": gpu, "pid": pid, "process": process},
        "value": [1782600000.0, mib],
    }


@pytest.mark.asyncio
async def test_evictable_sums_matching_rows_on_card():
    client = _mock_client_rows(
        [
            _proc_row("0", "ollama", "18432", pid="42"),
            _proc_row("0", "python3", "2048", pid="43"),  # not ollama → excluded
        ]
    )
    with patch("httpx.AsyncClient", return_value=client):
        got = await GPURegistry(site_config=_sc()).evictable_ollama_gb(0)
    assert got == pytest.approx(18.0)


@pytest.mark.asyncio
async def test_evictable_spilled_model_counts_only_this_cards_share():
    """A model split across both cards must contribute only its gpu-0 share —
    the exact overstatement the /api/ps cross-card total would make."""
    client = _mock_client_rows(
        [
            _proc_row("0", "ollama", "10240", pid="42"),
            _proc_row("1", "ollama", "8192", pid="42"),  # gpu1 share ignored
        ]
    )
    with patch("httpx.AsyncClient", return_value=client):
        got = await GPURegistry(site_config=_sc()).evictable_ollama_gb(0)
    assert got == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_evictable_absent_metric_is_unknown():
    """Exporter without the per-process metric (not yet rebuilt) → None.

    REVERSED 2026-07-31 (poindexter#914): this asserted 0.0. Returning 0.0 made
    "I have no telemetry" indistinguishable from "nothing is loaded", and
    admission read the latter — rejecting, which degrades a QA rail that then
    passes OPEN. None lets the caller fail open deliberately instead.
    """
    client = _mock_client_rows([])
    with patch("httpx.AsyncClient", return_value=client):
        assert await GPURegistry(site_config=_sc()).evictable_ollama_gb(0) is None


@pytest.mark.asyncio
async def test_evictable_failure_is_unknown():
    """Prometheus down is ignorance, not evidence of an empty card."""
    client = _mock_client(raise_exc=RuntimeError("prom down"))
    with patch("httpx.AsyncClient", return_value=client):
        assert await GPURegistry(site_config=_sc()).evictable_ollama_gb(0) is None


@pytest.mark.asyncio
async def test_evictable_pattern_is_configurable():
    sc = SiteConfig(
        initial_config={
            "gpu_metrics_prometheus_url": "http://prometheus:9090",
            "gpu_evictable_process_pattern": "llama-server",
        }
    )
    client = _mock_client_rows(
        [
            _proc_row("0", "/usr/bin/llama-server", "4096"),
            _proc_row("0", "ollama", "8192"),  # no longer matches the pattern
        ]
    )
    with patch("httpx.AsyncClient", return_value=client):
        assert await GPURegistry(site_config=sc).evictable_ollama_gb(0) == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Real-world process names (2026-07-29 regression)
# ---------------------------------------------------------------------------
# The pattern shipped as the single substring "ollama" and the tests above used
# a process literally named "ollama" — so they passed while production matched
# NOTHING. Stock Ollama on Linux runs /usr/local/lib/ollama/llama-server, which
# the exporter labels "llama-server", and "ollama" is not a substring of that.
# The credit was 0.0 on every card for the entire P1 soak and nothing errored,
# because 0.0 is also the legitimate "no telemetry" value and the fit gate fails
# open. These pin the observed names, not the assumed one.


@pytest.mark.asyncio
async def test_evictable_matches_real_stock_ollama_runner_name():
    """The exact production label that the old default missed."""
    client = _mock_client_rows([_proc_row("0", "llama-server", "20556", pid="1002799")])
    with patch("httpx.AsyncClient", return_value=client):
        got = await GPURegistry(site_config=_sc()).evictable_ollama_gb(0)
    assert got == pytest.approx(20556 / 1024.0, rel=1e-3)
    assert got > 0.0, "a real Ollama runner must produce non-zero eviction credit"


@pytest.mark.asyncio
async def test_evictable_still_matches_legacy_ollama_name():
    """Back-compat: an install that really does label the process 'ollama'."""
    client = _mock_client_rows([_proc_row("0", "ollama", "18432", pid="42")])
    with patch("httpx.AsyncClient", return_value=client):
        got = await GPURegistry(site_config=_sc()).evictable_ollama_gb(0)
    assert got == pytest.approx(18.0)


@pytest.mark.asyncio
async def test_evictable_single_value_config_still_works():
    """Operators with a single-substring value keep working unchanged."""
    sc = SiteConfig(initial_config={
        "gpu_metrics_prometheus_url": "http://prometheus:9090",
        "gpu_evictable_process_pattern": "llama-server",
    })
    client = _mock_client_rows([
        _proc_row("0", "llama-server", "1024", pid="1"),
        _proc_row("0", "ollama", "1024", pid="2"),  # not in the single pattern
    ])
    with patch("httpx.AsyncClient", return_value=client):
        got = await GPURegistry(site_config=sc).evictable_ollama_gb(0)
    assert got == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_evictable_csv_matches_any_entry_without_double_counting():
    sc = SiteConfig(initial_config={
        "gpu_metrics_prometheus_url": "http://prometheus:9090",
        "gpu_evictable_process_pattern": " llama-server , ollama ",  # whitespace tolerated
    })
    client = _mock_client_rows([
        _proc_row("0", "llama-server", "1024", pid="1"),
        _proc_row("0", "ollama", "1024", pid="2"),
        _proc_row("0", "chrome", "512", pid="3"),  # excluded
    ])
    with patch("httpx.AsyncClient", return_value=client):
        got = await GPURegistry(site_config=sc).evictable_ollama_gb(0)
    # A row matching BOTH entries must count once, not once per pattern.
    assert got == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_evictable_does_not_credit_unevictable_residents():
    """chatterbox / speaches hold GPU0 VRAM with no /unload endpoint — they are
    NOT reclaimable, so they must never inflate the eviction credit and let
    admission grant on VRAM it cannot actually free."""
    client = _mock_client_rows([
        _proc_row("0", "uvicorn", "5560", pid="5372"),   # chatterbox TTS
        _proc_row("0", "python", "16562", pid="2392551"),  # image-gen
    ])
    with patch("httpx.AsyncClient", return_value=client):
        got = await GPURegistry(site_config=_sc()).evictable_ollama_gb(0)
    assert got == 0.0
