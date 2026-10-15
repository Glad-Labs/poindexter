"""Unit tests for services/vram_budget.py — the deterministic single-GPU
VRAM footprint calculator.

The tests assert *relationships* (linearity in context, halving with q8 KV,
fit/headroom arithmetic) rather than absolute GB, so they don't depend on the
illustrative ModelArch shape below — the real arch is read live off
Ollama /api/show by read_model_arch at runtime.
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from poindexter.services import vram_budget
from poindexter.services.vram_budget import (
    ModelArch,
    estimate_kv_cache_gb,
    estimate_model_vram_gb,
    fits,
    kv_bytes_per_elem,
    max_safe_num_ctx,
    ollama_tag,
    read_model_arch,
)

# gemma-4-31B-class shape (illustrative): 48 layers, 8 KV heads, head_dim 128.
_ARCH = ModelArch(n_layers=48, n_kv_heads=8, head_dim=128, weight_bytes=18 * 1024**3)


def test_kv_bytes_per_elem_maps_dtype():
    assert kv_bytes_per_elem("f16") == 2.0
    assert kv_bytes_per_elem("q8_0") == 1.0
    assert kv_bytes_per_elem("q4_0") == 0.5
    assert kv_bytes_per_elem("") == 2.0  # unset sentinel -> safe f16


def test_kv_cache_grows_linearly_with_context():
    a = estimate_kv_cache_gb(_ARCH, num_ctx=8192, kv_bytes_per_elem=1.0)
    b = estimate_kv_cache_gb(_ARCH, num_ctx=16384, kv_bytes_per_elem=1.0)
    assert b == pytest.approx(2 * a, rel=1e-6)


def test_q8_halves_kv_vs_f16():
    f16 = estimate_kv_cache_gb(_ARCH, num_ctx=8192, kv_bytes_per_elem=2.0)
    q8 = estimate_kv_cache_gb(_ARCH, num_ctx=8192, kv_bytes_per_elem=1.0)
    assert q8 == pytest.approx(f16 / 2, rel=1e-6)


def test_fits_reports_headroom():
    ok, headroom = fits(footprint_gb=25.0, total_gb=32.0, desktop_reserve_gb=3.0)
    assert ok is True
    assert headroom == pytest.approx(4.0)
    bad, deficit = fits(footprint_gb=31.0, total_gb=32.0, desktop_reserve_gb=3.0)
    assert bad is False
    assert deficit == pytest.approx(-2.0)


def test_max_safe_num_ctx_fits_within_budget():
    n = max_safe_num_ctx(_ARCH, total_gb=32.0, desktop_reserve_gb=3.0, kv_bytes_per_elem=1.0)
    foot = estimate_model_vram_gb(_ARCH, estimate_kv_cache_gb(_ARCH, n, 1.0))
    ok, _ = fits(foot, 32.0, 3.0)
    assert ok is True
    assert n > 0


# ---------------------------------------------------------------------------
# read_model_arch only probes Ollama for models Ollama serves
# ---------------------------------------------------------------------------
# A cloud model (story_writer_model = anthropic/claude-sonnet-5-5) used to send
# POST /api/show to the local Ollama on every call, which 404'd and logged a
# WARNING each time. It holds no local VRAM, so there is nothing to ask.

_SHOW_INFO = {
    "gemma3.block_count": 48,
    "gemma3.attention.head_count_kv": 8,
    "gemma3.attention.head_count": 32,
    "gemma3.embedding_length": 4096,
}


def _recording_client(
    seen: list[str], client_cls: type[httpx.AsyncClient] = httpx.AsyncClient,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model_info": _SHOW_INFO})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": "gemma3:27b", "size": 17 * 1024 ** 3}]},
            )
        return httpx.Response(404)

    return client_cls(transport=httpx.MockTransport(handler))


@pytest.fixture
def _empty_arch_cache(monkeypatch):
    monkeypatch.setattr(vram_budget, "_ARCH_CACHE", {})


@pytest.mark.parametrize(
    ("model", "tag"),
    [
        ("ollama/gemma3:27b", "gemma3:27b"),
        ("ollama_chat/qwen3-vl:30b", "qwen3-vl:30b"),
        ("OLLAMA/phi4:14b", "phi4:14b"),
        ("phi4:14b", "phi4:14b"),
        ("anthropic/claude-sonnet-5-5", None),
        ("gemini/gemini-2.5-pro", None),
        ("vllm/some-model", None),
        ("http://example.com/v1", None),
        ("", None),
    ],
)
def test_ollama_tag_names_only_ollama_served_models(model, tag):
    assert ollama_tag(model) == tag


@pytest.mark.real_model_arch
@pytest.mark.asyncio
@pytest.mark.usefixtures("_empty_arch_cache")
async def test_cloud_model_makes_no_api_show_request():
    seen: list[str] = []
    async with _recording_client(seen) as client:
        arch = await read_model_arch(
            "anthropic/claude-sonnet-5-5", "http://ollama.test:11434", client,
        )
    assert arch is None
    assert seen == []


@pytest.mark.real_model_arch
@pytest.mark.asyncio
@pytest.mark.usefixtures("_empty_arch_cache")
async def test_ollama_model_is_still_probed():
    # Positive control: the same client does reach /api/show for a local model,
    # so the test above passes because the request was skipped, not because
    # the stub can't record one.
    seen: list[str] = []
    async with _recording_client(seen) as client:
        arch = await read_model_arch("ollama/gemma3:27b", "http://ollama.test:11434", client)
    assert seen == ["/api/show", "/api/tags"]
    assert arch is not None
    assert arch.n_layers == 48
    assert arch.weight_bytes == 17 * 1024 ** 3


@pytest.mark.real_model_arch
@pytest.mark.asyncio
@pytest.mark.usefixtures("_empty_arch_cache")
async def test_dispatch_clamp_leaves_a_cloud_model_unclamped_without_a_request(monkeypatch):
    # The path the warning came from: dispatch_complete -> _clamp_num_ctx_to_budget
    # -> _read_arch_for_budget, which builds its own httpx client.
    from poindexter.services.llm_providers import dispatcher

    seen: list[str] = []
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: _recording_client(seen, real_client),
    )
    monkeypatch.setattr(dispatcher, "_budget_inputs", AsyncMock(return_value=(1.0, 0.0, 1.0)))
    out = await dispatcher._clamp_num_ctx_to_budget(
        None, "anthropic/claude-sonnet-5-5", 16384, {},
    )
    assert out == 16384
    assert seen == []
