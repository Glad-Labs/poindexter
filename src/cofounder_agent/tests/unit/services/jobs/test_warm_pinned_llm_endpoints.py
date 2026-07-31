"""WarmPinnedLlmEndpointsJob — the missing half of stack#2051 (see #2938).

The pin shipped and works; the *warm* did not exist. ``OLLAMA_KEEP_ALIVE=-1``
means "never evict once loaded" and loads nothing, so the pinned GPU sat empty
after every restart until a rail cold-loaded an 18 GB model mid-pipeline and
timed out (observed 2026-07-31: instance up 16h48m, ``/api/ps`` empty, while
``vision_scorer_unavailable`` findings kept firing).

Pinned here:

1. A cold pinned endpoint gets warmed, with ``keep_alive=-1`` and an explicit
   ``num_ctx`` — Ollama reloads when the context changes, so warming at the
   wrong one is worse than not warming.
2. An already-resident model is left alone (no eviction, no wasted load).
3. The DEFAULT endpoint is never warmed — under ``OLLAMA_MAX_LOADED_MODELS=1``
   that would evict whatever the pipeline is mid-way through.
4. An unreachable endpoint degrades to a reported count, not a failed run.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.warm_pinned_llm_endpoints import WarmPinnedLlmEndpointsJob

_PINNED = "http://host.docker.internal:11435"
_DEFAULT = "http://host.docker.internal:11434"
_MODEL = "ollama/qwen3-vl:30b"
_TAG = "qwen3-vl:30b"


def _site_config(enabled: bool = True, num_ctx: str = "8192") -> MagicMock:
    sc = MagicMock()
    sc.get_bool.return_value = enabled
    sc.get.side_effect = lambda key, default="": (
        num_ctx if key == "ollama_num_ctx" else default
    )
    return sc


def _client(ps_body: Any, *, ps_raises: bool = False) -> MagicMock:
    """An httpx.AsyncClient stub usable as an async context manager."""
    client = MagicMock()
    ps_resp = MagicMock()
    ps_resp.json.return_value = ps_body
    ps_resp.raise_for_status.return_value = None
    client.get = AsyncMock(
        side_effect=RuntimeError("connection refused") if ps_raises else None,
        return_value=None if ps_raises else ps_resp,
    )
    gen_resp = MagicMock()
    gen_resp.raise_for_status.return_value = None
    client.post = AsyncMock(return_value=gen_resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return client, ctx


def _patches(client_ctx, overrides: dict[str, str]):
    return [
        patch("httpx.AsyncClient", return_value=client_ctx),
        patch(
            "services.llm_providers.dispatcher.get_provider_config",
            new=AsyncMock(return_value={
                "api_base": _DEFAULT,
                "model_api_base_overrides": overrides,
            }),
        ),
        patch(
            "services.llm_providers.litellm_provider._coerce_override_map",
            lambda v: dict(v or {}),
        ),
        patch("services.ollama_client.resolve_num_ctx", lambda *_a, **_k: 8192),
        patch("utils.findings.emit_finding", lambda **_k: None),
    ]


async def _run(client_ctx, overrides, site_config=None):
    import contextlib

    with contextlib.ExitStack() as stack:
        for p in _patches(client_ctx, overrides):
            stack.enter_context(p)
        return await WarmPinnedLlmEndpointsJob().run(
            pool=MagicMock(), config={"_site_config": site_config or _site_config()},
        )


@pytest.mark.asyncio
async def test_cold_pinned_endpoint_is_warmed_with_keep_alive_and_num_ctx():
    """THE pin: a cold endpoint gets loaded, never-evict, at an explicit ctx."""
    client, ctx = _client({"models": []})
    result = await _run(ctx, {_MODEL: _PINNED})

    assert result.ok is True
    assert result.changes_made == 1
    client.post.assert_awaited_once()
    call = client.post.await_args
    assert call.args[0] == f"{_PINNED}/api/generate"
    body = call.kwargs["json"]
    assert body["model"] == _TAG
    assert body["keep_alive"] == -1, "a pin that evicts is not a pin"
    assert body["options"]["num_ctx"] == 8192, (
        "warm must use the context real calls request — Ollama reloads when "
        "num_ctx changes, so warming at a different one wastes the load"
    )


@pytest.mark.asyncio
async def test_resident_model_is_left_alone():
    """No churn: an already-warm endpoint must not be reloaded."""
    client, ctx = _client({"models": [{"name": _TAG, "size_vram": 1}]})
    result = await _run(ctx, {_MODEL: _PINNED})

    assert result.ok is True
    assert result.changes_made == 0
    assert result.metrics["already_resident"] == 1
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_override_pointing_at_the_default_endpoint_is_skipped():
    """The default endpoint serves many models under MAX_LOADED_MODELS=1 —
    warming there evicts live pipeline work instead of protecting anything."""
    client, ctx = _client({"models": []})
    result = await _run(ctx, {_MODEL: _DEFAULT})

    assert result.changes_made == 0
    client.post.assert_not_awaited()
    client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_unreachable_endpoint_degrades_without_failing_the_run():
    """A down sidecar is advisory — it must not mark the scheduled run failed."""
    client, ctx = _client(None, ps_raises=True)
    result = await _run(ctx, {_MODEL: _PINNED})

    assert result.ok is True, "advisory housekeeping must not fail the run"
    assert result.changes_made == 0
    assert result.metrics["unreachable"] == 1
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_override_map_is_a_clean_noop():
    """The OSS single-endpoint install has nothing pinned and nothing to warm."""
    client, ctx = _client({"models": []})
    result = await _run(ctx, {})

    assert result.ok is True
    assert result.changes_made == 0
    assert "no pinned endpoints" in result.detail
    client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_flag_short_circuits():
    client, ctx = _client({"models": []})
    result = await _run(ctx, {_MODEL: _PINNED}, site_config=_site_config(enabled=False))

    assert result.ok is True
    assert result.detail == "disabled"
    client.get.assert_not_awaited()


def test_job_is_registered_and_instantiable():
    """A job that exists but is never scheduled is this bug all over again.

    Goes through ``get_core_samples`` rather than the registry's internal list:
    that function actually imports and instantiates each entry, so a typo'd
    module path or class name fails here instead of being swallowed by the
    registry's per-entry try/except and silently never scheduling.
    """
    from plugins.registry import get_core_samples

    jobs = get_core_samples().get("jobs", [])
    names = {getattr(j, "name", None) for j in jobs}
    assert "warm_pinned_llm_endpoints" in names, (
        "WarmPinnedLlmEndpointsJob is not in the registry's job samples — it "
        f"would never run. Registered jobs: {sorted(n for n in names if n)}"
    )
