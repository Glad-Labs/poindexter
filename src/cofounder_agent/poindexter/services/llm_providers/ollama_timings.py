"""Recover Ollama's decode/prefill timing split through LiteLLM.

Ollama's response JSON reports ``eval_duration`` (ns spent decoding) and
``prompt_eval_duration`` (ns spent on prompt/prefill), but LiteLLM's Ollama
transformations map only the token *counts* into ``Usage`` and drop the
durations — so ``cost_logs.duration_ms`` (wall clock) was the only timing we
had, and the Model Throughput surfaces could show *effective* tok/s but never
true decode speed.

``install_ollama_timing_capture()`` wraps ``transform_response`` on both
LiteLLM Ollama config classes (``ollama/`` → ``OllamaConfig``,
``ollama_chat/`` → ``OllamaChatConfig``) to parse the raw response and stash

    model_response._hidden_params["ollama_timings"] = {
        "decode_ms": <eval_duration / 1e6>,
        "prefill_ms": <prompt_eval_duration / 1e6>,
    }

``_hidden_params`` rides the response object back to the caller (verified
live on litellm 1.89.2, both dispatch spellings), so the dispatcher's
cost-write can persist the split with **no callback correlation and no
race** — the timing arrives on the same object as the tokens it describes.

Failure posture: this is passive observability capture, so every layer is
fail-open — if LiteLLM's internals move (import fails, signature changes,
raw body isn't the expected JSON), the wrapper logs once and LLM calls
proceed untouched; the ``cost_logs`` columns just stay NULL ("not
reported"), never 0. ``tests/unit/services/llm_providers/
test_ollama_timings.py`` pins the wrapped signature against the installed
litellm so an upgrade that breaks the seam fails CI loudly instead of
silently going dark.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

logger = logging.getLogger(__name__)

HIDDEN_PARAMS_KEY = "ollama_timings"

_installed = False


def _stash_timings(model_response: Any, raw_response: Any) -> None:
    """Best-effort: parse the raw Ollama JSON and attach the timing split."""
    try:
        body = raw_response.json()
        eval_ns = body.get("eval_duration")
        if eval_ns is None:
            return  # streaming shells / error bodies — nothing to record
        prefill_ns = body.get("prompt_eval_duration") or 0
        hidden = getattr(model_response, "_hidden_params", None)
        if not isinstance(hidden, dict):
            return
        hidden[HIDDEN_PARAMS_KEY] = {
            "decode_ms": float(eval_ns) / 1e6,
            "prefill_ms": float(prefill_ns) / 1e6,
        }
    except Exception:  # silent-ok: passive observability capture inside the LLM call path — a parse failure must never break the completion; the miss is visible as NULL decode columns and the litellm signature-pin test guards the systematic case
        logger.debug("[ollama_timings] raw-response parse failed", exc_info=True)


def _wrap(cls: type) -> None:
    orig = cls.transform_response

    @functools.wraps(orig)
    def patched(self, model, raw_response, model_response, *args, **kwargs):
        out = orig(self, model, raw_response, model_response, *args, **kwargs)
        _stash_timings(out, raw_response)
        return out

    patched.__ollama_timings_wrapped__ = True  # idempotency marker
    cls.transform_response = patched


def install_ollama_timing_capture() -> bool:
    """Idempotently patch both Ollama config classes. Returns install state."""
    global _installed
    if _installed:
        return True
    try:
        from litellm.llms.ollama.chat.transformation import OllamaChatConfig
        from litellm.llms.ollama.completion.transformation import OllamaConfig
    except Exception:
        logger.warning(
            "[ollama_timings] litellm ollama transformations not importable — "
            "decode/prefill capture disabled (cost_logs columns stay NULL)",
            exc_info=True,
        )
        return False
    for cls in (OllamaChatConfig, OllamaConfig):
        if getattr(cls.transform_response, "__ollama_timings_wrapped__", False):
            continue
        _wrap(cls)
    _installed = True
    logger.info("[ollama_timings] decode/prefill timing capture installed")
    return True


def extract_timings(response: Any) -> tuple[int | None, int | None]:
    """(decode_duration_ms, prefill_duration_ms) from a LiteLLM response.

    ``(None, None)`` when the provider didn't report a split (cloud models,
    seam not installed, litellm drift) — persisted as NULL, never 0.
    """
    try:
        hidden = getattr(response, "_hidden_params", None)
        timings = hidden.get(HIDDEN_PARAMS_KEY) if isinstance(hidden, dict) else None
        if not isinstance(timings, dict):
            return None, None
        decode = timings.get("decode_ms")
        prefill = timings.get("prefill_ms")
        return (
            int(decode) if decode is not None else None,
            int(prefill) if prefill is not None else None,
        )
    except Exception:  # silent-ok: reading an optional analytics stash off a response object — (None, None) IS the honest "not reported" value and the cost write proceeds; systematic breakage surfaces via the signature-pin test, not per-call noise
        logger.debug("[ollama_timings] extract failed", exc_info=True)
        return None, None
