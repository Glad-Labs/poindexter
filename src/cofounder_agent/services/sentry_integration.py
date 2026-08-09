"""
Error Tracking Integration — GlitchTip (Sentry-API-compatible)

Provides error tracking, performance monitoring, and issue management for
Poindexter (the AI cofounder pipeline).

The error sink for Glad-Labs/Poindexter is **GlitchTip** — the self-hosted,
open-source Sentry-API-compatible alternative. Locked in 2026-05-17 per
Glad-Labs/poindexter#414 to match ``feedback_no_paid_apis``. The
``sentry-sdk`` Python package is still the client library because GlitchTip
intentionally speaks the Sentry wire protocol; the "Sentry" naming in
identifiers below is therefore the SDK's nomenclature, NOT a reference to
sentry.io SaaS.

Local GlitchTip URL: http://localhost:8080 (org per
``app_settings.glitchtip_triage_org_slug``). Dashboard at the local URL
replaces the Sentry SaaS dashboard wherever the SDK docs reference it.

Features:
- Automatic exception capturing and reporting
- Performance monitoring with transaction tracing
- Breadcrumb tracking for debugging context
- FastAPI integration with request/response capture
- Async task monitoring
- Release tracking and version management
- Environment-specific configuration

Configuration:
Set ``app_settings.sentry_dsn`` to a GlitchTip DSN to enable:
    poindexter settings set sentry_dsn "http://<key>@localhost:8080/<project_id>"

Set ``sentry_enabled=false`` to disable reporting (e.g. local dev without
GlitchTip running). The legacy ``SENTRY_DSN`` env var is also honoured for
bootstrap paths that run before app_settings is reachable.

Sampling and SDK-debug knobs (all read from ``app_settings``):

* ``sentry_traces_sample_rate`` (default ``0.1``) — fraction of requests
  captured as performance traces.
* ``sentry_profiles_sample_rate`` (default ``0.1``) — fraction captured as
  CPU profiles.
* ``sentry_sdk_debug`` (default ``false``) — the SDK's own diagnostic
  logging. Legacy alias ``sentry_debug_logging`` is still honoured.

Noise-control knobs (see ``_before_send``, added from the 2026-08-08
GlitchTip triage — 364 open issues / 4,252 events, of which 31% were one
expected-control-flow exception and 206 issues were three incidents
fragmented by volatile text):

* ``sentry_drop_exception_types`` — CSV of exception class names dropped
  before send. Matched across the MRO, so subclasses are covered and no
  import of the raising package is needed.
* ``sentry_fingerprint_scrub_patterns`` — JSON array of
  ``[regex, replacement]`` pairs applied to the grouping fingerprint.
  Volatile tokens (temp filenames, UUIDs, float durations) otherwise mint
  a brand-new GlitchTip issue per event.
"""

import json
import logging
import re
from typing import Any

from fastapi import FastAPI

from services.logger_config import get_logger

try:
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    from sentry_sdk.integrations.threading import ThreadingIntegration

    try:
        from sentry_sdk.integrations.sqlalchemy import SqlAlchemyIntegration  # type: ignore
    except Exception:
        SqlAlchemyIntegration = None  # type: ignore[assignment,misc]

    SENTRY_AVAILABLE = True
except ImportError:
    sentry_sdk = None  # type: ignore[assignment]
    AsyncioIntegration = None  # type: ignore[assignment,misc]
    FastApiIntegration = None  # type: ignore[assignment,misc]
    LoggingIntegration = None  # type: ignore[assignment,misc]
    SqlAlchemyIntegration = None  # type: ignore[assignment,misc]
    StarletteIntegration = None  # type: ignore[assignment,misc]
    ThreadingIntegration = None  # type: ignore[assignment,misc]
    SENTRY_AVAILABLE = False
    logging.warning(
        "Sentry SDK not installed. Error tracking disabled. Install with: pip install sentry-sdk[fastapi]"
    )

logger = get_logger(__name__)


class SentryIntegration:
    """
    GlitchTip error tracking + performance monitoring integration.

    Handles initialization, configuration, and usage of the Sentry SDK
    against GlitchTip (the local Sentry-API-compatible sink) for the
    FastAPI application. Provides convenience methods for manual
    error/event reporting. Class name is retained from the SDK
    nomenclature for grep-ability with sentry-sdk docs; the underlying
    sink is GlitchTip per Glad-Labs/poindexter#414.
    """

    _initialized = False
    _sentry_enabled = False

    # SiteConfig stashed at initialize() so the sync ``_before_send`` hook can
    # read its noise-control settings. ``site_config.get`` is sync and served
    # from the in-memory cache the 1-minute ``reload_site_config`` job
    # refreshes, so operator edits land without a redeploy and without doing
    # DB I/O inside the SDK's event pipeline.
    _site_config: Any | None = None

    # Expected control flow that the SDK reports as an unhandled error.
    # Matched by class NAME across the MRO — no import of the raising
    # package required, and subclasses are covered.
    #
    # * ``GraphInterrupt`` — LangGraph ``interrupt()`` suspends a graph for
    #   operator approval (e.g. the seo_refresh_gate). The runner re-raises
    #   it correctly, but Sentry's asyncio integration still reports it from
    #   LangGraph's internal node tasks. A pause is not a failure.
    #   (2026-07-02 triage: 7 of 73 open issues were this.)
    # * ``GpuBusyError`` — raised by a budgeted ``gpu.lock()`` wait BEFORE
    #   any wait so fail-soft callers (QA rails, background jobs) can skip
    #   honestly this cycle instead of burning their timeout budget. The
    #   scheduler already records it as an ``info``-severity
    #   ``gpu_admission_rejected`` finding, so capturing it as an *error*
    #   double-reports one designed outcome at two different severities.
    #   (2026-08-08 triage: 1,320 events — 31% of ALL captured events, the
    #   single largest error source, none of it actionable.)
    DEFAULT_DROP_EXCEPTION_TYPES = "GraphInterrupt,GpuBusyError"

    # Volatile substrings that make otherwise-identical errors group apart.
    # Each entry is ``[regex, replacement]``; applied to the fingerprint
    # only, never to the message the operator reads.
    #
    # Sourced from the three worst fragmentation cases in the 2026-08-08
    # triage:
    #   164 issues — ``S3UploadFailedError: Failed to upload /tmp/tmpXXXX.json``
    #    31 issues — ``pg_advisory_lock wait exceeded 44.999968992000504s``
    #    11 issues — ``.../api/chat/watch/<uuid>``
    DEFAULT_FINGERPRINT_SCRUB_PATTERNS = json.dumps(
        [
            # tempfile.mkstemp names — meaningless outside the failing call
            [r"/tmp/tmp[A-Za-z0-9_]+", "/tmp/tmp<TMP>"],  # nosec B108 - a regex MATCHING a temp path in an error message; nothing is written here
            # UUIDs (conversation / task / run ids)
            [
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                "<UUID>",
            ],
            # Float durations — "exceeded 44.999968992000504s" et al. Bare
            # ints are left alone: they are usually meaningful (HTTP status,
            # error number), and full-precision floats are the actual
            # fragmenter.
            [r"\d+\.\d+s\b", "<DURATION>s"],
        ]
    )

    # Compiled-pattern cache keyed on the raw setting string, so a settings
    # reload recompiles once rather than per event.
    _scrub_cache_key: str | None = None
    _scrub_cache: tuple[tuple[re.Pattern[str], str], ...] = ()

    @classmethod
    def initialize(
        cls,
        app: FastAPI,  # noqa: ARG003 — main.py passes the app; FastApiIntegration hooks globally, doesn't need the instance
        site_config: Any | None = None,
        service_name: str = "cofounder-agent",
    ):
        """
        Initialize Sentry SDK with FastAPI integration.

        Args:
            app: FastAPI application instance
            site_config: SiteConfig DI seam. Required at runtime — passing
                ``None`` skips init with a loud warning rather than reaching
                for the deprecated module-level singleton (Phase H, GH#95).
                Optional in the signature only so existing callers that
                pass it positionally don't get a keyword-only TypeError.
            service_name: Name of the service for tracking

        Returns:
            bool: True if Sentry was successfully initialized, False otherwise
        """
        if not SENTRY_AVAILABLE or sentry_sdk is None:
            logger.warning("[ERROR] Sentry SDK not available - error tracking disabled")
            return False

        if cls._initialized:
            logger.debug("Sentry already initialized")
            return cls._sentry_enabled

        if site_config is None:
            logger.warning(
                "[SENTRY] setup_sentry called without site_config — "
                "Phase H requires DI; skipping init. Caller: %s",
                service_name,
            )
            cls._sentry_enabled = False
            return False

        # Hand the instance to the sync _before_send hook (noise-control
        # settings). Stored before the DSN/enabled early-returns so the
        # filter stays operator-tunable even on the paths that skip SDK init.
        cls._site_config = site_config

        # Get configuration from the injected site_config instance.
        sentry_dsn = (site_config.get("sentry_dsn", "") or "").strip()  # secret-get-ok: is_secret=false, a DSN identifier not a credential
        sentry_enabled = (
            (site_config.get("sentry_enabled", "true") or "true").lower()
            in ("true", "1", "yes")
        )
        environment = site_config.get("environment", "development") or "development"
        release = site_config.get("app_version", "3.0.1")
        # SDK-internal debug logging is gated by an explicit DB setting,
        # NOT by `environment`. The SDK emits ~12 lines/sec of envelope
        # dispatch + tracing baggage chatter under the `sentry_sdk.errors`
        # logger when `debug=True` — the name is misleading (it's the
        # SDK's diagnostic logger, level DEBUG), and any substring-match
        # error counter in Grafana picks them all up as false positives.
        # Operators flip this on only when actively troubleshooting the
        # SDK; default off everywhere.
        # ``sentry_sdk_debug`` is the seeded/documented key; ``sentry_debug_logging``
        # is the legacy name this code used to read. Only the legacy one was ever
        # read and only the canonical one was ever seeded, so neither did anything
        # on a default install. Canonical wins when explicitly set (non-empty —
        # '' is the unset sentinel); otherwise fall back to the legacy alias so an
        # operator who set it by hand keeps working.
        if (site_config.get("sentry_sdk_debug", "") or "").strip():
            sentry_debug_logging = site_config.get_bool("sentry_sdk_debug", False)
        else:
            sentry_debug_logging = site_config.get_bool("sentry_debug_logging", False)

        # Sampling rates are operator-tunable, NOT hardcoded. Defaults match the
        # seeded values (0.1) so an unconfigured install behaves identically in
        # every environment. The previous literal was
        # ``0.1 if environment == "production" else 1.0``, which meant the DB rows
        # were inert and every non-production process traced at 100% — ~50 DEBUG
        # lines/sec, and a suspected contributor to the 2026-05-15 event-loop hang.
        traces_sample_rate = site_config.get_float("sentry_traces_sample_rate", 0.1)
        profiles_sample_rate = site_config.get_float("sentry_profiles_sample_rate", 0.1)

        # Skip initialization if DSN not configured or explicitly disabled.
        # Do NOT set _initialized here — lifespan re-runs this after site_config
        # loads, and if we latched to "already initialized" on the empty read
        # from a module-level call, the real DSN would never take effect.
        if not sentry_dsn:
            logger.info("[SENTRY] DSN not configured (site_config.sentry_dsn) — SDK init skipped")
            cls._sentry_enabled = False
            return False

        if not sentry_enabled:
            logger.info("Sentry disabled via SENTRY_ENABLED=false")
            cls._initialized = True
            cls._sentry_enabled = False
            return False

        try:
            integrations = [
                FastApiIntegration(),  # type: ignore[misc]
                StarletteIntegration(),  # type: ignore[misc]
                AsyncioIntegration(),  # type: ignore[misc]
                LoggingIntegration(  # type: ignore[misc]
                    level=logging.INFO,
                    event_level=logging.ERROR,
                ),
                ThreadingIntegration(propagate_hub=True),  # type: ignore[misc]
            ]
            if SqlAlchemyIntegration is not None:
                integrations.append(SqlAlchemyIntegration())  # type: ignore[misc]

            sentry_sdk.init(
                dsn=sentry_dsn,
                integrations=integrations,
                # Environment and release information
                environment=environment,
                release=release,
                # Performance monitoring configuration — app_settings-driven
                # (sentry_traces_sample_rate / sentry_profiles_sample_rate).
                traces_sample_rate=traces_sample_rate,
                profiles_sample_rate=profiles_sample_rate,
                # Before sending event to Sentry (filter sensitive data)
                before_send=cls._before_send,  # type: ignore[arg-type]
                # Include local variables in stack traces
                include_local_variables=True,
                # Error attachment configurations
                max_value_length=4096,  # Max value length for variable inspection
                # SDK-internal debug logging — gated by app_settings.sentry_sdk_debug
                # (default false). Avoids the ~290k/day false-positive
                # "error" count from `sentry_sdk.errors` DEBUG chatter.
                debug=sentry_debug_logging,
            )

            # Set user context for authenticated requests (if available)
            sentry_sdk.set_tag("service", service_name)
            sentry_sdk.set_tag("version", release)

            # The DSN host is the part operators will check against
            # GlitchTip's URL, but the public key is a credential that
            # shouldn't go in logs. Log only host+path.
            try:
                from urllib.parse import urlparse

                _parsed = urlparse(sentry_dsn)
                _safe_endpoint = f"{_parsed.scheme}://{_parsed.hostname or '?'}"
                if _parsed.port:
                    _safe_endpoint += f":{_parsed.port}"
                _safe_endpoint += _parsed.path or ""
            except Exception:  # noqa: BLE001
                _safe_endpoint = "(redacted)"

            logger.info("[SENTRY] SDK initialized — endpoint=%s", _safe_endpoint)
            logger.info("   Environment: %s", environment)
            logger.info("   Release: %s", release)
            logger.info("   Traces Sample Rate: %s", traces_sample_rate)
            logger.info("   Profiles Sample Rate: %s", profiles_sample_rate)

            cls._initialized = True
            cls._sentry_enabled = True
            return True

        except Exception as e:
            logger.error(
                "[_initialize] [ERROR] Failed to initialize Sentry: %s", e, exc_info=True
            )
            cls._initialized = True
            cls._sentry_enabled = False
            return False

    @classmethod
    def _setting(cls, key: str, default: str) -> str:
        """Read a noise-control setting without ever raising.

        ``_before_send`` runs inside the SDK's event pipeline; an exception
        here would break error reporting itself, so every failure mode
        (no site_config, a cache miss, a bad type) falls back to the
        documented default.
        """
        sc = cls._site_config
        if sc is None:
            return default
        try:
            raw = sc.get(key, default)
        except Exception:  # noqa: BLE001  # silent-ok: raising inside _before_send breaks ALL error reporting; the documented default is the safe fallback
            return default
        if raw is None:
            return default
        text = str(raw).strip()
        # '' is the unset sentinel per feedback_app_settings_value_not_null.
        return text or default

    @classmethod
    def _drop_exception_types(cls) -> frozenset[str]:
        """Exception class names dropped before send (``app_settings``-driven)."""
        raw = cls._setting(
            "sentry_drop_exception_types", cls.DEFAULT_DROP_EXCEPTION_TYPES
        )
        return frozenset(part.strip() for part in raw.split(",") if part.strip())

    @classmethod
    def _scrub_patterns(cls) -> tuple[tuple[re.Pattern[str], str], ...]:
        """Compiled ``[regex, replacement]`` fingerprint scrubbers, cached.

        A malformed setting logs loud and falls back to the defaults rather
        than silently disabling scrubbing — an operator who breaks the JSON
        should hear about it, not quietly get their backlog fragmented again.
        """
        raw = cls._setting(
            "sentry_fingerprint_scrub_patterns",
            cls.DEFAULT_FINGERPRINT_SCRUB_PATTERNS,
        )
        if raw == cls._scrub_cache_key:
            return cls._scrub_cache

        compiled: list[tuple[re.Pattern[str], str]] = []
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise TypeError(f"expected a JSON array, got {type(parsed).__name__}")
            for entry in parsed:
                if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                    raise TypeError(f"expected [regex, replacement] pairs, got {entry!r}")
                pattern, replacement = re.compile(str(entry[0])), str(entry[1])
                # Exercise the replacement template now: an invalid group
                # reference (e.g. "\\9" with one group) raises only at sub()
                # time, which would otherwise fail silently once per event.
                pattern.sub(replacement, "")
                compiled.append((pattern, replacement))
        except (ValueError, TypeError, re.error) as exc:
            logger.error(
                "[SENTRY] sentry_fingerprint_scrub_patterns is invalid (%s) — "
                "falling back to built-in defaults. Fix the setting in app_settings.",
                exc,
            )
            if raw != cls.DEFAULT_FINGERPRINT_SCRUB_PATTERNS:
                cls._scrub_cache_key = raw
                cls._scrub_cache = cls._compile_default_scrubbers()
                return cls._scrub_cache
            compiled = []

        cls._scrub_cache_key = raw
        cls._scrub_cache = tuple(compiled)
        return cls._scrub_cache

    @classmethod
    def _compile_default_scrubbers(cls) -> tuple[tuple[re.Pattern[str], str], ...]:
        """Compile the built-in scrubbers. Defaults are ours, so they parse."""
        return tuple(
            (re.compile(pat), repl)
            for pat, repl in json.loads(cls.DEFAULT_FINGERPRINT_SCRUB_PATTERNS)
        )

    @classmethod
    def _scrub(cls, text: str) -> str:
        """Replace volatile tokens so equivalent errors group together.

        Bad replacement templates are caught at compile time by
        ``_scrub_patterns`` (loudly, once) rather than here, so the per-call
        guard below is pure defence and not the reporting path for a
        misconfiguration.
        """
        for pattern, replacement in cls._scrub_patterns():
            try:
                text = pattern.sub(replacement, text)
            except Exception:  # noqa: BLE001  # nosec B112 - continue is the point: one bad pattern must not stop the others  # silent-ok: templates are validated loudly at compile time; this runs per-event and must never raise
                continue
        return text

    @classmethod
    def _before_send(cls, event: dict, hint: dict) -> dict | None:
        """
        Filter events before sending to Sentry.
        Remove sensitive data (passwords, tokens, etc.)

        Also drops expected control flow and normalizes the grouping
        fingerprint — see the noise-control knobs in the module docstring.

        Args:
            event: The event dictionary
            hint: Additional hint information with exception details

        Returns:
            Modified event dict, or None to drop the event
        """
        # Drop expected control flow that the SDK reports as an unhandled
        # error (GraphInterrupt gate pauses, GpuBusyError admission skips).
        # Matched by class name across the MRO, so this needs no import of
        # the raising package and catches subclasses.
        drop_types = cls._drop_exception_types()
        exc_info = hint.get("exc_info") if hint else None
        if drop_types and isinstance(exc_info, (tuple, list)) and exc_info:
            exc_type = exc_info[0]
            if any(
                t.__name__ in drop_types for t in getattr(exc_type, "__mro__", [])
            ):
                return None

        # Collapse volatile text into a stable fingerprint. Only set the
        # fingerprint when scrubbing actually changed something: overriding
        # grouping for every event would merge unrelated errors, whereas a
        # message with no volatile tokens is already grouping correctly.
        cls._apply_fingerprint(event)

        # Check if this is an error event we should capture
        if event.get("level") == "error" or (hint and "exc_info" in hint):
            # Redact sensitive headers
            if "request" in event:
                headers = event["request"].get("headers", {})
                sensitive_headers = ["authorization", "cookie", "x-api-key", "x-token"]
                for header in sensitive_headers:
                    if header in headers:
                        headers[header] = "[REDACTED]"

            # Redact sensitive query parameters
            if "request" in event and "url" in event["request"]:
                url = event["request"]["url"]
                if "api_key=" in url or "token=" in url:
                    event["request"]["url"] = url.replace(
                        url[url.find("api_key=") :], "api_key=[REDACTED]"
                    )

        return event

    @classmethod
    def _apply_fingerprint(cls, event: dict) -> None:
        """Set a scrubbed grouping fingerprint in place, when it helps.

        GlitchTip derives an issue's identity from the exception value (or
        log message), so a volatile token in that text mints a fresh issue
        per event. Scrub the volatile parts and pin the fingerprint to the
        result; the message the operator reads is left untouched.

        No-ops when scrubbing changes nothing, so events that already group
        correctly keep the SDK's default grouping. Never raises — a
        fingerprint is an optimisation, not a reason to lose an error.
        """
        try:
            # Exception events: group on (type, scrubbed value).
            values = (event.get("exception") or {}).get("values") or []
            if values and isinstance(values[-1], dict):
                latest = values[-1]
                original = str(latest.get("value") or "")
                if original:
                    scrubbed = cls._scrub(original)
                    if scrubbed != original:
                        exc_type = str(latest.get("type") or "Exception")
                        event["fingerprint"] = [exc_type, scrubbed]
                    return

            # Log events (LoggingIntegration): group on the scrubbed message.
            logentry = event.get("logentry")
            if isinstance(logentry, dict):
                original = str(logentry.get("message") or "")
                if original:
                    scrubbed = cls._scrub(original)
                    if scrubbed != original:
                        event["fingerprint"] = [scrubbed]
                    return

            message = event.get("message")
            if isinstance(message, str) and message:
                scrubbed = cls._scrub(message)
                if scrubbed != message:
                    event["fingerprint"] = [scrubbed]
        except Exception:  # noqa: BLE001  # silent-ok: a fingerprint is a grouping optimisation — losing it must never cost us the error event itself
            logger.debug("[SENTRY] fingerprint scrub failed", exc_info=True)

    @classmethod
    def capture_exception(
        cls, error: Exception, context: dict | None = None, level: str = "error"
    ):
        """
        Manually capture an exception with optional context.

        Args:
            error: The exception to capture
            context: Additional context dictionary
            level: Severity level (fatal, error, warning, info, debug)
        """
        if not cls._sentry_enabled or sentry_sdk is None:
            return

        try:
            with sentry_sdk.push_scope() as scope:
                if context:
                    for key, value in context.items():
                        scope.set_context(key, value)

                scope.set_level(level)  # type: ignore[arg-type]
                sentry_sdk.capture_exception(error)
        except Exception as e:
            logger.error(
                "[_capture_exception] Failed to capture exception in Sentry: %s", e, exc_info=True
            )

    @classmethod
    def capture_message(cls, message: str, level: str = "info", context: dict | None = None):
        """
        Manually capture a message event.

        Args:
            message: The message to capture
            level: Severity level (fatal, error, warning, info, debug)
            context: Additional context dictionary
        """
        if not cls._sentry_enabled or sentry_sdk is None:
            return

        try:
            with sentry_sdk.push_scope() as scope:
                if context:
                    for key, value in context.items():
                        scope.set_context(key, value)

                sentry_sdk.capture_message(message, level=level)  # type: ignore[arg-type]
        except Exception as e:
            logger.error(
                "[_capture_message] Failed to capture message in Sentry: %s", e, exc_info=True
            )

    @classmethod
    def set_user_context(cls, user_id: str, email: str = "", username: str = ""):
        """
        Set user context for error tracking.
        Called after authentication to track which user experienced errors.

        Args:
            user_id: Unique user identifier
            email: User email address
            username: User's username
        """
        if not cls._sentry_enabled or sentry_sdk is None:
            return

        try:
            sentry_sdk.set_user({"id": user_id, "email": email, "username": username})
        except Exception as e:
            logger.error(
                "[_set_user_context] Failed to set user context in Sentry: %s", e, exc_info=True
            )

    @classmethod
    def clear_user_context(cls):
        """Clear user context after logout."""
        if not cls._sentry_enabled or sentry_sdk is None:
            return

        try:
            sentry_sdk.set_user(None)
        except Exception as e:
            logger.error(
                "[_clear_user_context] Failed to clear user context in Sentry: %s", e, exc_info=True
            )

    @classmethod
    def add_breadcrumb(
        cls, category: str, message: str, level: str = "info", data: dict | None = None
    ):
        """
        Add a breadcrumb for debugging context.
        Breadcrumbs are captured and sent with errors for better debugging.

        Args:
            category: Breadcrumb category (e.g., "api.call", "database", "auth")
            message: Breadcrumb message
            level: Severity level (critical, error, warning, info, debug)
            data: Additional data dictionary
        """
        if not cls._sentry_enabled or sentry_sdk is None:
            return

        try:
            sentry_sdk.add_breadcrumb(
                category=category, message=message, level=level, data=data or {}
            )
        except Exception as e:
            logger.error(
                "[_add_breadcrumb] Failed to add breadcrumb in Sentry: %s", e, exc_info=True
            )

    @classmethod
    def start_transaction(cls, name: str, op: str = "http.request", description: str = ""):
        """
        Start a performance monitoring transaction.

        Args:
            name: Transaction name
            op: Operation type (http.request, task, function, etc.)
            description: Human-readable description

        Returns:
            Sentry transaction object or None
        """
        if not cls._sentry_enabled or sentry_sdk is None:
            return None

        try:
            return sentry_sdk.start_transaction(name=name, op=op, description=description)
        except Exception as e:
            logger.error(
                "[_start_transaction] Failed to start Sentry transaction: %s", e, exc_info=True
            )
            return None

    @classmethod
    def get_initialized_status(cls) -> bool:
        """Check if Sentry is enabled and initialized."""
        return cls._sentry_enabled


def setup_sentry(
    app: FastAPI,
    site_config: Any | None = None,
    service_name: str = "cofounder-agent",
) -> bool:
    """
    Convenience function to initialize Sentry.

    Usage in main.py:
        from services.sentry_integration import setup_sentry
        setup_sentry(app, site_config, service_name="poindexter-worker")

    Args:
        app: FastAPI application instance
        site_config: The DI'd SiteConfig instance (Phase H). Required at
            runtime — passing ``None`` skips init with a warning rather
            than reaching for the deprecated module-level singleton.
        service_name: Name of the service

    Returns:
        bool: True if successfully initialized
    """
    return SentryIntegration.initialize(app, site_config, service_name)
