"""
Ollama Resilience — backwards-compat alias for the generic layer
================================================================

Closes Glad-Labs/poindexter#192. The retry / queue / circuit-breaker
plumbing originally shipped here in #153 has been generalized to
``plugins/llm_resilience.py`` so every LLM provider can plug in.

This module keeps its public surface (``OllamaResilienceManager``,
``OllamaCircuitOpenError``, ``OllamaEmptyResponseError``,
``CircuitBreaker``, ``is_retryable``, ``compute_backoff``,
``get_default_manager``, ``reset_default_manager``) so existing
callers — ``services/ollama_client.py``, the ``/api/health`` endpoint,
and the existing 34-test suite under
``tests/unit/services/test_ollama_resilience.py`` — keep working
without changes during the transition.

What's new:

* The actual machinery lives in
  :mod:`plugins.llm_resilience` and is shared with the other LLM
  providers (OpenAI-compat, Anthropic, Gemini, future plugins).
* ``OllamaResilienceManager`` is now a thin wrapper that constructs a
  :class:`LLMResilienceManager` configured with ``provider_name="ollama"``
  and the Ollama-specific :func:`ollama_classifier`.
* Settings now read ``llm_ollama_<key>`` first and fall back to the
  legacy ``ollama_<key>`` keys for one release. Operators can rename
  on their own schedule.

Settings (DB-tunable via ``app_settings``):

* ``llm_ollama_retry_max_attempts`` (legacy: ``ollama_retry_max_attempts``)
* ``llm_ollama_retry_base_seconds`` (legacy: ``ollama_retry_base_seconds``)
* ``llm_ollama_retry_max_seconds`` (legacy: ``ollama_retry_max_seconds``)
* ``llm_ollama_retry_jitter_pct`` (legacy: ``ollama_retry_jitter_pct``)
* ``llm_ollama_max_concurrent_calls`` (legacy: ``ollama_max_concurrent_calls``)
* ``llm_ollama_circuit_breaker_failures`` (legacy: ``ollama_circuit_breaker_failures``)
* ``llm_ollama_circuit_breaker_window_s`` (legacy: ``ollama_circuit_breaker_window_s``)
* ``llm_ollama_circuit_breaker_cooldown_s`` (legacy: ``ollama_circuit_breaker_cooldown_s``)
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

from plugins import llm_resilience as _llm_resilience  # noqa: F401 — re-export anchor
from plugins.llm_resilience import (
    CircuitBreaker as _GenericCircuitBreaker,
    CircuitOpenError,
    LLMResilienceManager,
    RetryDecision,
    compute_backoff as _compute_backoff,
)
from services.audit_log import audit_log_bg as _audit_log_bg  # noqa: F401
from services.logger_config import get_logger

logger = get_logger(__name__)


# Patchability shim. The original module imported ``audit_log_bg``
# at the top level so tests could ``patch("services.ollama_resilience.audit_log_bg")``
# to capture events. After the generalization those events are emitted
# from ``plugins.llm_resilience.audit_log_bg``, so we re-export the
# symbol here AND wrap it so a patch at this module path is honored
# even though the actual emission happens in the generic layer.
#
# The wrap-and-rebind dance: ``plugins.llm_resilience`` calls
# ``audit_log_bg(...)`` via its own module-level binding; tests that
# patch ``services.ollama_resilience.audit_log_bg`` would otherwise
# catch nothing. ``_install_audit_proxy`` installs a forwarder in the
# generic module that always reads back through ``ollama_resilience.audit_log_bg``,
# so patching either location works. Idempotent — safe to import twice.
def audit_log_bg(*args: Any, **kwargs: Any) -> Any:
    return _audit_log_bg(*args, **kwargs)


def _install_audit_proxy() -> None:
    """Route ``plugins.llm_resilience.audit_log_bg`` calls through this module.

    Lets existing test fixtures that patch
    ``services.ollama_resilience.audit_log_bg`` keep working without
    forcing a coordinated test-file edit. The proxy reads
    ``audit_log_bg`` off this module each call — so a ``patch`` swap
    takes effect immediately.
    """
    import sys as _sys

    def _proxy(*args: Any, **kwargs: Any) -> Any:
        mod = _sys.modules.get(__name__)
        target = getattr(mod, "audit_log_bg", _audit_log_bg) if mod else _audit_log_bg
        return target(*args, **kwargs)

    _llm_resilience.audit_log_bg = _proxy  # type: ignore[attr-defined]


_install_audit_proxy()

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Domain exceptions — kept where they were so callers' imports keep working
# ---------------------------------------------------------------------------


class OllamaCircuitOpenError(CircuitOpenError):
    """Raised by the resilience layer when the Ollama breaker is open.

    Subclass of the generic :class:`CircuitOpenError` so existing
    callers that ``except OllamaCircuitOpenError`` keep catching the
    same condition. The fast-fail trip is distinguishable from an
    upstream Ollama error so the cross-model fallback chain can decide
    whether to retry locally, queue for later, or fall through to the
    cloud fallback immediately.
    """

    def __init__(
        self,
        message: str,
        *,
        opened_at: float,
        cooldown_seconds: float,
        consecutive_failures: int,
        provider: str = "ollama",
    ) -> None:
        super().__init__(
            message,
            provider=provider,
            opened_at=opened_at,
            cooldown_seconds=cooldown_seconds,
            consecutive_failures=consecutive_failures,
        )

    # ``seconds_until_recheck`` is inherited from CircuitOpenError.


class OllamaEmptyResponseError(Exception):
    """Raised when Ollama returns an HTTP 200 with no usable content.

    This happens under GPU contention with thinking models (qwen3,
    glm-4.7) — the thinking trace eats the entire ``num_predict``
    budget and ``message.content`` comes back empty. We retry these
    instead of accepting the stub response.
    """


# ---------------------------------------------------------------------------
# Ollama-specific CircuitBreaker — preserves OllamaCircuitOpenError type
# ---------------------------------------------------------------------------


class CircuitBreaker(_GenericCircuitBreaker):
    """Ollama-tagged circuit breaker.

    Pinned to ``provider="ollama"`` and overrides ``_open_error_cls``
    so calls that raise on an open breaker get the legacy
    ``OllamaCircuitOpenError`` type (not the generic
    :class:`CircuitOpenError`). Existing test code and the Ollama
    client both ``except`` on the legacy class.

    Accepts an optional ``provider`` kwarg so :class:`LLMResilienceManager`
    can construct it via the same ``circuit_breaker_cls(provider=...)``
    call signature it uses for the generic :class:`_GenericCircuitBreaker`.
    The kwarg is ignored when the caller doesn't override the default —
    Ollama always means ``provider="ollama"``.
    """

    _open_error_cls = OllamaCircuitOpenError

    def __init__(
        self, *, site_config: Any = None, provider: str = "ollama",
    ) -> None:
        super().__init__(provider=provider, site_config=site_config)


# ---------------------------------------------------------------------------
# Retry classification — Ollama-specific, returns RetryDecision
# ---------------------------------------------------------------------------


_RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def ollama_classifier(exc: BaseException) -> RetryDecision:
    """Classify an exception for the Ollama call path.

    Retry on:
        * Connection drops, read timeouts, pool timeouts (``httpx.*``).
        * Ollama empty-response under load (``OllamaEmptyResponseError``).
        * HTTP 5xx + 408/425/429 from the upstream.

    Do NOT retry on:
        * 4xx schema / auth errors (400, 401, 403, 404, 422, ...).
        * Programmer errors (TypeError, ValueError, KeyError, ...).
        * ``asyncio.CancelledError`` (let cancellation propagate).
        * ``OllamaCircuitOpenError`` — the breaker IS the backoff.

    Ollama doesn't expose a ``Retry-After`` header (the upstream is a
    local process, not a rate-limited cloud API), so every decision
    leaves ``wait_seconds=None`` and the manager uses its exponential
    schedule.
    """
    if isinstance(exc, asyncio.CancelledError):
        return RetryDecision(retry=False, reason="cancelled")
    if isinstance(exc, CircuitOpenError):
        return RetryDecision(retry=False, reason="circuit_open")
    if isinstance(exc, OllamaEmptyResponseError):
        return RetryDecision(retry=True, reason="empty_response")
    if isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.RemoteProtocolError,
            httpx.ConnectTimeout,
        ),
    ):
        return RetryDecision(retry=True, reason=type(exc).__name__)
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in _RETRYABLE_HTTP_STATUSES:
            return RetryDecision(retry=True, reason=f"http_{code}")
        return RetryDecision(retry=False, reason=f"http_{code}")
    if isinstance(exc, httpx.HTTPError):
        # Generic HTTPError with a .response attribute (raise_for_status path)
        response = getattr(exc, "response", None)
        if response is not None:
            code = getattr(response, "status_code", 0)
            if code in _RETRYABLE_HTTP_STATUSES:
                return RetryDecision(retry=True, reason=f"http_{code}")
            return RetryDecision(retry=False, reason=f"http_{code}")
        # No response object — likely transport-level, treat as retryable
        return RetryDecision(retry=True, reason=type(exc).__name__)
    return RetryDecision(retry=False, reason="non_retryable")


def is_retryable(exc: BaseException) -> bool:
    """Bool-flavored shim over :func:`ollama_classifier`.

    Kept around for the existing test suite + any direct callers in
    older Ollama callsites. New code should call ``ollama_classifier``
    directly so the ``RetryDecision`` shape stays visible.
    """
    return ollama_classifier(exc).retry


# Re-exported so tests that imported ``compute_backoff`` from this
# module keep working. Forwards directly to the generic implementation.
def compute_backoff(*args: Any, **kwargs: Any) -> float:
    return _compute_backoff(*args, **kwargs)


# ---------------------------------------------------------------------------
# Manager — backwards-compat factory subclass
# ---------------------------------------------------------------------------


class OllamaResilienceManager(LLMResilienceManager):
    """Ollama-flavored :class:`LLMResilienceManager`.

    Constructs a manager with:

    * ``provider_name="ollama"`` — drives the ``llm_ollama_*`` settings
      lookup with legacy ``ollama_*`` fallback (one-release transition
      window).
    * ``classifier=ollama_classifier`` — preserves every retryability
      decision the original module made.
    * ``circuit_breaker_cls=CircuitBreaker`` — Ollama-tagged subclass
      so a tripped breaker raises ``OllamaCircuitOpenError`` (kept
      where existing code expects it).

    The constructor signature matches the original — callers that pass
    only ``site_config=`` keep working without modification. The
    ``run`` method passes ``empty_result_exc=OllamaEmptyResponseError``
    by default so ``validate_result`` rejections still surface the
    typed exception code paths around ``OllamaClient`` already catch.
    """

    def __init__(self, *, site_config: Any = None) -> None:
        super().__init__(
            provider_name="ollama",
            classifier=ollama_classifier,
            site_config=site_config,
            circuit_breaker_cls=CircuitBreaker,
        )

    async def run(  # type: ignore[override]
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        op_name: str,
        validate_result: Callable[[Any], bool] | None = None,
        task_id: str | None = None,
        empty_result_exc: type[BaseException] | None = None,
    ) -> T:
        # Default to the Ollama-specific empty-response exception so
        # OllamaClient code paths catching it stay intact. Callers can
        # still override (used by tests).
        return await super().run(
            operation,
            op_name=op_name,
            validate_result=validate_result,
            task_id=task_id,
            empty_result_exc=empty_result_exc or OllamaEmptyResponseError,
        )


# ---------------------------------------------------------------------------
# Module-level default manager (used when callers don't bring their own)
# ---------------------------------------------------------------------------


_default_manager: OllamaResilienceManager | None = None


def get_default_manager(*, site_config: Any = None) -> OllamaResilienceManager:
    """Return a process-wide default Ollama resilience manager.

    ``OllamaClient`` constructs its own manager per-instance, but the
    health endpoint and other read-only consumers want a single
    snapshot — they grab the default manager so they can render the
    Ollama section of ``/api/health -> components.llm_resilience``
    without coupling to a specific client instance.
    """
    global _default_manager
    if _default_manager is None:
        _default_manager = OllamaResilienceManager(site_config=site_config)
    return _default_manager


def reset_default_manager() -> None:
    """Test-only: drop the cached default manager."""
    global _default_manager
    _default_manager = None


# Tracer kept around for legacy imports that may have grabbed it via
# ``services.ollama_resilience._tracer`` — no current callsites do, but
# the symbol existed in the previous module so we keep it tidy.
_tracer = None  # noqa: PLW0603 — preserved for import-compat; unused.
del _tracer


__all__ = [
    "CircuitBreaker",
    "OllamaCircuitOpenError",
    "OllamaEmptyResponseError",
    "OllamaResilienceManager",
    "compute_backoff",
    "get_default_manager",
    "is_retryable",
    "ollama_classifier",
    "reset_default_manager",
    # Re-export the generic types so callers can migrate gradually.
    "RetryDecision",
    "CircuitOpenError",
    "LLMResilienceManager",
]


# Reference time module to keep linters from flagging the unused import
# in case a future patch re-introduces a time-based helper here. Cheap.
_ = time
