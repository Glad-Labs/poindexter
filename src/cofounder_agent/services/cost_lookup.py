"""Per-token cost lookup, sourced from LiteLLM (#199 Phase 1).

Previously the codebase hand-maintained two parallel pricing tables —
``services/model_constants.py:MODEL_COSTS`` (10 Ollama-only entries
at $0) and ``services/usage_tracker.py:UsageTracker.MODEL_PRICING``
(3 entries). Both required manual edits whenever a model was added,
and neither covered cloud providers (Anthropic, OpenAI, Gemini,
Bedrock) — so any cost calculation for a cloud call fell back to a
hand-tuned default that was usually wrong by an order of magnitude.
Both files were deleted 2026-05-08.

LiteLLM ships ``litellm.model_cost``, a community-maintained dict
with 2,600+ model/provider entries pulled from the provider's
official pricing pages. This module wraps it with our existing units
(USD per 1K tokens) and adds the Ollama-prefix fallback so local
models reliably resolve to $0 even if LiteLLM doesn't have the
specific tag.

The remaining hand-rolled price table — ``cost_guard.py:74-94`` (14
entries) — should also delegate here; tracked as a follow-up.
"""

from __future__ import annotations

from services.logger_config import get_logger

logger = get_logger(__name__)


# Default per-1K-token cost when a model isn't in LiteLLM's table and
# isn't a local-Ollama route. Matches the pre-#199 fallback from
# ``services/model_constants.py:DEFAULT_MODEL_COST`` so callers that
# rely on this path see no behavioral change.
DEFAULT_COST_PER_1K = 0.005


def _is_local_route(model: str) -> bool:
    """Local providers cost zero — GPU electricity is tracked separately
    via the ``cost_logs`` 'electricity' provider rows, not as per-token
    inference cost."""
    if not model:
        return False
    lower = model.lower()
    return (
        lower.startswith("ollama/")
        or lower.startswith("ollama_native/")
        or "://localhost" in lower
        or lower.startswith("local/")
    )


def get_model_cost_per_1k(model: str) -> tuple[float, float]:
    """Return ``(input_cost_per_1k, output_cost_per_1k)`` in USD.

    Lookup order:
    1. LiteLLM's ``model_cost`` dict (2,600+ entries, community-maintained).
    2. Local-route fallback ($0 for Ollama / local providers).
    3. Module default (``DEFAULT_COST_PER_1K`` for both input and output).

    Never raises — a missing model logs a debug message and falls
    through to the default. Callers integrating cost tracking should
    treat the return value as advisory, not authoritative; the
    authoritative source is LiteLLM's ``completion_cost`` callback
    fired at request time, which sees the actual token counts.
    """
    if not model:
        return (DEFAULT_COST_PER_1K, DEFAULT_COST_PER_1K)

    try:
        import litellm  # local import keeps test fixtures simple
    except ImportError:
        # litellm not installed (e.g. minimal test env). Fall through.
        if _is_local_route(model):
            return (0.0, 0.0)
        return (DEFAULT_COST_PER_1K, DEFAULT_COST_PER_1K)

    table = getattr(litellm, "model_cost", None) or {}
    entry = table.get(model)
    if entry is None:
        # Try a stripped variant — LiteLLM keys some models without the
        # provider prefix, e.g. "claude-haiku-4-5" not "anthropic/claude-haiku-4-5".
        stripped = model.split("/", 1)[1] if "/" in model else model
        entry = table.get(stripped)

    if entry is not None:
        # LiteLLM stores per-token cost; multiply by 1000 to match our units.
        ipt = float(entry.get("input_cost_per_token", 0.0) or 0.0) * 1000.0
        opt = float(entry.get("output_cost_per_token", 0.0) or 0.0) * 1000.0
        return (ipt, opt)

    # Not in LiteLLM. Local routes are zero by policy.
    if _is_local_route(model):
        return (0.0, 0.0)

    logger.debug(
        "cost_lookup: model %r not in LiteLLM table, using default $%s/1K",
        model, DEFAULT_COST_PER_1K,
    )
    return (DEFAULT_COST_PER_1K, DEFAULT_COST_PER_1K)


def get_model_cost(model: str) -> float:
    """Backward-compat shim: return a single per-1K-token figure.

    Pre-#199 callers (``model_router.get_model_cost``,
    ``model_constants.MODEL_COSTS`` consumers) only saw a single number,
    not separate input/output pricing — they used it for rough
    estimation. We collapse to ``max(input, output)`` so the estimate
    is conservative (i.e. doesn't undercount when a request is
    output-heavy).
    """
    ipt, opt = get_model_cost_per_1k(model)
    return max(ipt, opt)


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Convenience: full per-call cost estimate.

    For monitoring + dashboards. The authoritative cost log is still
    written from ``litellm.completion_cost`` after the actual call.
    """
    ipt_per_1k, opt_per_1k = get_model_cost_per_1k(model)
    return (prompt_tokens / 1000.0) * ipt_per_1k + (completion_tokens / 1000.0) * opt_per_1k


__all__ = [
    "DEFAULT_COST_PER_1K",
    "estimate_cost",
    "get_model_cost",
    "get_model_cost_per_1k",
]
