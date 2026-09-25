"""WarmPinnedLlmEndpointsJob — the missing half of stack#2051 (see #2938).

The pin shipped and works; the *warm* did not exist. ``OLLAMA_KEEP_ALIVE=-1``
means "never evict once loaded" and loads nothing, so the pinned GPU sat empty
after every restart until a rail cold-loaded an 18 GB model mid-pipeline and
timed out (observed 2026-07-31: instance up 16h48m, ``/api/ps`` empty, while
``vision_scorer_unavailable`` findings kept firing).

Pinned here:

1. A cold pinned endpoint gets warmed, with ``keep_alive=-1`` and an explicit
   ``num_ctx`` — Ollama reloads when the context changes, so warming at the
   wrong one is worse than not warming. The size is
   ``pinned_llm_endpoint_num_ctx``, the one every dispatch routed there runs at.
2. An already-resident model is left alone (no eviction, no wasted load).
3. The DEFAULT endpoint is never warmed — under ``OLLAMA_MAX_LOADED_MODELS=1``
   that would evict whatever the pipeline is mid-way through.
4. An unreachable endpoint degrades to a reported count, not a failed run.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services.jobs.warm_pinned_llm_endpoints import WarmPinnedLlmEndpointsJob

_PINNED = "http://host.docker.internal:11435"
_DEFAULT = "http://host.docker.internal:11434"
_MODEL = "ollama/qwen3-vl:30b"
_TAG = "qwen3-vl:30b"


def _site_config(
    enabled: bool = True, num_ctx: str = "8192", pinned: str = "16384",
) -> MagicMock:
    sc = MagicMock()
    sc.get_bool.return_value = enabled
    sc.get.side_effect = lambda key, default="": {
        "ollama_num_ctx": num_ctx,
        "pinned_llm_endpoint_num_ctx": pinned,
    }.get(key, default)
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
            "poindexter.services.llm_providers.dispatcher.get_provider_config",
            new=AsyncMock(return_value={
                "api_base": _DEFAULT,
                "model_api_base_overrides": overrides,
            }),
        ),
        patch(
            "poindexter.services.llm_providers.litellm_provider._coerce_override_map",
            lambda v: dict(v or {}),
        ),
        patch("poindexter.services.ollama_client.resolve_num_ctx", lambda *_a, **_k: 8192),
        patch("poindexter.utils.findings.emit_finding", lambda **_k: None),
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
    assert body["options"]["num_ctx"] == 16384, (
        "warm must use the context real calls request (pinned_llm_endpoint_num_ctx, "
        "which dispatch_complete enforces on a pinned route) — Ollama reloads "
        "when num_ctx changes, so warming at a different one wastes the load"
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
    from poindexter.plugins.registry import get_core_samples

    jobs = get_core_samples().get("jobs", [])
    names = {getattr(j, "name", None) for j in jobs}
    assert "warm_pinned_llm_endpoints" in names, (
        "WarmPinnedLlmEndpointsJob is not in the registry's job samples — it "
        f"would never run. Registered jobs: {sorted(n for n in names if n)}"
    )


# --- the shared-slot trap (2026-09-17) -------------------------------------
#
# A pinned instance is normally a ONE-slot instance (OLLAMA_MAX_LOADED_MODELS=1).
# Routing two different tags to it made every fire warm A (evicting B) and then
# warm B (evicting A): two 50 s loads per fire, the judge cold half the time.

_OTHER_MODEL = "ollama/qwen3-vl:30b-a3b-instruct"
_OTHER_TAG = "qwen3-vl:30b-a3b-instruct"
_TWO_TAGS = {_MODEL: _PINNED, _OTHER_MODEL: _PINNED}


def _site_config_with_cap(cap: str) -> MagicMock:
    sc = _site_config()
    sc.get.side_effect = lambda key, default="": {
        "ollama_num_ctx": "8192",
        "warm_pinned_llm_max_models_per_endpoint": cap,
    }.get(key, default)
    return sc


@pytest.mark.asyncio
async def test_second_tag_on_a_full_single_slot_endpoint_is_not_warmed():
    """One resident pin + one absent sibling: warming the sibling would EVICT
    the pin, so the job must leave it alone and count it as skipped."""
    client, ctx = _client({"models": [{"name": _OTHER_TAG, "size_vram": 1}]})
    result = await _run(ctx, _TWO_TAGS)

    assert result.ok is True
    client.post.assert_not_awaited()
    assert result.changes_made == 0
    assert result.metrics["already_resident"] == 1
    assert result.metrics["skipped_shared_slot"] == 1
    assert "skipped_shared_slot=1" in result.detail


@pytest.mark.asyncio
async def test_cold_endpoint_with_two_tags_warms_exactly_one():
    """Both absent: warm the first, then the slot is full — the second tag must
    not be loaded in the SAME fire either (that is the ping-pong's first leg)."""
    client, ctx = _client({"models": []})
    result = await _run(ctx, _TWO_TAGS)

    client.post.assert_awaited_once()
    assert client.post.await_args.kwargs["json"]["model"] == _TAG
    assert result.metrics["warmed"] == 1
    assert result.metrics["skipped_shared_slot"] == 1


@pytest.mark.asyncio
async def test_raising_the_per_endpoint_cap_warms_both():
    """An instance that really runs OLLAMA_MAX_LOADED_MODELS=2 opts in via the
    setting; the cap is DB-first, not a constant."""
    client, ctx = _client({"models": []})
    result = await _run(ctx, _TWO_TAGS, site_config=_site_config_with_cap("2"))

    assert client.post.await_count == 2
    assert {c.kwargs["json"]["model"] for c in client.post.await_args_list} == {_TAG, _OTHER_TAG}
    assert result.metrics["warmed"] == 2
    assert result.metrics["skipped_shared_slot"] == 0


@pytest.mark.asyncio
async def test_two_spellings_of_one_tag_are_one_model():
    """``ollama/x`` and ``ollama_chat/x`` route the same weights; they must
    count as ONE tag, not trip the shared-slot guard against each other."""
    client, ctx = _client({"models": []})
    result = await _run(ctx, {"ollama/" + _TAG: _PINNED, "ollama_chat/" + _TAG: _PINNED})

    client.post.assert_awaited_once()
    assert result.metrics["warmed"] == 1
    assert result.metrics["skipped_shared_slot"] == 0
    assert result.metrics["pinned_endpoints"] == 1


@pytest.mark.asyncio
async def test_overcommitted_endpoint_raises_a_finding_naming_the_tags():
    """The skip must be VISIBLE: an operator who routed two tags to one slot
    gets told which one is being held and which is not being warmed."""
    import contextlib

    client, ctx = _client({"models": [{"name": _OTHER_TAG, "size_vram": 1}]})
    findings: list[dict[str, Any]] = []
    with contextlib.ExitStack() as stack:
        for p in _patches(ctx, _TWO_TAGS):
            stack.enter_context(p)
        stack.enter_context(
            patch("poindexter.utils.findings.emit_finding", lambda **kw: findings.append(kw)),
        )
        await WarmPinnedLlmEndpointsJob().run(
            pool=MagicMock(), config={"_site_config": _site_config()},
        )

    kinds = [f["kind"] for f in findings]
    assert kinds == ["pinned_endpoint_overcommitted"], kinds
    body = findings[0]["body"]
    assert _OTHER_TAG in body and _TAG in body
    assert findings[0]["severity"] == "warn"
    assert findings[0]["dedup_key"] == f"pinned_endpoint_overcommitted_{_PINNED}"


def test_non_integer_cap_falls_back_to_one():
    from poindexter.services.jobs.warm_pinned_llm_endpoints import _max_models_per_endpoint

    assert _max_models_per_endpoint(_site_config_with_cap("two")) == 1
    assert _max_models_per_endpoint(_site_config_with_cap("0")) == 1
    assert _max_models_per_endpoint(_site_config_with_cap("3")) == 3


# --- one context per pinned endpoint (2026-09-25) --------------------------
#
# Ollama reloads a resident model for any other num_ctx. The judge alternated
# 16384 / 32768 / 8192 on 2026-09-24 (48 llama-server starts): the rails asked
# for one size, a host poller and the brain's re-pin got the instance default,
# an ad-hoc run got 8192. The warm must load at the size dispatch enforces, and
# a model resident at any other size must be reported, not reloaded.


async def _run_collecting(ctx, overrides, site_config):
    import contextlib

    findings: list[dict[str, Any]] = []
    with contextlib.ExitStack() as stack:
        for p in _patches(ctx, overrides):
            stack.enter_context(p)
        stack.enter_context(
            patch("poindexter.utils.findings.emit_finding", lambda **kw: findings.append(kw)),
        )
        result = await WarmPinnedLlmEndpointsJob().run(
            pool=MagicMock(), config={"_site_config": site_config},
        )
    return result, findings


@pytest.mark.asyncio
async def test_warm_uses_the_pinned_size_not_the_fleet_default():
    """ollama_num_ctx is the shared endpoint's default; a pinned endpoint runs
    at its own setting, and the warm must match THAT or it is thrown away."""
    client, ctx = _client({"models": []})
    result = await _run(ctx, {_MODEL: _PINNED}, site_config=_site_config(num_ctx="8192", pinned="24576"))

    assert client.post.await_args.kwargs["json"]["options"]["num_ctx"] == 24576
    assert result.metrics["warm_num_ctx"] == 24576


@pytest.mark.asyncio
async def test_opting_out_warms_at_the_fleet_default_as_before():
    """pinned_llm_endpoint_num_ctx=0 hands pinned calls back to per-phase sizes;
    the warm then falls back to what it always used."""
    client, ctx = _client({"models": []})
    await _run(ctx, {_MODEL: _PINNED}, site_config=_site_config(pinned="0"))

    # _patches stubs resolve_num_ctx (the fleet-default path) to 8192.
    assert client.post.await_args.kwargs["json"]["options"]["num_ctx"] == 8192


@pytest.mark.asyncio
async def test_resident_at_another_size_is_reported_and_left_loaded():
    """The 10-minute poller's 32768 load: the next rail call reloads it, and
    re-warming would only fight the poller. Report it; touch nothing."""
    client, ctx = _client({"models": [{"name": _TAG, "context_length": 32768}]})
    result, findings = await _run_collecting(ctx, {_MODEL: _PINNED}, _site_config())

    client.post.assert_not_awaited()
    assert result.metrics["context_mismatch"] == 1
    assert "context_mismatch=1" in result.detail
    assert [f["kind"] for f in findings] == ["pinned_endpoint_context_mismatch"]
    finding = findings[0]
    assert finding["severity"] == "warn"
    assert finding["dedup_key"] == f"pinned_endpoint_context_mismatch_{_PINNED}"
    assert "32768" in finding["title"] and "16384" in finding["title"]
    assert "pinned_llm_endpoint_num_ctx" in finding["body"]
    assert "OLLAMA_CONTEXT_LENGTH" in finding["body"]


@pytest.mark.asyncio
async def test_resident_at_the_pinned_size_is_quiet():
    client, ctx = _client({"models": [{"name": _TAG, "context_length": 16384}]})
    result, findings = await _run_collecting(ctx, {_MODEL: _PINNED}, _site_config())

    assert findings == []
    assert result.metrics["context_mismatch"] == 0
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unreported_context_is_unknown_not_a_mismatch():
    """An Ollama too old to put context_length in /api/ps must not page."""
    client, ctx = _client({"models": [{"name": _TAG, "size_vram": 1}]})
    result, findings = await _run_collecting(ctx, {_MODEL: _PINNED}, _site_config())

    assert findings == []
    assert result.metrics["context_mismatch"] == 0


@pytest.mark.asyncio
async def test_opted_out_endpoints_are_not_size_checked():
    """With the enforcement off, per-phase sizes are the configured intent, so
    a resident size proves nothing."""
    client, ctx = _client({"models": [{"name": _TAG, "context_length": 32768}]})
    result, findings = await _run_collecting(ctx, {_MODEL: _PINNED}, _site_config(pinned="0"))

    assert findings == []
    assert result.metrics["context_mismatch"] == 0


def test_resident_contexts_parses_only_real_sizes():
    from poindexter.services.jobs.warm_pinned_llm_endpoints import _resident_contexts

    body = {"models": [
        {"name": "a", "context_length": 16384},
        {"model": "b", "context_length": 32768},
        {"name": "c"},
        {"name": "d", "context_length": "16384"},
        {"name": "e", "context_length": True},
        {"name": "f", "context_length": 0},
        "junk",
    ]}
    assert _resident_contexts(body) == {"a": 16384, "b": 32768}
    assert _resident_contexts(None) == {}
