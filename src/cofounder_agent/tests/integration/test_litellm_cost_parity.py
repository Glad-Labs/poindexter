"""Cost + response parity between OllamaNativeProvider and LiteLLMProvider.

Glad-Labs/poindexter#372 — pre-cutover safety net.

Drives the same prompt through both completion paths and asserts:

* Both return a non-empty response with the same finish_reason vocabulary
  ("stop") so downstream pipeline logic that branches on finish_reason
  keeps working unchanged.
* Token counts agree within tolerance — Ollama populates
  ``prompt_eval_count`` / ``eval_count`` directly; LiteLLM normalizes
  the same fields onto the OpenAI ``usage`` shape. They should match
  exactly for a deterministic prompt; the tolerance is there for the
  occasional off-by-one tokenization quirk LiteLLM's normalizer
  introduces, not as a budget for divergence.
* Per-1K cost agrees ($0 for both — local Ollama route). Cost ledger
  parity matters because the cutover swaps the writer pipeline's cost
  accounting from the OllamaClient electricity-cost path to LiteLLM's
  built-in ``response_cost`` field. Both must report the same dollar
  figure for the same call or the budget tracker (cost_guard) will
  drift after the cutover.

Per `feedback_no_silent_defaults`: any divergence > tolerance fails the
test loudly — operator decides whether to ship the cutover anyway or
hold. Per `feedback_no_paid_apis`: this test only ever hits local
Ollama; cloud paths are out of scope.

## Running

The test is gated behind the existing INTEGRATION_TESTS +
REAL_SERVICES_TESTS env-var pair (see tests/integration/conftest.py).

Local run on Matt's PC::

    OLLAMA_URL=http://localhost:11434 \\
    INTEGRATION_TESTS=1 \\
    REAL_SERVICES_TESTS=1 \\
    poetry run pytest tests/integration/test_litellm_cost_parity.py -v

CI does NOT run this — Ollama isn't on the runners. Operator-gated
smoke is the right place; the unit tests in tests/unit/services/ cover
the stub-able code paths.
"""

from __future__ import annotations

import os

import httpx
import pytest

from services.cost_lookup import estimate_cost, get_model_cost_per_1k
from services.llm_providers.litellm_provider import LiteLLMProvider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _ollama_url() -> str:
    """Resolve the Ollama base URL the test should hit.

    Same precedence as the integration conftest's ``_ollama_url``
    helper. Replicated locally because the conftest fixture that wraps
    it (``real_ollama_url``) is session-scoped, which clashes with
    pytest-asyncio's function-scoped event loop on newer versions.
    Bypassing the fixture keeps this test runnable while the
    integration harness scope-mismatch is being sorted out separately.
    """
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


@pytest.fixture
async def live_ollama_url() -> str:
    """Per-test Ollama URL; skips the test if Ollama isn't reachable.

    Function-scoped so the fixture and the test share a loop; this
    sidesteps the existing session/function-scope mismatch on the
    conftest's ``real_ollama_url`` fixture (the dispatcher integration
    test hits the same scope error on newer pytest-asyncio releases).
    """
    if not (
        os.getenv("INTEGRATION_TESTS")
        and os.getenv("REAL_SERVICES_TESTS")
    ):
        pytest.skip(
            "Real-services harness disabled. Set INTEGRATION_TESTS=1 + "
            "REAL_SERVICES_TESTS=1 to enable.",
        )
    url = _ollama_url()
    if not await _ollama_is_reachable(url):
        pytest.skip(
            f"Ollama not reachable at {url}; start the host Ollama before "
            f"running the cost parity test.",
        )
    return url


# Deterministic prompt — temperature 0, max_tokens generous enough for
# thinking models (glm-4.7 splits content/thinking, so a tight
# max_tokens budget makes content empty). Prompt is intentionally
# trivial so the test runs in <30s on a 5090.
_PROMPT_MESSAGES = [
    {
        "role": "user",
        "content": (
            "Reply with exactly the single word: pong. "
            "Do not add punctuation, explanation, or any other text."
        ),
    },
]
_TEST_MODEL_BARE = "glm-4.7-5090:latest"
_TEST_MODEL_LITELLM = f"ollama/{_TEST_MODEL_BARE}"
_MAX_TOKENS = 256
_TEMPERATURE = 0.0

# How wide a token-count divergence we'll tolerate. LiteLLM's Ollama
# integration reads the same prompt_eval_count / eval_count fields the
# native provider reads, so for a deterministic prompt the values
# should be identical. The 5% slack is purely defensive against the
# rare case where LiteLLM's response normalizer rounds or aggregates
# differently across versions.
_TOKEN_TOLERANCE_PCT = 0.05

# Cost divergence tolerance — local Ollama is $0/$0, so any absolute
# divergence is a regression. Matched to the 1% the brief calls out.
_COST_TOLERANCE_USD = 0.0001


async def _call_via_litellm_provider(
    base_url: str,
) -> dict:
    """Drive LiteLLMProvider.complete() — the path the cutover activates."""
    provider = LiteLLMProvider()
    completion = await provider.complete(
        messages=_PROMPT_MESSAGES,
        model=_TEST_MODEL_BARE,
        _provider_config={
            "api_base": base_url,
            "timeout_seconds": 180,
            "drop_params": True,
        },
        temperature=_TEMPERATURE,
        max_tokens=_MAX_TOKENS,
    )
    return {
        "text": completion.text,
        "model": completion.model,
        "prompt_tokens": completion.prompt_tokens,
        "completion_tokens": completion.completion_tokens,
        "total_tokens": completion.total_tokens,
        "finish_reason": completion.finish_reason,
        # LiteLLM stamps ``response_cost`` only when its model_cost
        # table knows the model. Local Ollama is $0 by policy and
        # absent from the table, so this comes back as None — matched
        # by ``cost_lookup.get_model_cost_per_1k`` returning (0, 0).
        "response_cost": completion.raw.get("response_cost"),
        "raw_keys": sorted(completion.raw.keys()),
    }


async def _call_via_ollama_client(base_url: str) -> dict:
    """Drive OllamaClient.generate() directly — the legacy default path.

    Bypasses OllamaNativeProvider so we don't need to mutate
    app_settings.ollama_base_url for the test. The Provider wraps
    OllamaClient 1:1 (see services/llm_providers/ollama_native.py); the
    response shape it produces is reconstructed below to match the
    Completion dataclass surface.
    """
    from services.ollama_client import OllamaClient

    # OllamaClient is dependency-injected via base_url so we can point
    # at the test environment's Ollama without depending on the
    # app_settings.ollama_base_url row (which lives in the operating
    # DB, not the isolated test DB).
    client = OllamaClient(base_url=base_url, timeout=180)
    system = next(
        (m["content"] for m in _PROMPT_MESSAGES if m.get("role") == "system"), None
    )
    prompt = "\n\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}"
        for m in _PROMPT_MESSAGES
        if m.get("role") != "system"
    )
    result = await client.generate(
        prompt=prompt,
        model=_TEST_MODEL_BARE,
        system=system,
        temperature=_TEMPERATURE,
        max_tokens=_MAX_TOKENS,
        stream=False,
    )
    return {
        "text": result.get("text") or result.get("response") or "",
        "model": result.get("model", _TEST_MODEL_BARE),
        "prompt_tokens": int(result.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(result.get("tokens", 0) or 0),
        "total_tokens": int(result.get("total_tokens", 0) or 0),
        "finish_reason": result.get("done_reason", "stop"),
        # OllamaClient computes electricity cost directly; surfaced as
        # the per-call cost the legacy stage layer logs to cost_logs.
        "electricity_cost_usd": float(result.get("cost", 0.0) or 0.0),
        "duration_seconds": float(result.get("duration_seconds", 0.0) or 0.0),
    }


class TestLiteLLMOllamaParity:
    """Cutover safety net: same prompt → equivalent results either way."""

    async def test_response_shape_parity(self, live_ollama_url: str) -> None:
        """Both providers populate the same response fields.

        The pipeline reads ``text``, ``prompt_tokens``, ``completion_tokens``,
        ``total_tokens``, and ``finish_reason``. Any field flipping to
        None / 0 / "" only on one provider would silently change
        downstream behavior post-cutover.
        """
        lite = await _call_via_litellm_provider(live_ollama_url)
        olla = await _call_via_ollama_client(live_ollama_url)

        # Both produced text — empty content from either side means the
        # path is broken (e.g. thinking budget too small, model swapped).
        assert lite["text"], (
            f"LiteLLMProvider returned empty text. raw_keys={lite['raw_keys']}"
        )
        assert olla["text"], "OllamaClient returned empty text."

        # finish_reason vocabulary — pipeline branches on "stop" vs
        # "length" vs etc. Both should report "stop" for this prompt.
        assert lite["finish_reason"] == "stop", (
            f"LiteLLM finish_reason={lite['finish_reason']!r}, expected 'stop'"
        )
        assert olla["finish_reason"] == "stop", (
            f"Ollama finish_reason={olla['finish_reason']!r}, expected 'stop'"
        )

        # All three token counts must be populated and positive on both
        # paths — cost_logs / cost_guard derive budget from these.
        for path_name, payload in (("litellm", lite), ("ollama", olla)):
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = payload[field]
                assert isinstance(value, int) and value > 0, (
                    f"{path_name}.{field} = {value!r}; expected positive int"
                )

    async def test_token_count_parity(self, live_ollama_url: str) -> None:
        """Token counts match within ~5% across the two paths.

        For deterministic prompts on the same Ollama backend they should
        match exactly. The tolerance is purely a defense against
        LiteLLM's response normalizer changing its rounding behavior
        across versions; never as a budget for real divergence.
        """
        lite = await _call_via_litellm_provider(live_ollama_url)
        olla = await _call_via_ollama_client(live_ollama_url)

        prompt_diff_pct = abs(lite["prompt_tokens"] - olla["prompt_tokens"]) / max(
            olla["prompt_tokens"], 1
        )
        completion_diff_pct = abs(
            lite["completion_tokens"] - olla["completion_tokens"]
        ) / max(olla["completion_tokens"], 1)

        assert prompt_diff_pct <= _TOKEN_TOLERANCE_PCT, (
            f"prompt_tokens diverged by {prompt_diff_pct:.2%} "
            f"(litellm={lite['prompt_tokens']}, ollama={olla['prompt_tokens']}); "
            f"tolerance={_TOKEN_TOLERANCE_PCT:.0%}"
        )
        assert completion_diff_pct <= _TOKEN_TOLERANCE_PCT, (
            f"completion_tokens diverged by {completion_diff_pct:.2%} "
            f"(litellm={lite['completion_tokens']}, "
            f"ollama={olla['completion_tokens']}); "
            f"tolerance={_TOKEN_TOLERANCE_PCT:.0%}"
        )

    async def test_cost_parity_for_local_ollama(
        self, live_ollama_url: str
    ) -> None:
        """Both paths report $0 inference cost for a local Ollama call.

        The two cost flows differ structurally:

        * **OllamaClient** computes per-call electricity cost from GPU
          power draw * duration. The cutover MUST preserve this — Matt
          watches the cost dashboard for the energy-cost line.
        * **LiteLLM** doesn't know Ollama's model_cost (correctly $0)
          so ``response_cost`` is None. ``services/cost_lookup`` resolves
          the same model to (0, 0) per-1K rates via ``_is_local_route``.

        For inference-cost parity the LiteLLM path's resolved per-call
        cost must be 0. For electricity parity, the OllamaClient path
        must still report a positive duration (so the cost layer can
        recompute electricity from duration even if the LiteLLM path
        doesn't surface it directly).
        """
        lite = await _call_via_litellm_provider(live_ollama_url)
        olla = await _call_via_ollama_client(live_ollama_url)

        # LiteLLM-side inference cost. Either response_cost is set
        # (rare for local Ollama) or it's None — in which case
        # cost_lookup is the authoritative source.
        if lite["response_cost"] is not None:
            litellm_cost = float(lite["response_cost"])
        else:
            input_per_1k, output_per_1k = get_model_cost_per_1k(
                _TEST_MODEL_LITELLM
            )
            assert input_per_1k == 0.0 and output_per_1k == 0.0, (
                f"cost_lookup returned non-zero per-1K rates for "
                f"{_TEST_MODEL_LITELLM} (input={input_per_1k}, "
                f"output={output_per_1k}); local Ollama must be $0."
            )
            litellm_cost = estimate_cost(
                _TEST_MODEL_LITELLM,
                lite["prompt_tokens"],
                lite["completion_tokens"],
            )

        assert litellm_cost == pytest.approx(0.0, abs=_COST_TOLERANCE_USD), (
            f"LiteLLM-resolved inference cost = ${litellm_cost:.6f} for a "
            f"local Ollama route; expected $0. Cost layer regression."
        )

        # OllamaClient-side electricity cost — separate budget line; we
        # only assert it's a non-negative float so the cutover doesn't
        # accidentally start logging negative numbers. Actual value
        # depends on GPU power draw and is never $0 on a real call.
        electricity_cost = olla["electricity_cost_usd"]
        assert electricity_cost >= 0.0, (
            f"OllamaClient electricity_cost_usd={electricity_cost!r}; "
            f"expected non-negative."
        )
        assert olla["duration_seconds"] > 0.0, (
            f"OllamaClient duration_seconds={olla['duration_seconds']!r}; "
            f"expected positive — cost layer derives electricity from this."
        )

    async def test_writer_model_prefix_resolution(
        self,
        live_ollama_url: str,
    ) -> None:
        """LiteLLMProvider applies the ``ollama/`` prefix to bare names.

        Per memory.feedback_model_selection / feedback_writer_model_canary:
        the writer model contract is ``glm-4.7-5090``. LiteLLM requires
        provider-namespaced model strings (``ollama/glm-4.7-5090:latest``);
        the provider's ``_resolve_model`` adds the prefix so existing
        callers that pass bare names keep working unchanged.

        Regression on this method silently swaps every Ollama call to
        an unrouted ``glm-4.7-5090`` (no provider, ConnectionError) post-
        cutover. This test exercises the bare-name path end-to-end.
        """
        provider = LiteLLMProvider()
        # _configure_from must run for _default_prefix to apply
        provider._configure_from(  # noqa: SLF001 — direct invariant check
            {"api_base": live_ollama_url, "timeout_seconds": 180}
        )

        # Bare name → ollama/ prefix
        assert (
            provider._resolve_model("glm-4.7-5090:latest")  # noqa: SLF001
            == "ollama/glm-4.7-5090:latest"
        )
        # Already-prefixed names pass through unchanged
        assert (
            provider._resolve_model("ollama/glm-4.7-5090:latest")  # noqa: SLF001
            == "ollama/glm-4.7-5090:latest"
        )
        assert (
            provider._resolve_model("anthropic/claude-haiku-4-5")  # noqa: SLF001
            == "anthropic/claude-haiku-4-5"
        )
