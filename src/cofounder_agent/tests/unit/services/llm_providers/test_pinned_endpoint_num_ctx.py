"""One context per GPU-pinned endpoint (``pinned_llm_endpoint_num_ctx``).

The :11435 judge instance holds one model at one context size, and Ollama
reloads a resident model whenever a request asks for another ``num_ctx``: a
10-40 s cold load of the 19.6 GB judge on the 3090, mid-rail. Before this, the
callers of that one model each sent their own size: per-phase keys (16384), an
ad-hoc run with no app container (8192, ``resolve_num_ctx``'s fallback), the
brain's RAM-recycle re-pin (none, so 32768) and a host poller (none, so 32768).
ollama-vision.service logged 48 llama-server starts on 2026-09-24.

Pinned here: every dispatch routed to a pinned endpoint runs at ONE size read
from the DB, whatever the caller or its phase key asked for, and calls on the
shared endpoint are untouched.

No module-level asyncio mark: pyproject ``asyncio_mode = "auto"``.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import poindexter.services.llm_providers.dispatcher as d
from poindexter.services.settings_defaults import DEFAULTS
from poindexter.services.site_config import SiteConfig

_DEFAULT_BASE = "http://host.docker.internal:11434"
_JUDGE_BASE = "http://host.docker.internal:11435"
_JUDGE = "ollama/qwen3-vl:30b-a3b-instruct"
_WRITER = "ollama/gemma-4-31B-it-qat:latest"
_DECLARED = int(DEFAULTS[d.PINNED_NUM_CTX_KEY])

_PROVIDER_CONFIG = {
    "api_base": _DEFAULT_BASE,
    "model_api_base_overrides": {
        _JUDGE: _JUDGE_BASE,
        "ollama_chat/qwen3-vl:30b-a3b-instruct": _JUDGE_BASE,
    },
}

# Every phase that dispatched to the judge on 2026-09-24/25 (cost_logs), plus
# the console + architect pins that share it.
_JUDGE_PHASES = (
    "qa_shot_vision", "qa_shot_vision_calibration", "qa_ragas_judge",
    "qa_deepeval_judge", "caption_image", "image_fanout_judge",
    "qa_gate_topic_delivery", "qa_gate_internal_consistency",
    "qa_vision_image_relevance", "qa_review", "media_qa_faithfulness",
    "media_qa_human_detect", "console_chat", "pipeline_architect",
)


@pytest.fixture(autouse=True)
def _fresh_override_log():
    d._pinned_ctx_overrides_logged.clear()
    yield
    d._pinned_ctx_overrides_logged.clear()


def _container(**settings: str) -> SimpleNamespace:
    return SimpleNamespace(site_config=SiteConfig(initial_config=settings))


class _Provider:
    name = "litellm"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            prompt_tokens=1, completion_tokens=1, finish_reason="stop", text="ok",
        )


def _wire(monkeypatch, *, container=None, vram_guard=False) -> tuple[_Provider, list]:
    """Stub dispatch_complete's collaborators; return the provider + clamp log."""
    provider = _Provider()
    clamps: list[int] = []

    async def _get_provider(_pool, _tier):
        return provider

    async def _get_provider_config(_pool, _name):
        return dict(_PROVIDER_CONFIG)

    async def _noop(*_a, **_k):
        return None

    async def _clamp(_pool, _model, num_ctx, _pc):
        clamps.append(num_ctx)
        return num_ctx

    monkeypatch.setattr(d, "get_provider", _get_provider)
    monkeypatch.setattr(d, "get_provider_config", _get_provider_config)
    monkeypatch.setattr(d, "_enforce_budget_if_paid", _noop)
    monkeypatch.setattr(d, "_record_dispatch_cost", _noop)
    monkeypatch.setattr(d, "_vram_guard_enabled", lambda: vram_guard)
    monkeypatch.setattr(d, "_clamp_num_ctx_to_budget", _clamp)
    monkeypatch.setattr(d, "_gpu_serialize_local_dispatch", lambda _m, _pc: False)
    monkeypatch.setattr(
        "poindexter.services.container_registry.get_container", lambda: container,
    )
    return provider, clamps


async def _dispatch(model: str = _JUDGE, phase: str = "qa_shot_vision", pool=None, **kwargs):
    return await d.dispatch_complete(
        pool if pool is not None else object(),
        [{"role": "user", "content": "score this"}],
        model, tier="standard", phase=phase, **kwargs,
    )


# ---------------------------------------------------------------------------
# The setting itself
# ---------------------------------------------------------------------------


def test_the_declared_default_is_the_fleet_judge_size():
    """16384 is what every judge caller already sent on prod; the setting must
    not move the judge the day it lands."""
    assert _DECLARED == 16384


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, _DECLARED),      # row not seeded yet: the declared default, NOT off
        ("", _DECLARED),
        ("  ", _DECLARED),
        ("24576", 24576),
        (" 12288 ", 12288),
        (32768, 32768),
        ("0", None),            # explicit opt-out
        ("-1", None),
    ],
)
def test_parse_pinned_num_ctx(raw, expected):
    assert d.parse_pinned_num_ctx(raw) == expected


def test_an_unparseable_value_is_loud_and_keeps_one_size(caplog):
    with caplog.at_level(logging.WARNING, logger=d.logger.name):
        assert d.parse_pinned_num_ctx("sixteen-k") == _DECLARED
    assert "not an integer" in caplog.text


async def test_reads_the_injected_site_config_first(monkeypatch):
    monkeypatch.setattr(
        "poindexter.services.container_registry.get_container",
        lambda: _container(pinned_llm_endpoint_num_ctx="8192"),
    )
    sc = SiteConfig(initial_config={"pinned_llm_endpoint_num_ctx": "24576"})
    assert await d.pinned_endpoint_num_ctx(None, site_config=sc) == 24576


async def test_reads_the_container_when_nothing_is_injected(monkeypatch):
    monkeypatch.setattr(
        "poindexter.services.container_registry.get_container",
        lambda: _container(pinned_llm_endpoint_num_ctx="24576"),
    )
    assert await d.pinned_endpoint_num_ctx(None) == 24576


async def test_a_process_with_no_container_reads_the_row_not_a_code_default(monkeypatch):
    """The 2026-09-24 20:50 smoke: no container, so resolve_num_ctx said 8192
    and reloaded the judge four times. A pinned route asks the DB instead."""
    monkeypatch.setattr("poindexter.services.container_registry.get_container", lambda: None)
    pool = SimpleNamespace(fetchval=AsyncMock(return_value="24576"))

    assert await d.pinned_endpoint_num_ctx(pool) == 24576
    pool.fetchval.assert_awaited_once()
    assert pool.fetchval.await_args.args[1] == "pinned_llm_endpoint_num_ctx"


async def test_an_unreadable_row_still_pins_one_size(monkeypatch, caplog):
    monkeypatch.setattr("poindexter.services.container_registry.get_container", lambda: None)
    pool = SimpleNamespace(fetchval=AsyncMock(side_effect=RuntimeError("pool closed")))

    with caplog.at_level(logging.WARNING, logger=d.logger.name):
        assert await d.pinned_endpoint_num_ctx(pool) == _DECLARED
    assert "could not read pinned_llm_endpoint_num_ctx" in caplog.text


# ---------------------------------------------------------------------------
# dispatch_complete — every judge caller lands on one size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", _JUDGE_PHASES)
async def test_every_judge_phase_runs_at_the_one_pinned_size(monkeypatch, phase):
    """Divergent per-phase keys are exactly how the judge got three sizes;
    through a pinned route they must all collapse to one."""
    container = _container(
        pinned_llm_endpoint_num_ctx="16384",
        ollama_num_ctx="8192",
        qa_ragas_judge_num_ctx="32768",
        qa_deepeval_judge_num_ctx="24576",
        qa_shot_vision_num_ctx="8192",
        caption_image_num_ctx="4096",
    )
    provider, _ = _wire(monkeypatch, container=container)

    await _dispatch(phase=phase)

    assert provider.calls[-1]["num_ctx"] == 16384


async def test_explicit_num_ctx_is_overridden_on_a_pinned_route(monkeypatch):
    """Ragas and DeepEval send their own key explicitly; on a pinned route it
    cannot win, or the judge reloads for them and again for the next rail."""
    provider, _ = _wire(monkeypatch, container=_container(pinned_llm_endpoint_num_ctx="16384"))

    await _dispatch(phase="qa_ragas_judge", num_ctx=32768)

    assert provider.calls[-1]["num_ctx"] == 16384


async def test_both_spellings_of_the_judge_are_pinned(monkeypatch):
    """console_chat / pipeline_architect use ``ollama_chat/``; same weights,
    same instance, same size."""
    provider, _ = _wire(monkeypatch, container=_container(pinned_llm_endpoint_num_ctx="16384"))

    await _dispatch(model="ollama_chat/qwen3-vl:30b-a3b-instruct", phase="console_chat", num_ctx=8192)

    assert provider.calls[-1]["num_ctx"] == 16384


async def test_the_override_is_said_once_per_phase_and_size(monkeypatch, caplog):
    provider, _ = _wire(monkeypatch, container=_container(pinned_llm_endpoint_num_ctx="16384"))

    with caplog.at_level(logging.WARNING, logger=d.logger.name):
        await _dispatch(phase="qa_ragas_judge", num_ctx=32768)
        await _dispatch(phase="qa_ragas_judge", num_ctx=32768)
        await _dispatch(phase="qa_deepeval_judge", num_ctx=16384)  # agrees: silent

    warnings = [r for r in caplog.records if "GPU-pinned" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    message = warnings[0].getMessage()
    assert "phase=qa_ragas_judge" in message
    assert "num_ctx=32768" in message
    assert "pinned_llm_endpoint_num_ctx=16384" in message


async def test_a_pinned_route_skips_the_vram_clamp(monkeypatch):
    """The clamp budgets the whole GPU pool, not the pinned card, and a clamp
    that answered differently call to call would hand the judge a second size."""
    provider, clamps = _wire(
        monkeypatch, container=_container(pinned_llm_endpoint_num_ctx="16384"), vram_guard=True,
    )

    await _dispatch()

    assert clamps == []
    assert provider.calls[-1]["num_ctx"] == 16384


async def test_a_container_less_process_sends_the_db_size(monkeypatch):
    provider, _ = _wire(monkeypatch, container=None)
    pool = SimpleNamespace(fetchval=AsyncMock(return_value="16384"))

    await _dispatch(pool=pool)

    assert provider.calls[-1]["num_ctx"] == 16384


# ---------------------------------------------------------------------------
# ...and nothing else moves
# ---------------------------------------------------------------------------


async def test_the_shared_endpoint_keeps_per_phase_sizes(monkeypatch):
    """A writer on :11434 is not pinned: its explicit size and the backfill
    behave exactly as before, clamp included."""
    container = _container(
        pinned_llm_endpoint_num_ctx="16384", ollama_num_ctx="8192",
        draft_generation_num_ctx="32768",
    )
    provider, clamps = _wire(monkeypatch, container=container, vram_guard=True)

    await _dispatch(model=_WRITER, phase="draft_generation", num_ctx=24576)
    await _dispatch(model=_WRITER, phase="draft_generation")
    await _dispatch(model=_WRITER, phase="seo")

    assert [c["num_ctx"] for c in provider.calls] == [24576, 32768, 8192]
    assert clamps == [24576, 32768, 8192]


async def test_zero_opts_out_back_to_per_phase_sizes(monkeypatch):
    container = _container(
        pinned_llm_endpoint_num_ctx="0", ollama_num_ctx="8192", qa_ragas_judge_num_ctx="32768",
    )
    provider, clamps = _wire(monkeypatch, container=container, vram_guard=True)

    await _dispatch(phase="qa_ragas_judge")
    await _dispatch(phase="qa_shot_vision", num_ctx=12288)

    assert [c["num_ctx"] for c in provider.calls] == [32768, 12288]
    assert clamps == [32768, 12288], "opted out = the legacy path, clamp and all"


async def test_a_paid_model_in_the_override_map_gets_no_num_ctx(monkeypatch):
    """num_ctx is an Ollama option; a cloud model routed through the map (a
    proxy, say) must not have one invented for it."""
    provider, _ = _wire(monkeypatch, container=_container(pinned_llm_endpoint_num_ctx="16384"))
    monkeypatch.setattr(d, "_routes_to_pinned_endpoint", lambda _m, _pc: True)
    monkeypatch.setattr(d, "_is_paid_llm_call", lambda _m, _pc: True)

    await _dispatch(model="anthropic/claude-sonnet-5", phase="qa_review")

    assert "num_ctx" not in provider.calls[-1]
