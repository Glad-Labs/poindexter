"""Cost-tier resolution path for ``services.title_generation.generate_canonical_title``.

Lane B sweep #2 (Writer / content surface). Pins the new
``resolve_tier_model(pool, "standard")`` integration: when the cost-tier
mapping is configured, the resolved model flows through to the provider
call. When it isn't, the per-call-site ``pipeline_writer_model`` setting
is the last-ditch fallback. When BOTH miss, ``notify_operator()`` fires
and the function returns None — no silent literal default.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.title_generation import generate_canonical_title


class _FakeConn:
    def __init__(self, value: str | None):
        self._value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def fetchval(self, query: str, *args: Any) -> str | None:
        return self._value


class _FakePool:
    def __init__(self, value: str | None):
        self._value = value

    def acquire(self):
        return _FakeConn(self._value)


def _make_provider(captured: dict[str, Any]) -> MagicMock:
    """Return a mock ollama_native provider that captures the model arg."""
    provider = MagicMock()
    provider.name = "ollama_native"

    async def _complete(**kwargs):
        captured["model"] = kwargs.get("model")
        captured["called"] = True
        result = MagicMock()
        result.text = "A Crisp SEO-Optimized Title"
        return result

    provider.complete = AsyncMock(side_effect=_complete)
    return provider


@pytest.mark.asyncio
async def test_uses_cost_tier_standard_when_mapping_present():
    """Tier mapping is the primary path; ``ollama/`` prefix is stripped."""
    captured: dict[str, Any] = {}
    provider = _make_provider(captured)

    fake_sc = MagicMock()
    fake_sc._pool = _FakePool("ollama/gemma3:27b")
    fake_sc.get.return_value = ""  # pipeline_writer_model unused on the happy path
    fake_sc.get_int.return_value = 4000

    with patch("services.title_generation.site_config", fake_sc), \
         patch(
             "plugins.registry.get_all_llm_providers",
             return_value=[provider],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        out = await generate_canonical_title(
            topic="AI trends",
            primary_keyword="AI",
            content_excerpt="Body excerpt",
        )

    assert captured["called"] is True
    # ollama/ prefix stripped — providers expect the bare name
    assert captured["model"] == "gemma3:27b"
    assert out == "A Crisp SEO-Optimized Title"


@pytest.mark.asyncio
async def test_falls_back_to_pipeline_writer_model_when_tier_missing():
    """When ``cost_tier.standard.model`` is empty, the legacy setting wins."""
    captured: dict[str, Any] = {}
    provider = _make_provider(captured)

    fake_sc = MagicMock()
    fake_sc._pool = _FakePool(None)  # tier mapping missing
    # pipeline_writer_model is the last-ditch fallback per the no-silent-
    # defaults guarantee (resolves through the legacy code path).
    fake_sc.get.return_value = "ollama/glm-4.7-5090"
    fake_sc.get_int.return_value = 4000

    with patch("services.title_generation.site_config", fake_sc), \
         patch(
             "plugins.registry.get_all_llm_providers",
             return_value=[provider],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        out = await generate_canonical_title(
            topic="AI", primary_keyword="AI", content_excerpt="x",
        )

    assert captured["called"] is True
    assert captured["model"] == "glm-4.7-5090"
    assert out is not None


@pytest.mark.asyncio
async def test_pages_operator_when_both_miss():
    """No tier mapping AND no pipeline_writer_model — fail loud, return None."""
    fake_sc = MagicMock()
    fake_sc._pool = _FakePool(None)
    fake_sc.get.return_value = ""  # pipeline_writer_model also empty
    fake_sc.get_int.return_value = 4000

    notify = AsyncMock()
    provider = MagicMock()
    provider.name = "ollama_native"
    provider.complete = AsyncMock()

    with patch("services.title_generation.site_config", fake_sc), \
         patch(
             "plugins.registry.get_all_llm_providers",
             return_value=[provider],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm, \
         patch(
             "services.integrations.operator_notify.notify_operator",
             new=notify,
         ):
        pm.return_value.get_prompt.return_value = "PROMPT"
        out = await generate_canonical_title(
            topic="AI", primary_keyword="AI", content_excerpt="x",
        )

    assert out is None
    notify.assert_awaited_once()
    msg = notify.await_args.args[0]
    assert "title_generation" in msg
    assert "cost_tier" in msg
    # Provider was never called because we bailed before dispatch.
    provider.complete.assert_not_awaited()
