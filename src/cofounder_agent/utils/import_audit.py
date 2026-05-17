"""Boot-time import audit for the worker.

Closes the silent-degradation gap from
[Glad-Labs/poindexter#504](https://github.com/Glad-Labs/poindexter/issues/504).

Several modules use ``try/except ImportError: AVAILABLE = False``
patterns to keep working when an optional SDK is missing. That's fine
for genuinely-optional features (OpenTelemetry SDK, when
``enable_tracing=false``), but disastrous for packaging regressions — a
broken worker image where ``sentry_sdk`` or ``sentence_transformers``
or ``slowapi`` failed to install would boot "successfully" with whole
features dark.

The 2026-05-14 Langfuse v2→v3 incident was this exact shape: the SDK
moved its decorator path, the shim caught ImportError, flipped a
boolean to ``False``, and production traces silently stopped flowing
for weeks. Per ``feedback_no_silent_defaults``: WARN logs are silent
in practice; only ``notify_operator`` (Telegram + Discord +
alerts.log) is actually loud.

This module is called from ``main.py`` lifespan AFTER ``site_config``
is loaded so the audit can read the relevant ``enable_X`` flags and
distinguish "packaging bug" (loud) from "feature disabled by operator"
(silent OK).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _check_module(
    module_path: str,
    *,
    why_required: str,
    docs_hint: str,
) -> tuple[bool, str | None]:
    """Probe a single import. Returns (ok, detail_for_notify_on_failure)."""
    try:
        __import__(module_path)
        return True, None
    except ImportError as exc:
        return False, (
            f"`import {module_path}` raised ImportError: {exc}. "
            f"This is a packaging regression — {why_required}. "
            f"Fix: {docs_hint}"
        )


def audit_worker_imports(site_config: Any) -> dict[str, str]:
    """Audit all expected-present worker modules; notify_operator on
    any packaging regression.

    Returns ``{"<module>": "<failure-detail>"}`` for missing modules,
    empty dict on clean boot. Caller (main.py lifespan) logs at INFO
    when clean.

    Three classes of check:

    1. **Always-required** — module ships with the worker image; missing
       = broken pyproject install. Loud-fail.
    2. **Conditional-required** — required IFF a feature flag is on
       (e.g. ``opentelemetry`` only matters when ``enable_tracing=true``).
       Loud-fail only when the operator turned the feature on.
    3. **Optional** — legitimately bring-your-own (not currently any
       on the worker side; reserved for plugin-loaded deps).

    No exception types bubble out of here — audit must never block boot.
    Failures are reported via notify_operator + returned dict.
    """
    failures: dict[str, str] = {}

    # ---- Class 1: always-required modules ---------------------------------
    #
    # Each of these ships in ``pyproject.toml`` and is silently caught by
    # an ``except ImportError`` somewhere in the codebase. Missing = the
    # worker image was built against a broken poetry lockfile or a build
    # step failed silently.
    always_required = [
        (
            "services.deepeval_rails",
            "QA Rails (DeepEval brand-fabrication / G-Eval / Faithfulness) "
            "skip silently when this is gone — pipeline runs blind on the "
            "deepeval rails. See multi_model_qa.py:1235/1282/1365.",
            "Rebuild the worker image: "
            "`docker compose build worker && docker compose up -d worker`.",
        ),
        (
            "sentry_sdk",
            "GlitchTip / Sentry error reporting is dark — unhandled "
            "exceptions stop being captured for triage. See "
            "utils/exception_handlers.py:30.",
            "Add `sentry-sdk` to pyproject.toml dependencies (already pinned).",
        ),
        # sentence_transformers is intentionally NOT in this list. The
        # production embedding path flows through Ollama's nomic-embed-text
        # via services.embeddings; memory_system.py's SentenceTransformer
        # branch is legacy and silently falls back to the alternative
        # cosine path. If we ever wire up a SentenceTransformer-required
        # feature, add a conditional-required check tied to a feature flag.
        (
            "slowapi",
            "Rate limiting is silently disabled — every @limiter.limit() "
            "decorator is a pass-through. Public API endpoints unprotected. "
            "See utils/rate_limiter.py:28.",
            "Add `slowapi` to pyproject.toml (already pinned).",
        ),
        (
            "langfuse",
            "LLM tracing UI goes dark — every @observe decorator becomes "
            "a no-op. See services/langfuse_shim.py.",
            "Add `langfuse>=3.0,<4.0` to pyproject.toml (already pinned).",
        ),
        (
            "litellm",
            "Cost tracking degraded — every cost lookup falls back to "
            "$0/$0.005 defaults. cost_guard caps run blind. See "
            "services/cost_lookup.py:75.",
            "Add `litellm>=1.83.7,<2.0` to pyproject.toml (already pinned).",
        ),
    ]
    for mod_path, why, hint in always_required:
        ok, detail = _check_module(mod_path, why_required=why, docs_hint=hint)
        if not ok and detail is not None:
            failures[mod_path] = detail

    # ---- Class 2: conditional-required modules ----------------------------
    #
    # opentelemetry is legitimately optional; only required when the
    # operator turned tracing on in app_settings.
    enable_tracing = (
        site_config.get("enable_tracing", "false") or "false"
    ).lower() == "true"
    if enable_tracing:
        # Check the SPECIFIC submodules that ``services.telemetry``
        # actually imports — not the umbrella ``opentelemetry``
        # package. ``opentelemetry-api`` + ``opentelemetry-sdk`` ship
        # the ``opentelemetry`` namespace import, so an audit that
        # probes just ``opentelemetry`` would silently pass even when
        # the critical instrumentation + exporter packages are
        # missing — exactly the regression Glad-Labs/poindexter#505
        # surfaced 2026-05-17 (Tempo panels empty, audit reported
        # clean because the umbrella import succeeded).
        otel_required = [
            (
                "opentelemetry.exporter.otlp.proto.http.trace_exporter",
                "OTLP HTTP exporter (services/telemetry.py imports it "
                "directly). Without it the TracerProvider has no exporter "
                "and every span is silently dropped before reaching Tempo.",
                "Add `opentelemetry-exporter-otlp` to pyproject.toml dependencies.",
            ),
            (
                "opentelemetry.instrumentation.fastapi",
                "FastAPIInstrumentor (services/telemetry.py:154) wires "
                "per-request spans. Missing means /api/* requests produce "
                "no spans even when the exporter is healthy.",
                "Add `opentelemetry-instrumentation-fastapi` to pyproject.toml.",
            ),
        ]
        for mod_path, why, hint in otel_required:
            ok, detail = _check_module(
                mod_path, why_required=why, docs_hint=hint,
            )
            if not ok and detail is not None:
                failures[mod_path] = detail

    # ---- Notify on any failure --------------------------------------------
    if failures:
        for mod, detail in failures.items():
            logger.error("[boot-audit] %s missing — %s", mod, detail)
        try:
            from brain.operator_notifier import notify_operator
        except ImportError:
            try:
                from operator_notifier import notify_operator  # type: ignore[no-redef]
            except ImportError:
                logger.warning(
                    "[boot-audit] operator_notifier unavailable — %d failures "
                    "logged but NOT paged", len(failures),
                )
                return failures
        try:
            notify_operator(
                title=(
                    f"Worker boot-time import audit failed "
                    f"({len(failures)} module{'s' if len(failures) != 1 else ''})"
                ),
                detail="\n\n".join(
                    f"• {mod}: {detail}" for mod, detail in failures.items()
                ),
                source="worker:boot-audit",
                severity="error",
            )
        except Exception as exc:  # noqa: BLE001 — notify is best-effort
            logger.warning("[boot-audit] notify_operator raised: %s", exc)
    else:
        logger.info(
            "[boot-audit] all %d expected-present modules importable",
            len(always_required) + (len(otel_required) if enable_tracing else 0),
        )

    return failures


__all__ = ["audit_worker_imports"]
