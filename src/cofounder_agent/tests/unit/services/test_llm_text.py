"""Unit tests for :mod:`services.llm_text`.

The 2026-05-16 refactor unified three private ``_ollama_chat_text``
duplicates (in ``atoms.narrate_bundle``, ``writer_rag_modes.deterministic_compositor``,
and ``pipeline_architect``) into this single shared helper. The helper
now routes through :func:`services.llm_providers.dispatcher.dispatch_complete`
when a ``pool`` is provided (production path — picks up
``plugin.llm_provider.primary.<tier>``, including LiteLLM) and falls
back to direct httpx → local Ollama when no pool is available
(tests / bootstrap).

These tests pin the routing contract:

- Pool provided → call goes through ``dispatch_complete``.
- No pool → direct httpx POST to ``/api/chat`` on
  ``site_config['local_llm_api_url']``.
- ``maybe_unwrap_json`` fires on both paths (some local models still
  emit ``{"thought": "..."}`` envelopes unprompted).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm_text import (
    maybe_unwrap_json,
    ollama_chat_text,
    resolve_local_model,
    resolve_structured_model,
)

# No module-level asyncio mark: ``asyncio_mode = "auto"`` (pyproject.toml)
# already auto-marks coroutine tests. An explicit mark wrongly tagged the
# sync tests here, emitting a PytestWarning (Glad-Labs/poindexter#997).


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubSiteConfig:
    """Minimal SiteConfig stand-in.

    Returns the same model + base_url for every test so assertions can
    pin the expected resolved values.
    """

    def __init__(self, model: str = "gemma3:27b", base_url: str = "http://localhost:11434"):
        self._model = model
        self._base_url = base_url

    def get(self, key: str, default: Any = None) -> Any:
        if key == "pipeline_writer_model":
            return self._model
        if key == "local_llm_api_url":
            return self._base_url
        if key == "cost_tier.standard.model":
            return self._model
        return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(default)

    def get_int(self, key: str, default: int = 0) -> int:
        return int(default)


def _fake_completion(text: str = "dispatched output", prompt_tokens: int = 7, completion_tokens: int = 11):
    """Build a Completion-shaped object for ``dispatch_complete`` returns."""
    completion = MagicMock()
    completion.text = text
    completion.prompt_tokens = prompt_tokens
    completion.completion_tokens = completion_tokens
    completion.total_tokens = prompt_tokens + completion_tokens
    completion.finish_reason = "stop"
    return completion


# ---------------------------------------------------------------------------
# resolve_local_model
# ---------------------------------------------------------------------------


class TestResolveLocalModel:
    def test_explicit_model_wins_and_strips_ollama_prefix(self):
        assert resolve_local_model("ollama/gemma3:27b") == "gemma3:27b"

    def test_pipeline_writer_model_is_preferred(self):
        sc = _StubSiteConfig(model="ollama/glm-4.7:latest")
        assert resolve_local_model(site_config=sc) == "glm-4.7:latest"

    def test_pipeline_writer_model_beats_cost_tier_standard(self):
        """Canonical precedence: pipeline_writer_model > cost_tier.standard.model.

        Regression guard for glad-labs-stack#1281 — this function is the
        canonical reference that all other writer-model resolvers must follow.
        """
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda k, d=None: {
            "pipeline_writer_model": "my-pinned-writer",
            "cost_tier.standard.model": "cost-tier-model",
        }.get(k, d or ""))
        result = resolve_local_model(site_config=sc)
        assert result == "my-pinned-writer", (
            f"pipeline_writer_model must win over cost_tier.standard.model; got {result!r}"
        )

    def test_cost_tier_standard_used_when_pipeline_writer_empty(self):
        """cost_tier.standard.model is the correct fallback when pipeline_writer_model unset."""
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda k, d=None: {
            "pipeline_writer_model": "",
            "cost_tier.standard.model": "cost-tier-model",
        }.get(k, d or ""))
        result = resolve_local_model(site_config=sc)
        assert result == "cost-tier-model"

    def test_raises_when_nothing_resolves(self):
        """Per ``feedback_no_silent_defaults``: missing config fails loud."""
        sc = MagicMock()
        sc.get = MagicMock(return_value="")
        with pytest.raises(ValueError, match="no writer model resolvable"):
            resolve_local_model(site_config=sc)

    def test_raises_when_no_site_config_and_no_model(self):
        with pytest.raises(ValueError, match="site_config is required"):
            resolve_local_model()


# ---------------------------------------------------------------------------
# resolve_structured_model
# ---------------------------------------------------------------------------


class TestResolveStructuredModel:
    """The 2026-05-28 stall fix: structured-JSON calls must NOT resolve to a
    reasoning writer model. This resolver reads the DB-configurable
    ``structured_extraction_model`` instead."""

    def test_explicit_model_wins_and_strips_prefix(self):
        assert resolve_structured_model("ollama/gemma3:27b") == "gemma3:27b"

    def test_structured_setting_is_preferred(self):
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda k, d=None: {
            "structured_extraction_model": "ollama/gemma3:27b",
            "cost_tier.standard.model": "ollama/glm-4.7-5090:latest",
        }.get(k, d))
        assert resolve_structured_model(site_config=sc) == "gemma3:27b"

    def test_falls_back_to_cost_tier_standard(self):
        sc = MagicMock()
        sc.get = MagicMock(side_effect=lambda k, d=None: {
            "structured_extraction_model": "",
            "cost_tier.standard.model": "ollama/qwen3:30b",
        }.get(k, d))
        assert resolve_structured_model(site_config=sc) == "qwen3:30b"

    def test_raises_when_nothing_resolves(self):
        sc = MagicMock()
        sc.get = MagicMock(return_value="")
        with pytest.raises(ValueError, match="no structured-extraction model"):
            resolve_structured_model(site_config=sc)

    def test_raises_when_no_site_config_and_no_model(self):
        with pytest.raises(ValueError, match="site_config is required"):
            resolve_structured_model()


# ---------------------------------------------------------------------------
# ollama_chat_text — dispatcher path (pool provided)
# ---------------------------------------------------------------------------


class TestOllamaChatTextDispatcherPath:
    """Production path: when ``pool`` is provided, route via dispatch_complete."""

    async def test_dispatch_complete_is_called_with_messages_and_model(self):
        pool = MagicMock()
        sc = _StubSiteConfig()
        dispatch = AsyncMock(return_value=_fake_completion("hello from dispatcher"))
        with patch("services.llm_providers.dispatcher.dispatch_complete", dispatch):
            result = await ollama_chat_text(
                "what's the weather?",
                site_config=sc,
                pool=pool,
            )
        assert result == "hello from dispatcher"
        dispatch.assert_awaited_once()
        kwargs = dispatch.await_args.kwargs
        assert kwargs["pool"] is pool
        assert kwargs["model"] == "gemma3:27b"
        assert kwargs["tier"] == "standard"
        # Single user message; no system message.
        assert kwargs["messages"] == [{"role": "user", "content": "what's the weather?"}]

    async def test_dispatch_complete_includes_system_message_when_provided(self):
        pool = MagicMock()
        sc = _StubSiteConfig()
        dispatch = AsyncMock(return_value=_fake_completion("answered"))
        with patch("services.llm_providers.dispatcher.dispatch_complete", dispatch):
            await ollama_chat_text(
                "tell me a joke",
                system="you are a critic",
                site_config=sc,
                pool=pool,
            )
        kwargs = dispatch.await_args.kwargs
        assert kwargs["messages"] == [
            {"role": "system", "content": "you are a critic"},
            {"role": "user", "content": "tell me a joke"},
        ]

    async def test_dispatch_complete_honors_tier_kwarg(self):
        pool = MagicMock()
        sc = _StubSiteConfig()
        dispatch = AsyncMock(return_value=_fake_completion("budget answer"))
        with patch("services.llm_providers.dispatcher.dispatch_complete", dispatch):
            await ollama_chat_text(
                "cheap query",
                site_config=sc,
                pool=pool,
                tier="budget",
            )
        kwargs = dispatch.await_args.kwargs
        assert kwargs["tier"] == "budget"

    async def test_dispatch_path_runs_maybe_unwrap_json(self):
        """Some providers (Ollama) still return JSON envelopes — must unwrap."""
        pool = MagicMock()
        sc = _StubSiteConfig()
        wrapped = '{"thought": "the actual prose"}'
        dispatch = AsyncMock(return_value=_fake_completion(wrapped))
        with patch("services.llm_providers.dispatcher.dispatch_complete", dispatch):
            result = await ollama_chat_text("p", site_config=sc, pool=pool)
        assert result == "the actual prose"

    async def test_dispatch_failure_propagates(self):
        """Per ``feedback_no_silent_defaults``: production-path failures
        must NOT silently fall back to httpx. The caller catches +
        retries; the helper raises."""
        pool = MagicMock()
        sc = _StubSiteConfig()
        dispatch = AsyncMock(side_effect=RuntimeError("provider down"))
        with patch("services.llm_providers.dispatcher.dispatch_complete", dispatch):
            with pytest.raises(RuntimeError, match="provider down"):
                await ollama_chat_text("p", site_config=sc, pool=pool)


# ---------------------------------------------------------------------------
# ollama_chat_text — httpx fallback (no pool)
# ---------------------------------------------------------------------------


class TestOllamaChatTextHttpxFallback:
    """Test / bootstrap path: no pool → direct httpx → local Ollama."""

    async def test_httpx_called_when_no_pool(self):
        sc = _StubSiteConfig(base_url="http://localhost:11434")

        # Mock the httpx.AsyncClient context manager.
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={
            "message": {"content": "httpx-direct output"},
            "prompt_eval_count": 5,
            "eval_count": 8,
        })
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await ollama_chat_text("hi", site_config=sc)

        assert result == "httpx-direct output"
        # Verify the POST went to /api/chat on the configured base URL.
        post_kwargs = mock_client.post.await_args
        assert post_kwargs[0][0] == "http://localhost:11434/api/chat"
        payload = post_kwargs[1]["json"]
        assert payload["model"] == "gemma3:27b"
        assert payload["stream"] is False
        assert payload["messages"] == [{"role": "user", "content": "hi"}]

    async def test_httpx_fallback_dispatcher_is_not_called(self):
        """Explicit: when no pool is provided, the dispatcher is bypassed."""
        sc = _StubSiteConfig()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"message": {"content": "x"}})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        dispatch = AsyncMock()
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("services.llm_providers.dispatcher.dispatch_complete", dispatch),
        ):
            await ollama_chat_text("hi", site_config=sc)

        dispatch.assert_not_awaited()

    async def test_httpx_fallback_unwraps_json_envelope(self):
        sc = _StubSiteConfig()
        wrapped = '{"response": "real prose"}'
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"message": {"content": wrapped}})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await ollama_chat_text("p", site_config=sc)
        assert result == "real prose"


# ---------------------------------------------------------------------------
# maybe_unwrap_json
# ---------------------------------------------------------------------------


class TestMaybeUnwrapJson:
    def test_passes_through_non_json(self):
        assert maybe_unwrap_json("plain prose") == "plain prose"

    def test_unwraps_thought_envelope(self):
        assert maybe_unwrap_json('{"thought": "inner"}') == "inner"

    def test_unwraps_content_envelope(self):
        assert maybe_unwrap_json('{"content": "the post"}') == "the post"

    def test_returns_input_when_envelope_has_no_known_key(self):
        # Unknown shape — leave as-is so the caller can inspect.
        wrapped = '{"unknown_key": "value"}'
        assert maybe_unwrap_json(wrapped) == wrapped

    def test_handles_empty_string(self):
        assert maybe_unwrap_json("") == ""

    def test_unwraps_body_envelope(self):
        # 2026-05-27: the canonical_blog regression. The writer emitted
        # ``{"title": "T", "body": "<actual markdown>"}`` and the previous
        # key list missed it because "body" wasn't listed — leaving the raw
        # JSON envelope in pipeline_versions.content.
        wrapped = '{"title": "Headline", "body": "# Headline\\n\\nProse here."}'
        assert maybe_unwrap_json(wrapped) == "# Headline\n\nProse here."

    def test_unwraps_markdown_fenced_json_envelope(self):
        # Same incident — the model wrapped the envelope in a fenced code
        # block. Before the fix, ``startswith("{")`` was False so the
        # function bailed and returned the fenced envelope verbatim.
        wrapped = (
            "```json\n"
            '{"title": "Headline", "body": "# Real markdown\\n\\nBody."}\n'
            "```"
        )
        assert maybe_unwrap_json(wrapped) == "# Real markdown\n\nBody."

    def test_unwraps_bare_fenced_envelope(self):
        # Some models open with ``` (no language tag) instead of ```json.
        wrapped = (
            "```\n"
            '{"content": "the inner prose"}\n'
            "```"
        )
        assert maybe_unwrap_json(wrapped) == "the inner prose"

    def test_does_not_unwrap_non_json_fence(self):
        # ```python ... ``` blocks must survive — that's real prose
        # the user/writer intended to ship, not an envelope.
        snippet = "```python\nprint('hi')\n```"
        assert maybe_unwrap_json(snippet) == snippet

    def test_body_key_wins_over_title_key(self):
        # The canonical_blog envelope has BOTH "title" and "body".
        # "body" is the document; "title" is metadata. Body wins.
        wrapped = '{"title": "Just the title", "body": "Full markdown post here."}'
        assert maybe_unwrap_json(wrapped) == "Full markdown post here."

    def test_unwraps_markdown_key(self):
        # Some models prefer "markdown" as the prose key, especially when
        # the system prompt mentions markdown explicitly.
        wrapped = '{"markdown": "# Heading\\n\\nProse."}'
        assert maybe_unwrap_json(wrapped) == "# Heading\n\nProse."

    def test_unwraps_post_body_envelope(self):
        # 2026-05-31: the canonical_blog writer emitted
        # ``{"title": ..., "post_body": "<markdown>"}`` — "post_body" wasn't
        # in the key list, so a ```json-fenced variant reached
        # awaiting_approval and rendered as a raw JSON code block in the
        # mobile preview. Both the bare and fenced shapes must unwrap.
        bare = '{"title": "Zero Trust", "post_body": "## Section\\n\\nProse."}'
        assert maybe_unwrap_json(bare) == "## Section\n\nProse."
        fenced = (
            "```json\n"
            '{"title": "Zero Trust", "post_body": "## Section\\n\\nProse."}\n'
            "```"
        )
        assert maybe_unwrap_json(fenced) == "## Section\n\nProse."

    def test_post_body_key_wins_over_post_fragment(self):
        # "post_body" is the full document; the generic "post" fragment key
        # must not shadow it (post_body is listed first).
        wrapped = '{"post": "teaser", "post_body": "Full markdown body."}'
        assert maybe_unwrap_json(wrapped) == "Full markdown body."
