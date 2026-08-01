"""Model-resolution tests for ``pipeline_architect.compose``.

Graph composition is a **satellite** phase — the architect LLM designs a
pipeline; it is NOT the blog draft (which has its own resolver in
``ai_content_generator``). So it must never bill cloud prices when an operator
pins ``pipeline_writer_model`` to a paid model for a writer experiment.

Regression guard for the 2026-07-07 Sonnet-canary leak (Refs
Glad-Labs/poindexter#866): the architect used to fall through to
``resolve_local_model``, which returns ``pipeline_writer_model`` verbatim, so a
paid writer pin silently billed every graph composition at cloud rates. It now
routes through ``resolve_local_writer_model`` (guaranteed-LOCAL). The dedicated
``pipeline_architect_model`` override still wins when set.

The LLM call (``_ollama_chat_text``) and the atom catalog (``to_catalog_text``)
are the only things mocked; the real resolver runs.
"""

from __future__ import annotations

from typing import Any

import pytest

from services import pipeline_architect
from services.site_config import SiteConfig


def _patch_io(monkeypatch, captured: dict[str, Any]) -> None:
    """Patch the two collaborators ``compose`` needs to reach its LLM call:
    a non-empty catalog (else it early-returns) and the chat helper (capture
    the resolved ``model`` and return junk so compose finishes in one attempt).
    """

    async def _fake_ollama(prompt: str, *, model: str, **_kwargs: Any) -> str:
        captured["model"] = model
        return "{}"  # invalid graph -> compose returns ok=False, model captured

    monkeypatch.setattr(
        pipeline_architect, "to_catalog_text",
        lambda: "restart_container v1 | atom | tier=budget | cost=0",
    )
    monkeypatch.setattr(pipeline_architect, "_ollama_chat_text", _fake_ollama)


@pytest.mark.asyncio
async def test_paid_writer_does_not_leak_routes_to_local_writer(monkeypatch):
    """No dedicated architect pin + a PAID ``pipeline_writer_model``: the
    architect must resolve the LOCAL writer pin, never the paid model."""
    captured: dict[str, Any] = {}
    _patch_io(monkeypatch, captured)

    sc = SiteConfig(initial_config={
        "pipeline_architect_model": "",  # no dedicated override
        "pipeline_writer_model": "anthropic/claude-sonnet-5",  # PAID
        "pipeline_local_writer_model": "ollama/gemma3:27b",  # LOCAL pin
    })

    await pipeline_architect.compose("compose a pipeline", site_config=sc, max_attempts=1)

    # Pre-fix (resolve_local_model) returned "anthropic/claude-sonnet-5" verbatim.
    assert captured["model"] == "gemma3:27b"


@pytest.mark.asyncio
async def test_dedicated_architect_model_override_still_wins(monkeypatch):
    """The dedicated ``pipeline_architect_model`` pin takes precedence over the
    writer chain — an operator choosing an architect model is honored verbatim
    (``ollama/`` prefix stripped), unaffected by the satellite-resolver swap."""
    captured: dict[str, Any] = {}
    _patch_io(monkeypatch, captured)

    sc = SiteConfig(initial_config={
        "pipeline_architect_model": "ollama/qwen3:14b",
        # A paid writer pin present to prove the override short-circuits it.
        "pipeline_writer_model": "anthropic/claude-sonnet-5",
    })

    await pipeline_architect.compose("compose a pipeline", site_config=sc, max_attempts=1)

    assert captured["model"] == "qwen3:14b"


@pytest.mark.asyncio
async def test_compose_requests_constrained_json(monkeypatch):
    """compose() must request grammar-constrained JSON output AND the
    thinking channel off (poindexter#970). think=False alone is delivered
    on the wire but qwen3-vl still streams literal deliberation into
    content on hard structured tasks — response_format json_object is the
    lever that makes prose impossible."""
    captured: dict[str, Any] = {}

    async def _fake_ollama(prompt: str, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "{}"

    monkeypatch.setattr(
        pipeline_architect, "to_catalog_text",
        lambda: "restart_container v1 | atom | tier=budget | cost=0",
    )
    monkeypatch.setattr(pipeline_architect, "_ollama_chat_text", _fake_ollama)

    sc = SiteConfig(initial_config={
        "pipeline_architect_model": "ollama_chat/qwen3-vl:30b",
    })
    await pipeline_architect.compose(
        "a lean post plan", site_config=sc, max_attempts=1,
    )
    assert captured.get("think") is False
    assert captured.get("response_format") == {"type": "json_object"}
