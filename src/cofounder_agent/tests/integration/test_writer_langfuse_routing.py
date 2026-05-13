"""Writer-pipeline → Langfuse routing smoke test (Glad-Labs/poindexter#407).

Confirms that `dispatch_complete` (now the only LLM trip point in
`services/ai_content_generator.py` + `services/stages/script_for_video.py`
+ `services/stages/cross_model_qa.py`) routes through LiteLLM when the
operator has flipped `plugin.llm_provider.primary.standard='litellm'`
(migration 0160's seeded default), and that the LiteLLM provider
fires its `langfuse_otel` success callback so the Langfuse host
records a span.

This is a **gated** smoke test:

- Requires `INTEGRATION_TESTS=1 REAL_SERVICES_TESTS=1` plus a reachable
  local Ollama at `OLLAMA_URL` (default http://localhost:11434).
- Requires `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
  env vars (or the equivalent app_settings rows on Matt's PC). The
  test does NOT assert that the span lands in Langfuse — there's no
  read-API on the local Langfuse OSS deployment that we want to
  hammer from CI. Instead, after dispatch the test inspects
  `litellm.success_callback` to confirm `langfuse_otel` got registered
  by the lifespan startup path, which is the deterministic check we
  can make from inside the process.

For the actual visual confirmation (a span lands at
http://localhost:3010 within a few seconds), the operator runs this
test locally and tails the Langfuse UI. The CI path stays fast +
deterministic.

CI does NOT run this — Langfuse isn't on the runners. Operator-gated
smoke is the right place; the unit tests in
``tests/unit/services/test_ai_content_generator.py`` +
``tests/unit/services/stages/test_script_for_video.py`` cover the
stub-able code paths.

## Running

Local run on Matt's PC::

    INTEGRATION_TESTS=1 \
    REAL_SERVICES_TESTS=1 \
    OLLAMA_URL=http://localhost:11434 \
    poetry run pytest tests/integration/test_writer_langfuse_routing.py -v
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _ollama_url() -> str:
    """Resolve the Ollama base URL — same precedence as
    test_litellm_cost_parity.py."""
    return (
        os.getenv("OLLAMA_URL")
        or os.getenv("OLLAMA_BASE_URL")
        or "http://localhost:11434"
    )


async def _ollama_is_reachable(url: str, timeout: float = 3.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url.rstrip('/')}/api/tags")
        return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


class _FakePool:
    """Minimal asyncpg-pool stand-in returning a single
    ``plugin.llm_provider.primary.standard`` row so dispatch_complete
    routes to LiteLLM without us touching the real database.

    The test still hits real Ollama through LiteLLM, so the request
    side of the pipeline (litellm.acompletion → http) is exercised
    end-to-end. The Langfuse callback is process-global state
    registered by ``configure_langfuse_callback``; we just check it
    landed.
    """

    def __init__(self, provider_name: str = "litellm"):
        self._provider_name = provider_name

    def acquire(self):  # noqa: D401 — pool API parity
        return _FakeAcquire(self._provider_name)


class _FakeAcquire:
    def __init__(self, provider_name: str):
        self._provider_name = provider_name

    async def __aenter__(self):
        return _FakeConn(self._provider_name)

    async def __aexit__(self, *_args):
        return None


class _FakeConn:
    def __init__(self, provider_name: str):
        self._provider_name = provider_name

    async def fetchval(self, query: str, *args):  # noqa: ARG002
        # Dispatcher's get_provider_name queries by key parameter.
        if args and isinstance(args[0], str):
            key = args[0]
            if key.startswith("plugin.llm_provider.primary."):
                return self._provider_name
            if key.startswith("cost_tier."):
                # Bare model name — LiteLLM provider prepends ``ollama/``
                # via _resolve_model.
                return "tinyllama"
        return None


@pytest.mark.skipif(
    not os.getenv("INTEGRATION_TESTS"),
    reason="INTEGRATION_TESTS=1 required",
)
@pytest.mark.skipif(
    not os.getenv("REAL_SERVICES_TESTS"),
    reason="REAL_SERVICES_TESTS=1 required (uses real Ollama)",
)
async def test_dispatch_complete_routes_through_litellm_and_registers_langfuse_callback():
    """End-to-end: dispatch_complete -> LiteLLM -> Ollama, with the
    Langfuse OTEL callback registered as a side effect of the lifespan
    startup path."""
    url = _ollama_url()
    if not await _ollama_is_reachable(url):
        pytest.skip(f"Ollama not reachable at {url}")

    from services.llm_providers.dispatcher import dispatch_complete
    from services.llm_providers.litellm_provider import configure_langfuse_callback

    # Configure Langfuse callback FIRST — this is what main.py's
    # lifespan does at worker startup. Without it, the litellm
    # success_callback list is empty and no spans land.
    fake_site_config = SimpleNamespace(
        get_bool=lambda _k, _d=False: True,  # langfuse_tracing_enabled=true
        get=lambda k, _d="": {
            "langfuse_host": os.getenv("LANGFUSE_HOST", ""),
            "langfuse_public_key": os.getenv("LANGFUSE_PUBLIC_KEY", ""),
        }.get(k, _d),
        get_secret=_async_secret_lookup,
    )

    if not (
        os.getenv("LANGFUSE_HOST")
        and os.getenv("LANGFUSE_PUBLIC_KEY")
        and os.getenv("LANGFUSE_SECRET_KEY")
    ):
        pytest.skip(
            "Langfuse credentials missing — set LANGFUSE_HOST + "
            "LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY env vars",
        )

    await configure_langfuse_callback(fake_site_config)

    # Confirm the Langfuse callback registered — this is the
    # deterministic check that the smoke test pins.
    import litellm
    assert "langfuse_otel" in (litellm.success_callback or []), (
        "configure_langfuse_callback did not register langfuse_otel — "
        "Langfuse traces will not fire"
    )
    assert "langfuse_otel" in (litellm.failure_callback or []), (
        "configure_langfuse_callback did not register failure callback"
    )

    # Now drive a real call through the dispatcher with
    # provider=litellm so litellm.acompletion is the actual code path.
    pool = _FakePool(provider_name="litellm")
    completion = await dispatch_complete(
        pool=pool,
        messages=[{"role": "user", "content": "Say hello in exactly 3 words."}],
        model="tinyllama",
        tier="standard",
        max_tokens=20,
    )

    # Completion shape assertions — basic sanity that the dispatcher
    # path didn't silently fail. Operator visually confirms the span
    # in Langfuse UI; this just proves the call reached LiteLLM.
    assert completion is not None
    assert isinstance(getattr(completion, "text", None), str)
    # LiteLLM stamps the resolved model with the provider prefix.
    assert "ollama" in getattr(completion, "model", "") or completion.model == "tinyllama"


async def _async_secret_lookup(key: str, _default: str = "") -> str:
    """Mirror SiteConfig.get_secret signature for the smoke test."""
    if key == "langfuse_secret_key":
        return os.getenv("LANGFUSE_SECRET_KEY", "")
    return _default
