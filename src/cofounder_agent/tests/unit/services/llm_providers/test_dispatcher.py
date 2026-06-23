"""Unit tests for the VRAM-budget clamp in services/llm_providers/dispatcher.py.

The clamp (`_clamp_num_ctx_to_budget`) is the pre-load fit guard: before the
GPU lock is acquired it estimates the model footprint at the requested context
and, if that would breach (total - desktop_reserve), reduces num_ctx to the
largest value that still fits — keeping the NVIDIA driver from spilling VRAM
into system RAM (the WDDM freeze). It fails OPEN: if the model arch can't be
read, the requested context passes through unchanged.

No module-level asyncio mark — pyproject `asyncio_mode = "auto"` auto-marks the
coroutine tests; an explicit mark would wrongly tag any sync helper here.
"""


def _arch_factory(weight_gb=18):
    from services.vram_budget import ModelArch

    # gemma-4-31B-class illustrative shape; tests assert the clamp relationship,
    # not absolute GB, so the exact numbers only need to be internally usable.
    async def _arch(*_a, **_k):
        return ModelArch(
            n_layers=48, n_kv_heads=8, head_dim=128,
            weight_bytes=weight_gb * 1024**3,
        )

    return _arch


async def test_clamp_num_ctx_reduces_when_over_budget(monkeypatch):
    import services.llm_providers.dispatcher as d

    # f16 KV (2.0 bytes/elem — the dangerous *unquantized* case): a 65k context
    # on an 18GB-weights writer projects ~31.5GB, over the 32-3=29GB budget, so
    # it must clamp down to something that fits.
    monkeypatch.setattr(d, "_budget_inputs", lambda pc: (32.0, 3.0, 2.0))
    monkeypatch.setattr(d, "_read_arch_for_budget", _arch_factory())

    clamped = await d._clamp_num_ctx_to_budget(
        pool=None, model="ollama/gemma-4-31B-it-qat:latest",
        num_ctx=65536, provider_config={},
    )
    assert 0 < clamped < 65536


async def test_clamp_noop_when_within_budget(monkeypatch):
    import services.llm_providers.dispatcher as d

    # q8 KV (1.0) at 8k ctx on the same writer is ~20GB — comfortably inside the
    # 29GB budget, so the requested context passes through untouched.
    monkeypatch.setattr(d, "_budget_inputs", lambda pc: (32.0, 3.0, 1.0))
    monkeypatch.setattr(d, "_read_arch_for_budget", _arch_factory())

    out = await d._clamp_num_ctx_to_budget(
        pool=None, model="m", num_ctx=8192, provider_config={},
    )
    assert out == 8192


async def test_clamp_noop_when_arch_unavailable(monkeypatch):
    import services.llm_providers.dispatcher as d

    async def _none(*_a, **_k):
        return None

    monkeypatch.setattr(d, "_read_arch_for_budget", _none)
    # fail-open: unknown arch -> return the requested num_ctx unchanged
    out = await d._clamp_num_ctx_to_budget(
        pool=None, model="m", num_ctx=8192, provider_config={},
    )
    assert out == 8192
