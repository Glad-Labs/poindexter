"""
Generic LLM Resilience Layer
============================

Closes Glad-Labs/poindexter#192. Generalizes the Ollama-specific layer
shipped in #153 (``services/ollama_resilience.py``) so every LLM
provider — ``OllamaNativeProvider``, ``OpenAICompatProvider``,
``AnthropicProvider``, ``GeminiProvider``, plus future plugins — can
share the same retry / concurrency / circuit-breaker plumbing.

Architecture
------------

What's universal across providers:

* **Retry helper** — exponential backoff with jitter, configurable
  max-attempts + max-window.
* **Concurrency limit** — ``asyncio.Semaphore`` so a slow generation
  can't starve the rest of the pipeline.
* **Circuit breaker** — sliding-window failure count → trip → cooldown
  → half-open state machine. Counts only failures inside the window.
* **Audit log + OTel tracing** — every retry / trip / recovery writes
  an ``audit_log_bg`` row and records span attributes
  (``llm.retry_attempt``, ``llm.circuit_state``, ``llm.queue_wait_s``).

What's provider-specific (pluggable via :class:`Classifier`):

* **Error classifier** — ``Callable[[Exception], RetryDecision]``.
  Anthropic 429s ship a ``Retry-After`` header (use ``wait_seconds``
  override); Ollama's "200 OK with empty content under thinking-trace
  overflow" is local-only; Gemini quota errors have a different shape;
  OpenAI-compat varies by backend.
* **Backoff override** — when the classifier returns
  ``RetryDecision(retry=True, wait_seconds=N)``, the manager honors
  ``N`` instead of computing the exponential schedule. Lets
  Anthropic's ``Retry-After`` flow through cleanly.

Settings
--------

All knobs are DB-tunable via ``app_settings``:

* ``llm_<provider>_retry_max_attempts`` (default ``3``)
* ``llm_<provider>_retry_base_seconds`` (default ``1.0``)
* ``llm_<provider>_retry_max_seconds`` (default ``30.0``)
* ``llm_<provider>_retry_jitter_pct`` (default ``0.25``)
* ``llm_<provider>_max_concurrent_calls`` (default ``2`` for local,
  ``8`` recommended for cloud)
* ``llm_<provider>_circuit_breaker_failures`` (default ``5``)
* ``llm_<provider>_circuit_breaker_window_s`` (default ``60``)
* ``llm_<provider>_circuit_breaker_cooldown_s`` (default ``300``)

Backwards compat: when ``provider_name="ollama"`` and the new
``llm_ollama_*`` key is unset, the manager falls back to the legacy
``ollama_*`` key. One-release transition; the alias module
``services/ollama_resilience.py`` documents the timeline.

Health registry
---------------

Each provider's manager registers itself with the module-level
``ResilienceRegistry`` on construction. ``main.py``'s ``/api/health``
walks the registry and emits per-provider snapshots under
``components.llm_resilience.<provider>``. Process-local — no DB —
because the registry only describes which managers live in this worker.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from plugins.tracing import get_tracer
from services.audit_log import audit_log_bg
from services.logger_config import get_logger

logger = get_logger(__name__)

_tracer = get_tracer("poindexter.llm_resilience")

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Retry contract — RetryDecision + Classifier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryDecision:
    """Per-exception classifier verdict.

    Attributes:
        retry: Whether to retry at all.
        wait_seconds: Explicit override for the retry delay. When ``None``
            the manager computes the delay via exponential backoff with
            jitter from the per-provider settings. Anthropic returns this
            from ``Retry-After`` headers; OpenAI providers may also use it
            when the SDK exposes a recommended retry interval.
        reason: Short tag for audit + logs (e.g. ``"rate_limit"``,
            ``"http_503"``, ``"empty_response"``). Stays out of the
            user-visible exception message.
    """

    retry: bool
    wait_seconds: float | None = None
    reason: str = ""


# Type alias used in classifier signatures. We keep it as a plain
# Callable rather than a runtime Protocol because the call sites already
# use first-class functions / lambdas — a Protocol would add type-check
# noise without runtime benefit.
Classifier = Callable[[BaseException], RetryDecision]


# ---------------------------------------------------------------------------
# Settings helpers — per-provider, DB-tunable, env fallback
# ---------------------------------------------------------------------------


def _sc_get(
    key: str,
    default: str = "",
    *,
    site_config: Any = None,
) -> str:
    """Read a setting from SiteConfig with env-var + default fallback.

    Mirrors the helper that the legacy Ollama resilience module exposed
    so existing callers don't see a behavioral shift after the rename.
    """
    if site_config is not None:
        return site_config.get(key, default)
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


# Per-setting defaults — kept in one place so tests, docs and the
# manager all read from the same source of truth.
_DEFAULTS: dict[str, str | int | float] = {
    "retry_max_attempts": 3,
    "retry_base_seconds": 1.0,
    "retry_max_seconds": 30.0,
    "retry_jitter_pct": 0.25,
    "max_concurrent_calls": 2,
    "circuit_breaker_failures": 5,
    "circuit_breaker_window_s": 60.0,
    "circuit_breaker_cooldown_s": 300.0,
}


class _SettingsReader:
    """Per-provider settings reader with backwards-compat fallback.

    Reads ``llm_<provider>_<key>`` first. When the value is unset AND the
    provider is ``ollama`` (the only provider whose settings predated
    this rename), falls back to ``ollama_<key>``. The fallback is
    explicit and scoped — no other provider name picks up the legacy
    namespace.

    Documenting this for future code archaeology: the rename happened in
    GH#192. The plan is one full release with both keys honored, then
    the legacy fallback drops in a follow-up. Search ``ollama_retry_``
    / ``ollama_circuit_`` etc. when removing.
    """

    def __init__(self, provider_name: str, *, site_config: Any = None) -> None:
        self.provider_name = provider_name
        self._site_config = site_config

    def _read(self, key: str, default: str) -> str:
        new_key = f"llm_{self.provider_name}_{key}"
        new_val = _sc_get(new_key, "", site_config=self._site_config)
        if new_val:
            return new_val
        if self.provider_name == "ollama":
            legacy_key = f"ollama_{key}"
            legacy_val = _sc_get(
                legacy_key, default, site_config=self._site_config,
            )
            return legacy_val
        return default

    def retry_max_attempts(self) -> int:
        key = "retry_max_attempts"
        return _coerce_int(
            self._read(key, str(_DEFAULTS[key])),
            int(_DEFAULTS[key]),
            key=f"llm_{self.provider_name}_{key}",
        )

    def retry_base_seconds(self) -> float:
        key = "retry_base_seconds"
        return _coerce_float(
            self._read(key, str(_DEFAULTS[key])),
            float(_DEFAULTS[key]),
            key=f"llm_{self.provider_name}_{key}",
        )

    def retry_max_seconds(self) -> float:
        key = "retry_max_seconds"
        return _coerce_float(
            self._read(key, str(_DEFAULTS[key])),
            float(_DEFAULTS[key]),
            key=f"llm_{self.provider_name}_{key}",
        )

    def retry_jitter_pct(self) -> float:
        key = "retry_jitter_pct"
        return _coerce_float(
            self._read(key, str(_DEFAULTS[key])),
            float(_DEFAULTS[key]),
            key=f"llm_{self.provider_name}_{key}",
        )

    def max_concurrent_calls(self) -> int:
        key = "max_concurrent_calls"
        return max(
            1,
            _coerce_int(
                self._read(key, str(_DEFAULTS[key])),
                int(_DEFAULTS[key]),
                key=f"llm_{self.provider_name}_{key}",
            ),
        )

    def circuit_failures(self) -> int:
        key = "circuit_breaker_failures"
        return _coerce_int(
            self._read(key, str(_DEFAULTS[key])),
            int(_DEFAULTS[key]),
            key=f"llm_{self.provider_name}_{key}",
        )

    def circuit_window_s(self) -> float:
        key = "circuit_breaker_window_s"
        return _coerce_float(
            self._read(key, str(_DEFAULTS[key])),
            float(_DEFAULTS[key]),
            key=f"llm_{self.provider_name}_{key}",
        )

    def circuit_cooldown_s(self) -> float:
        key = "circuit_breaker_cooldown_s"
        return _coerce_float(
            self._read(key, str(_DEFAULTS[key])),
            float(_DEFAULTS[key]),
            key=f"llm_{self.provider_name}_{key}",
        )


# ---------------------------------------------------------------------------
# Backoff calculation
# ---------------------------------------------------------------------------


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
# Circuit-open exception
# ---------------------------------------------------------------------------


class CircuitOpenError(Exception):
    """Raised when the resilience layer fails fast due to an open breaker.

    Carries the provider name so cross-provider fallback chains can
    distinguish "this provider's breaker is tripped" from "the upstream
    actually rejected the call". Subclasses (e.g.
    ``OllamaCircuitOpenError``) preserve the per-provider exception
    types existing code expects.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        opened_at: float,
        cooldown_seconds: float,
        consecutive_failures: int,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.opened_at = opened_at
        self.cooldown_seconds = cooldown_seconds
        self.consecutive_failures = consecutive_failures

    def seconds_until_recheck(self, now: float | None = None) -> float:
        """Seconds until the circuit transitions to half-open."""
        now = now if now is not None else time.monotonic()
        elapsed = now - self.opened_at
        return max(0.0, self.cooldown_seconds - elapsed)


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Sliding-window circuit breaker — generic across providers.

    State machine:

    * **closed** (default) — calls flow through.
    * **open** — calls fail fast with :class:`CircuitOpenError` until
      ``cooldown_s`` has elapsed.
    * **half-open** — first call after cooldown is allowed through; on
      success the breaker re-closes, on failure it re-opens.

    Counts only failures within the configured rolling window. Stale
    failures fall off, so an old burst can't accidentally trip the
    breaker on a fresh blip.

    Knobs are read from ``app_settings`` per call so a live config
    change takes effect on the next invocation without a worker restart.

    The breaker is **provider-agnostic** — it takes a ``provider`` tag
    so audit-log entries and the raised exception identify which
    provider's circuit is open. The legacy Ollama subclass keeps the
    old exception type alive for callers that ``except`` on it.
    """

    # Subclasses (OllamaCircuitOpenError) override this so the public
    # alias module can keep its existing exception class.
    _open_error_cls: type[CircuitOpenError] = CircuitOpenError

    def __init__(
        self,
        *,
        provider: str,
        site_config: Any = None,
    ) -> None:
        self._provider = provider
        self._settings = _SettingsReader(provider, site_config=site_config)
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
        cooldown = self._settings.circuit_cooldown_s()
        if (time.monotonic() - self._opened_at) >= cooldown:
            return "half_open"
        return "open"

    def snapshot(self) -> dict[str, Any]:
        """Lightweight dict for ``/api/health``. Cheap to call."""
        cooldown = self._settings.circuit_cooldown_s()
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
            "window_seconds": self._settings.circuit_window_s(),
            "failure_threshold": self._settings.circuit_failures(),
            "cooldown_seconds": cooldown,
            "seconds_until_recheck": seconds_until_recheck,
        }

    # -- gating -------------------------------------------------------------

    async def allow(self) -> None:
        """Raise :class:`CircuitOpenError` if the call is blocked.

        Call this BEFORE the wrapped operation. After cooldown the
        first allow() flips the state to half-open and admits the
        call; concurrent half-open admissions are serialized so only
        one probe is in-flight at a time.
        """
        async with self._lock:
            if self._opened_at is None:
                return  # closed, allow
            cooldown = self._settings.circuit_cooldown_s()
            elapsed = time.monotonic() - self._opened_at
            if elapsed < cooldown:
                raise self._open_error_cls(
                    f"{self._provider} circuit breaker open — failing fast",
                    provider=self._provider,
                    opened_at=self._opened_at,
                    cooldown_seconds=cooldown,
                    consecutive_failures=self._consecutive_failures,
                )
            # Cooldown elapsed → admit one half-open probe
            if self._half_open_in_flight:
                raise self._open_error_cls(
                    f"{self._provider} circuit breaker half-open — probe in flight",
                    provider=self._provider,
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
            logger.info(
                "%s circuit breaker closed (recovery)", self._provider,
            )
            audit_log_bg(
                f"{self._provider}_circuit_closed",
                "llm_resilience",
                {"provider": self._provider, "reason": "successful_probe"},
                severity="info",
            )

    async def record_failure(self, *, exc: BaseException | None = None) -> None:
        async with self._lock:
            now = time.monotonic()
            self._consecutive_failures += 1
            self._failures.append(now)
            window = self._settings.circuit_window_s()
            threshold = max(1, self._settings.circuit_failures())
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
                "%s circuit breaker tripped after %d failures in %.0fs window",
                self._provider,
                threshold,
                self._settings.circuit_window_s(),
            )
            audit_log_bg(
                f"{self._provider}_circuit_opened",
                "llm_resilience",
                {
                    "provider": self._provider,
                    "consecutive_failures": self._consecutive_failures,
                    "window_seconds": self._settings.circuit_window_s(),
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
            self._settings.circuit_failures(),
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


class LLMResilienceManager:
    """Bundles semaphore + circuit breaker + retry loop, generically.

    One instance per provider — the provider holds it on
    ``self._resilience`` and routes hot calls (``complete``, ``stream``,
    ``embed``) through ``await self._resilience.run(...)``.

    Construction is intentionally cheap — the semaphore and breaker
    pick up live config changes on every call so a worker restart isn't
    required to retune.

    Args:
        provider_name: Identifies the manager for settings + audit-log
            tagging. Drives the ``llm_<provider>_*`` settings prefix
            and the ``<provider>_call_failed`` audit event names.
        classifier: Maps an exception to a :class:`RetryDecision`. Lets
            providers honor SDK-specific retryability conventions
            (Anthropic's ``Retry-After``, Gemini's quota errors,
            OpenAI's 5xx) without baking them into this module.
        site_config: DI seam for app_settings.
        circuit_breaker_cls: Override for the :class:`CircuitBreaker`
            class — used by the Ollama compat alias to keep the legacy
            ``OllamaCircuitOpenError`` exception type live for callers
            that ``except`` on it.
    """

    def __init__(
        self,
        *,
        provider_name: str,
        classifier: Classifier,
        site_config: Any = None,
        circuit_breaker_cls: type[CircuitBreaker] = CircuitBreaker,
    ) -> None:
        self.provider_name = provider_name
        self._classifier = classifier
        self._site_config = site_config
        self._settings = _SettingsReader(provider_name, site_config=site_config)
        self._semaphore_capacity = self._settings.max_concurrent_calls()
        self._semaphore = asyncio.Semaphore(self._semaphore_capacity)
        self.circuit = circuit_breaker_cls(
            provider=provider_name, site_config=site_config,
        )
        self._rng = random.Random()
        # Register self with the process-local registry so the health
        # endpoint can enumerate live managers without each provider
        # having to wire it up explicitly.
        try:
            ResilienceRegistry.register(self)
        except Exception as e:  # pragma: no cover — defensive
            logger.debug(
                "[llm_resilience] registry.register failed for %s: %s",
                provider_name, e,
            )

    # -- introspection ------------------------------------------------------

    def health_snapshot(self) -> dict[str, Any]:
        """Status dict for ``/api/health -> components.llm_resilience.<provider>``."""
        snap = self.circuit.snapshot()
        snap["provider"] = self.provider_name
        snap["max_concurrent_calls"] = self._settings.max_concurrent_calls()
        # Active in-flight = capacity - available permits. asyncio.Semaphore
        # exposes ._value (private but stable across CPython versions);
        # wrapping the semaphore ourselves to avoid the private access
        # would add overhead to every call site.
        try:
            available = self._semaphore._value  # type: ignore[attr-defined]
        except AttributeError:
            available = self._semaphore_capacity
        snap["in_flight_calls"] = max(0, self._semaphore_capacity - available)
        return snap

    # -- semaphore resize ---------------------------------------------------

    def _resize_semaphore_if_needed(self) -> None:
        """Pick up a live ``llm_<provider>_max_concurrent_calls`` change.

        asyncio.Semaphore has no public resize, so we replace it.
        Callers already in ``run()`` keep their permit on the old
        semaphore and release it on the old semaphore — that's safe
        because we still hold a reference to the new one and the old
        one is GC'd once all permits are released. New callers acquire
        on the new semaphore.
        """
        target = self._settings.max_concurrent_calls()
        if target == self._semaphore_capacity:
            return
        logger.info(
            "Resizing %s concurrency semaphore: %d -> %d",
            self.provider_name,
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
        empty_result_exc: type[BaseException] | None = None,
    ) -> T:
        """Run ``operation`` under retry + queue + circuit-breaker.

        Args:
            operation: Zero-arg async callable (factory) that performs
                the actual call. Called fresh for every attempt — must
                be safe to invoke multiple times.
            op_name: Short name for logs / audit (``"complete"``,
                ``"stream"``, ``"embed"``).
            validate_result: Optional predicate. If supplied and returns
                False on a successful return value, the result is
                treated as ``empty_result_exc`` (defaulting to
                :class:`RuntimeError`) and the attempt is retried.
                Used by Ollama to catch the "200 OK but empty content"
                failure mode under thinking-model overflow.
            task_id: Optional pipeline task ID for audit-log
                correlation.
            empty_result_exc: Exception type raised when
                ``validate_result`` rejects a result. Defaults to
                :class:`RuntimeError`. Ollama's alias module passes
                ``OllamaEmptyResponseError`` so existing call sites that
                catch the old exception keep working.

        Raises:
            :class:`CircuitOpenError`: If breaker is open.
            The last exception raised by ``operation()`` once retries
            are exhausted, or ``empty_result_exc`` if
            ``validate_result`` rejected every attempt.
        """
        max_attempts = max(1, self._settings.retry_max_attempts())
        base_s = self._settings.retry_base_seconds()
        max_s = self._settings.retry_max_seconds()
        jitter = self._settings.retry_jitter_pct()
        empty_exc_cls = empty_result_exc or RuntimeError

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
                    current_span.set_attribute("llm.provider", self.provider_name)
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
                        # Treat as empty-response failure — retry. The
                        # exception type comes from the caller so
                        # provider-specific empty markers stay typed.
                        raise empty_exc_cls(
                            f"{op_name} returned empty/invalid result on attempt {attempt}"
                        )
                    if attempt > 1:
                        audit_log_bg(
                            f"{self.provider_name}_retry_success",
                            "llm_resilience",
                            {
                                "provider": self.provider_name,
                                "op": op_name,
                                "attempt": attempt,
                                "max_attempts": max_attempts,
                            },
                            task_id=task_id,
                            severity="info",
                        )
                        logger.info(
                            "%s %s succeeded on attempt %d/%d after retry",
                            self.provider_name,
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
                    decision = self._classify(exc)
                    retryable = decision.retry
                    is_last = attempt >= max_attempts
                    audit_log_bg(
                        f"{self.provider_name}_call_failed",
                        "llm_resilience",
                        {
                            "provider": self.provider_name,
                            "op": op_name,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "retryable": retryable,
                            "reason": decision.reason,
                            "wait_seconds": decision.wait_seconds,
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
                    # Retryable + more attempts left — honor classifier
                    # override when the SDK gave us a Retry-After value,
                    # otherwise compute the exponential schedule.
                    if decision.wait_seconds is not None:
                        delay = max(0.0, float(decision.wait_seconds))
                    else:
                        delay = compute_backoff(
                            attempt,
                            base_seconds=base_s,
                            max_seconds=max_s,
                            jitter_pct=jitter,
                            rng=self._rng,
                        )
                    logger.warning(
                        "%s %s attempt %d/%d failed (%s%s) — retrying in %.2fs",
                        self.provider_name,
                        op_name,
                        attempt,
                        max_attempts,
                        type(exc).__name__,
                        f"; reason={decision.reason}" if decision.reason else "",
                        delay,
                    )
                    audit_log_bg(
                        f"{self.provider_name}_retry_backoff",
                        "llm_resilience",
                        {
                            "provider": self.provider_name,
                            "op": op_name,
                            "attempt": attempt,
                            "delay_seconds": round(delay, 4),
                            "reason": decision.reason,
                            "explicit_wait": decision.wait_seconds is not None,
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
            f"{self.provider_name} {op_name} failed without a captured exception"
        )

    # -- classifier guard ---------------------------------------------------

    def _classify(self, exc: BaseException) -> RetryDecision:
        """Run the per-provider classifier with a safety net.

        ``CircuitOpenError`` (from any provider) is never retried —
        the breaker IS the backoff. ``CancelledError`` propagates.
        Otherwise we delegate to the configured classifier; if it
        raises (buggy plugin) we treat that as ``retry=False`` so a
        provider can't accidentally hide a programmer error.
        """
        if isinstance(exc, asyncio.CancelledError):
            return RetryDecision(retry=False, reason="cancelled")
        if isinstance(exc, CircuitOpenError):
            return RetryDecision(retry=False, reason="circuit_open")
        try:
            decision = self._classifier(exc)
        except Exception as classifier_exc:  # pragma: no cover — defensive
            logger.warning(
                "[%s] classifier raised; treating as non-retryable: %s",
                self.provider_name, classifier_exc,
            )
            return RetryDecision(retry=False, reason="classifier_error")
        # Defensive — a classifier returning the wrong type would crash
        # the inner ``decision.retry`` access. Coerce to a safe default
        # rather than spread the bug.
        if not isinstance(decision, RetryDecision):  # pragma: no cover
            logger.warning(
                "[%s] classifier returned non-RetryDecision (%r); "
                "treating as non-retryable",
                self.provider_name, type(decision).__name__,
            )
            return RetryDecision(retry=False, reason="classifier_bad_return")
        return decision


# ---------------------------------------------------------------------------
# Process-local registry
# ---------------------------------------------------------------------------


class ResilienceRegistry:
    """Process-local list of live :class:`LLMResilienceManager` instances.

    Each manager registers itself on construction so ``main.py``'s
    ``/api/health`` can render per-provider snapshots without the
    health handler having to know which providers a given deployment
    has loaded.

    No DB, no thread / event-loop synchronization needed — the registry
    is mutated only at startup and read on each health probe (rare,
    no contention). A simple module-level dict is enough.
    """

    _registry: dict[str, LLMResilienceManager] = {}

    @classmethod
    def register(cls, manager: LLMResilienceManager) -> None:
        """Register (or replace) the manager for a provider name.

        Re-registration is allowed — a fresh manager (e.g. from
        ``site_config`` rebind) supersedes the previous one. The latest
        registration wins so the health endpoint reflects the live
        instance.
        """
        cls._registry[manager.provider_name] = manager

    @classmethod
    def get(cls, provider_name: str) -> LLMResilienceManager | None:
        return cls._registry.get(provider_name)

    @classmethod
    def all(cls) -> dict[str, LLMResilienceManager]:
        """Return a snapshot dict of registered managers."""
        return dict(cls._registry)

    @classmethod
    def snapshot_all(cls) -> dict[str, dict[str, Any]]:
        """Return ``{provider: health_snapshot}`` for every registered manager.

        Catches per-manager errors so a buggy provider doesn't blank
        the whole health endpoint.
        """
        out: dict[str, dict[str, Any]] = {}
        for name, manager in cls._registry.items():
            try:
                out[name] = manager.health_snapshot()
            except Exception as e:  # pragma: no cover — defensive
                out[name] = {
                    "status": "error",
                    "reason": str(e)[:200],
                    "error_type": type(e).__name__,
                }
        return out

    @classmethod
    def reset(cls) -> None:
        """Test-only: clear the registry."""
        cls._registry.clear()


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------


__all__ = [
    "RetryDecision",
    "Classifier",
    "LLMResilienceManager",
    "CircuitBreaker",
    "CircuitOpenError",
    "ResilienceRegistry",
    "compute_backoff",
]
