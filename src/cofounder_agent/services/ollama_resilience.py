"""
Ollama Resilience Layer
=======================

Closes Glad-Labs/poindexter#153.

The bug:
    `services/content_router_service.py` was publishing stub content
    when Ollama returned degraded responses under GPU contention or
    VRAM pressure. Commit 3d1a6035 fixed the symptom (refused to
    publish empty/stub drafts), but did not address the root cause —
    bare ``await client.post(...)`` calls with no retry, no queue,
    no circuit breaker. Any transient overload caused immediate task
    failure with no graceful recovery.

This module is the recovery layer:

* **Retry helper** — ``retry_call()`` with exponential backoff and
  jitter. Retries only on transient errors (timeouts, 503, dropped
  connections, empty content from thinking-trace overflow). Auth /
  4xx schema errors fail fast.
* **Concurrency queue** — ``OllamaResilienceManager.semaphore`` caps
  the number of in-flight calls so a single slow generation does not
  starve the rest of the pipeline.
* **Circuit breaker** — N consecutive failures within a sliding
  window trip the breaker. While tripped, every call fails fast with
  ``OllamaCircuitOpenError`` and the state is surfaced in
  ``/api/health``. Cools down after a configurable interval.

Every retry, backoff, and circuit-state transition writes an
``audit_log_bg`` row and records a span attribute on the active
OTel span (``llm.retry_attempt``, ``llm.circuit_state``,
``llm.queue_wait_s``) so the resilience behaviour is observable in
both the audit log and Grafana/Tempo without any extra wiring at the
call site.

All knobs are DB-tunable via ``app_settings``:

* ``ollama_retry_max_attempts`` (default ``3``)
* ``ollama_retry_base_seconds`` (default ``1.0``)
* ``ollama_retry_max_seconds`` (default ``30.0``)
* ``ollama_retry_jitter_pct`` (default ``0.25``)
* ``ollama_max_concurrent_calls`` (default ``2``)
* ``ollama_circuit_breaker_failures`` (default ``5``)
* ``ollama_circuit_breaker_window_s`` (default ``60``)
* ``ollama_circuit_breaker_cooldown_s`` (default ``300``)

Design notes:

* Wraps, doesn't replace — ``OllamaClient`` keeps its existing public
  surface; the resilience layer is composed in via
  ``OllamaResilienceManager.run(coro_factory, ...)``.
* Nothing here is Ollama-specific in shape; the same pattern would
  work for any LLM provider. We name it after Ollama for clarity
  because that's the load-bearing path the bug surfaced on.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

from plugins.tracing import get_tracer
from services.audit_log import audit_log_bg
from services.logger_config import get_logger

logger = get_logger(__name__)

_tracer = get_tracer("poindexter.ollama_resilience")

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class OllamaCircuitOpenError(Exception):
    """Raised by the resilience layer when the breaker is open and the
    call is being short-circuited.

    Distinguishes a fast-fail trip from an upstream Ollama error so
    callers (and the cross-model fallback chain) can decide whether
    to retry locally, queue for later, or fall through to the cloud
    fallback immediately.
    """

    def __init__(
        self,
        message: str,
        *,
        opened_at: float,
        cooldown_seconds: float,
        consecutive_failures: int,
    ) -> None:
        super().__init__(message)
        self.opened_at = opened_at
        self.cooldown_seconds = cooldown_seconds
        self.consecutive_failures = consecutive_failures

    def seconds_until_recheck(self, now: float | None = None) -> float:
        """Seconds until the circuit transitions to half-open."""
        now = now if now is not None else time.monotonic()
        elapsed = now - self.opened_at
        return max(0.0, self.cooldown_seconds - elapsed)


class OllamaEmptyResponseError(Exception):
    """Raised when Ollama returns an HTTP 200 with no usable content.

    This happens under GPU contention with thinking models (qwen3,
    glm-4.7) — the thinking trace eats the entire ``num_predict``
    budget and ``message.content`` comes back empty. We retry these
    instead of accepting the stub response.
    """


# ---------------------------------------------------------------------------
# Settings helpers — all tunable via app_settings
# ---------------------------------------------------------------------------


def _sc_get(
    key: str,
    default: str = "",
    *,
    site_config: Any = None,
) -> str:
    """Read a setting from SiteConfig with env-var + default fallback.

    Mirrors the helper in ``services/ollama_client.py`` so the two
    modules read the same way without a hard import dependency.
    """
    if site_config is not None:
        return site_config.get(key, default)
    # Fall back to module-level binding from ollama_client if available
    try:
        from services import ollama_client as _oc

        sc = getattr(_oc, "_site_config", None)
        if sc is not None:
            return sc.get(key, default)
    except Exception:  # pragma: no cover — defensive only
        pass
    env_val = os.getenv(key.upper())
    if env_val:
        return env_val
    return default


def _coerce_int(value: str, default: int, *, key: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        logger.warning(
            "Invalid app_settings value for %s (%r), using default %s",
            key,
            value,
            default,
        )
        return default


def _coerce_float(value: str, default: float, *, key: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid app_settings value for %s (%r), using default %s",
            key,
            value,
            default,
        )
        return default


def get_retry_max_attempts(site_config: Any = None) -> int:
    return _coerce_int(
        _sc_get("ollama_retry_max_attempts", "3", site_config=site_config),
        3,
        key="ollama_retry_max_attempts",
    )


def get_retry_base_seconds(site_config: Any = None) -> float:
    return _coerce_float(
        _sc_get("ollama_retry_base_seconds", "1.0", site_config=site_config),
        1.0,
        key="ollama_retry_base_seconds",
    )


def get_retry_max_seconds(site_config: Any = None) -> float:
    return _coerce_float(
        _sc_get("ollama_retry_max_seconds", "30.0", site_config=site_config),
        30.0,
        key="ollama_retry_max_seconds",
    )


def get_retry_jitter_pct(site_config: Any = None) -> float:
    return _coerce_float(
        _sc_get("ollama_retry_jitter_pct", "0.25", site_config=site_config),
        0.25,
        key="ollama_retry_jitter_pct",
    )


def get_max_concurrent_calls(site_config: Any = None) -> int:
    return max(
        1,
        _coerce_int(
            _sc_get("ollama_max_concurrent_calls", "2", site_config=site_config),
            2,
            key="ollama_max_concurrent_calls",
        ),
    )


def get_circuit_failures(site_config: Any = None) -> int:
    return _coerce_int(
        _sc_get("ollama_circuit_breaker_failures", "5", site_config=site_config),
        5,
        key="ollama_circuit_breaker_failures",
    )


def get_circuit_window_s(site_config: Any = None) -> float:
    return _coerce_float(
        _sc_get("ollama_circuit_breaker_window_s", "60", site_config=site_config),
        60.0,
        key="ollama_circuit_breaker_window_s",
    )


def get_circuit_cooldown_s(site_config: Any = None) -> float:
    return _coerce_float(
        _sc_get("ollama_circuit_breaker_cooldown_s", "300", site_config=site_config),
        300.0,
        key="ollama_circuit_breaker_cooldown_s",
    )


# ---------------------------------------------------------------------------
# Retry classification
# ---------------------------------------------------------------------------


_RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def is_retryable(exc: BaseException) -> bool:
    """Return True if the exception represents a transient failure.

    Retry on:
        * Connection drops, read timeouts, pool timeouts (httpx.*)
        * Ollama empty-response under load (OllamaEmptyResponseError)
        * HTTP 5xx + 408/425/429 from the upstream

    Do NOT retry on:
        * 4xx schema / auth errors (400, 401, 403, 404, 422, ...)
        * Programmer errors (TypeError, ValueError, KeyError, ...)
        * asyncio.CancelledError (let cancellation propagate)
    """
    if isinstance(exc, asyncio.CancelledError):
        return False
    if isinstance(exc, OllamaCircuitOpenError):
        # Don't retry past an open breaker — the breaker IS the
        # backoff. The caller should fall through to cloud or queue.
        return False
    if isinstance(exc, OllamaEmptyResponseError):
        return True
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
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_HTTP_STATUSES
    # Generic httpx.HTTPError with a .response attribute (raise_for_status path)
    if isinstance(exc, httpx.HTTPError):
        response = getattr(exc, "response", None)
        if response is not None:
            return getattr(response, "status_code", 0) in _RETRYABLE_HTTP_STATUSES
        # No response object — likely transport-level, treat as retryable
        return True
    return False


def compute_backoff(
    attempt: int,
    *,
    base_seconds: float,
    max_seconds: float,
    jitter_pct: float,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff with proportional jitter.

    Args:
        attempt: 1-indexed attempt number (1 = first retry).
        base_seconds: Base delay (delay for attempt=1 ignoring jitter).
        max_seconds: Cap on the resulting delay.
        jitter_pct: Symmetric jitter as a fraction of the computed
            delay (0.25 = ±25%).
        rng: Optional Random instance for deterministic tests.

    Returns:
        Sleep seconds, always >= 0.
    """
    if attempt < 1:
        return 0.0
    raw = base_seconds * (2 ** (attempt - 1))
    capped = min(raw, max_seconds)
    if jitter_pct <= 0:
        return max(0.0, capped)
    r = rng if rng is not None else random
    delta = capped * jitter_pct
    jittered = capped + r.uniform(-delta, delta)
    return max(0.0, jittered)


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Sliding-window circuit breaker for the Ollama call path.

    State machine:

    * **closed** (default) — calls flow through.
    * **open** — calls fail fast with ``OllamaCircuitOpenError`` until
      ``cooldown_s`` has elapsed.
    * **half-open** — first call after cooldown is allowed through; on
      success the breaker re-closes, on failure it re-opens.

    Counts only failures within the configured rolling window
    (``window_s``). Stale failures fall off, so an old burst can't
    accidentally trip the breaker on a fresh blip.

    All knobs are read from ``app_settings`` per call to ``allow()``
    and ``record_*()``, so a live config change takes effect on the
    next invocation without a restart.
    """

    def __init__(self, *, site_config: Any = None) -> None:
        self._site_config = site_config
        self._failures: deque[float] = deque()
        self._opened_at: float | None = None
        self._half_open_in_flight: bool = False
        self._lock = asyncio.Lock()
        self._consecutive_failures: int = 0

    # -- introspection ------------------------------------------------------

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        cooldown = get_circuit_cooldown_s(self._site_config)
        if (time.monotonic() - self._opened_at) >= cooldown:
            return "half_open"
        return "open"

    def snapshot(self) -> dict[str, Any]:
        """Lightweight dict for ``/api/health``. Cheap to call."""
        cooldown = get_circuit_cooldown_s(self._site_config)
        opened_at = self._opened_at
        seconds_until_recheck = 0.0
        if opened_at is not None:
            seconds_until_recheck = max(
                0.0, cooldown - (time.monotonic() - opened_at)
            )
        return {
            "state": self.state,
            "consecutive_failures": self._consecutive_failures,
            "failures_in_window": len(self._failures),
            "window_seconds": get_circuit_window_s(self._site_config),
            "failure_threshold": get_circuit_failures(self._site_config),
            "cooldown_seconds": cooldown,
            "seconds_until_recheck": seconds_until_recheck,
        }

    # -- gating -------------------------------------------------------------

    async def allow(self) -> None:
        """Raise OllamaCircuitOpenError if the call is blocked.

        Call this BEFORE the wrapped operation. After cooldown the
        first allow() flips the state to half-open and admits the
        call; concurrent half-open admissions are serialized so only
        one probe is in-flight at a time.
        """
        async with self._lock:
            if self._opened_at is None:
                return  # closed, allow
            cooldown = get_circuit_cooldown_s(self._site_config)
            elapsed = time.monotonic() - self._opened_at
            if elapsed < cooldown:
                raise OllamaCircuitOpenError(
                    "Ollama circuit breaker open — failing fast",
                    opened_at=self._opened_at,
                    cooldown_seconds=cooldown,
                    consecutive_failures=self._consecutive_failures,
                )
            # Cooldown elapsed → admit one half-open probe
            if self._half_open_in_flight:
                raise OllamaCircuitOpenError(
                    "Ollama circuit breaker half-open — probe in flight",
                    opened_at=self._opened_at,
                    cooldown_seconds=cooldown,
                    consecutive_failures=self._consecutive_failures,
                )
            self._half_open_in_flight = True

    async def record_success(self) -> None:
        async with self._lock:
            previously_open = self._opened_at is not None
            self._failures.clear()
            self._opened_at = None
            self._half_open_in_flight = False
            self._consecutive_failures = 0
        if previously_open:
            logger.info("Ollama circuit breaker closed (recovery)")
            audit_log_bg(
                "ollama_circuit_closed",
                "ollama_resilience",
                {"reason": "successful_probe"},
                severity="info",
            )

    async def record_failure(self, *, exc: BaseException | None = None) -> None:
        async with self._lock:
            now = time.monotonic()
            self._consecutive_failures += 1
            self._failures.append(now)
            window = get_circuit_window_s(self._site_config)
            threshold = max(1, get_circuit_failures(self._site_config))
            # Drop failures outside the rolling window
            while self._failures and (now - self._failures[0]) > window:
                self._failures.popleft()
            # If a half-open probe failed, re-open by resetting the
            # opened_at timestamp — this restarts the cooldown clock.
            was_half_open_probe = (
                self._opened_at is not None and self._half_open_in_flight
            )
            self._half_open_in_flight = False
            if was_half_open_probe:
                self._opened_at = now
                should_open = False  # already open, just reset the clock
            else:
                should_open = (
                    self._opened_at is None and len(self._failures) >= threshold
                )
                if should_open:
                    self._opened_at = now
        # Outside the lock to keep audit fire-and-forget non-blocking
        if should_open:
            logger.warning(
                "Ollama circuit breaker tripped after %d failures in %.0fs window",
                threshold,
                get_circuit_window_s(self._site_config),
            )
            audit_log_bg(
                "ollama_circuit_opened",
                "ollama_resilience",
                {
                    "consecutive_failures": self._consecutive_failures,
                    "window_seconds": get_circuit_window_s(self._site_config),
                    "failure_threshold": threshold,
                    "exc_type": type(exc).__name__ if exc else None,
                    "exc_repr": repr(exc)[:200] if exc else None,
                },
                severity="warning",
            )

    # -- test hooks ---------------------------------------------------------

    def _force_open(self) -> None:
        """Test-only: trip the breaker without driving real failures."""
        self._opened_at = time.monotonic()
        self._consecutive_failures = max(
            self._consecutive_failures,
            get_circuit_failures(self._site_config),
        )

    def _reset(self) -> None:
        """Test-only: reset breaker to closed state."""
        self._failures.clear()
        self._opened_at = None
        self._half_open_in_flight = False
        self._consecutive_failures = 0


# ---------------------------------------------------------------------------
# Resilience manager
# ---------------------------------------------------------------------------


class OllamaResilienceManager:
    """Bundles the semaphore + circuit breaker + retry loop.

    One instance per ``OllamaClient``. Composed in via
    ``OllamaClient.__init__`` — the client's ``generate``, ``chat``,
    ``embed`` methods all call ``self._resilience.run(...)`` instead
    of executing the HTTP call directly.

    The manager is intentionally cheap to construct — semaphore and
    breaker are lazily resized on each call so a live
    ``app_settings`` change picks up without a client restart.
    """

    def __init__(self, *, site_config: Any = None) -> None:
        self._site_config = site_config
        self._semaphore_capacity = get_max_concurrent_calls(site_config)
        self._semaphore = asyncio.Semaphore(self._semaphore_capacity)
        self.circuit = CircuitBreaker(site_config=site_config)
        self._rng = random.Random()

    # -- introspection ------------------------------------------------------

    def health_snapshot(self) -> dict[str, Any]:
        """Status dict for ``/api/health -> components.ollama``."""
        snap = self.circuit.snapshot()
        snap["max_concurrent_calls"] = get_max_concurrent_calls(self._site_config)
        # Active in-flight = capacity - available permits. Best-effort:
        # asyncio.Semaphore exposes ._value which is the available
        # permit count. This is a private attribute but stable across
        # CPython versions; the alternative is wrapping the semaphore
        # ourselves which adds overhead to a hot path.
        try:
            available = self._semaphore._value  # type: ignore[attr-defined]
        except AttributeError:
            available = self._semaphore_capacity
        snap["in_flight_calls"] = max(0, self._semaphore_capacity - available)
        return snap

    # -- semaphore resize ---------------------------------------------------

    def _resize_semaphore_if_needed(self) -> None:
        """Pick up a live ``ollama_max_concurrent_calls`` change.

        asyncio.Semaphore has no public resize, so we replace it.
        Callers already in ``run()`` keep their permit on the old
        semaphore and release it on the old semaphore — that's safe
        because we still hold a reference to the new one and the old
        one is GC'd once all permits are released. New callers acquire
        on the new semaphore.
        """
        target = get_max_concurrent_calls(self._site_config)
        if target == self._semaphore_capacity:
            return
        logger.info(
            "Resizing Ollama concurrency semaphore: %d -> %d",
            self._semaphore_capacity,
            target,
        )
        self._semaphore = asyncio.Semaphore(target)
        self._semaphore_capacity = target

    # -- core run loop ------------------------------------------------------

    async def run(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        op_name: str,
        validate_result: Callable[[Any], bool] | None = None,
        task_id: str | None = None,
    ) -> T:
        """Run ``operation`` under retry + queue + circuit-breaker.

        Args:
            operation: Zero-arg async callable (factory) that performs
                the actual call. Called fresh for every attempt — must
                be safe to invoke multiple times.
            op_name: Short name for logs / audit (e.g. ``"generate"``,
                ``"chat"``, ``"embed"``).
            validate_result: Optional predicate. If supplied and
                returns False on a successful return value, the result
                is treated as ``OllamaEmptyResponseError`` and the
                attempt is retried. Used by ``generate`` / ``chat`` to
                catch the "200 OK but empty content" failure mode.
            task_id: Optional pipeline task ID for audit-log
                correlation.

        Raises:
            OllamaCircuitOpenError: If breaker is open.
            The last exception raised by ``operation()`` once retries
            are exhausted, or ``OllamaEmptyResponseError`` if
            ``validate_result`` rejected every attempt.
        """
        max_attempts = max(1, get_retry_max_attempts(self._site_config))
        base_s = get_retry_base_seconds(self._site_config)
        max_s = get_retry_max_seconds(self._site_config)
        jitter = get_retry_jitter_pct(self._site_config)

        self._resize_semaphore_if_needed()

        # Circuit breaker gate (before queueing — fail fast)
        await self.circuit.allow()

        from opentelemetry import trace as _trace_api  # cheap, plugin-installed

        try:
            current_span = _trace_api.get_current_span()
        except Exception:  # pragma: no cover — defensive only
            current_span = None

        last_exc: BaseException | None = None
        queue_wait_start = time.monotonic()

        async with self._semaphore:
            queue_wait_s = time.monotonic() - queue_wait_start
            if current_span is not None:
                try:
                    current_span.set_attribute("llm.queue_wait_s", queue_wait_s)
                    current_span.set_attribute(
                        "llm.circuit_state", self.circuit.state
                    )
                except Exception:  # pragma: no cover
                    pass

            for attempt in range(1, max_attempts + 1):
                try:
                    if current_span is not None:
                        try:
                            current_span.set_attribute("llm.retry_attempt", attempt)
                        except Exception:  # pragma: no cover
                            pass
                    result = await operation()
                    if validate_result is not None and not validate_result(result):
                        # Treat as empty-response failure — retry.
                        raise OllamaEmptyResponseError(
                            f"{op_name} returned empty/invalid result on attempt {attempt}"
                        )
                    if attempt > 1:
                        audit_log_bg(
                            "ollama_retry_success",
                            "ollama_resilience",
                            {
                                "op": op_name,
                                "attempt": attempt,
                                "max_attempts": max_attempts,
                            },
                            task_id=task_id,
                            severity="info",
                        )
                        logger.info(
                            "Ollama %s succeeded on attempt %d/%d after retry",
                            op_name,
                            attempt,
                            max_attempts,
                        )
                    await self.circuit.record_success()
                    return result

                except asyncio.CancelledError:
                    raise

                except BaseException as exc:  # noqa: BLE001 — classify below
                    last_exc = exc
                    retryable = is_retryable(exc)
                    is_last = attempt >= max_attempts
                    audit_log_bg(
                        "ollama_call_failed",
                        "ollama_resilience",
                        {
                            "op": op_name,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "retryable": retryable,
                            "exc_type": type(exc).__name__,
                            "exc_repr": repr(exc)[:200],
                        },
                        task_id=task_id,
                        severity="warning" if retryable and not is_last else "error",
                    )
                    if not retryable or is_last:
                        await self.circuit.record_failure(exc=exc)
                        if current_span is not None:
                            try:
                                current_span.set_attribute(
                                    "llm.circuit_state", self.circuit.state
                                )
                            except Exception:  # pragma: no cover
                                pass
                        raise
                    # Retryable + more attempts left
                    delay = compute_backoff(
                        attempt,
                        base_seconds=base_s,
                        max_seconds=max_s,
                        jitter_pct=jitter,
                        rng=self._rng,
                    )
                    logger.warning(
                        "Ollama %s attempt %d/%d failed (%s) — retrying in %.2fs",
                        op_name,
                        attempt,
                        max_attempts,
                        type(exc).__name__,
                        delay,
                    )
                    audit_log_bg(
                        "ollama_retry_backoff",
                        "ollama_resilience",
                        {
                            "op": op_name,
                            "attempt": attempt,
                            "delay_seconds": round(delay, 4),
                            "exc_type": type(exc).__name__,
                        },
                        task_id=task_id,
                        severity="info",
                    )
                    await asyncio.sleep(delay)

        # Defensive — loop should have raised or returned. If we ever
        # reach here, surface the last seen exception (or a generic
        # one) so the caller doesn't get None.
        raise last_exc if last_exc is not None else RuntimeError(
            f"Ollama {op_name} failed without a captured exception"
        )


# ---------------------------------------------------------------------------
# Module-level default manager (used when callers don't bring their own)
# ---------------------------------------------------------------------------


_default_manager: OllamaResilienceManager | None = None


def get_default_manager(*, site_config: Any = None) -> OllamaResilienceManager:
    """Return a process-wide default resilience manager.

    ``OllamaClient`` constructs its own manager per-instance, but the
    health endpoint and other read-only consumers want a single
    snapshot — they grab the default manager so they can render
    ``/api/health -> components.ollama_resilience`` without coupling
    to a specific client instance.
    """
    global _default_manager
    if _default_manager is None:
        _default_manager = OllamaResilienceManager(site_config=site_config)
    return _default_manager


def reset_default_manager() -> None:
    """Test-only: drop the cached default manager."""
    global _default_manager
    _default_manager = None
