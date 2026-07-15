"""Findings emission — replaces the dead Gitea issues path.

Background audit jobs (under ``services/jobs/``) used to file Gitea issues
for discovered problems via ``utils/gitea_issues.py``. Gitea was
decommissioned 2026-04-30, leaving the issue-creation calls silently
no-op'ing for 8 days — every quality finding vanished into a black hole.

This module replaces that path with a persistent emit-to-audit_log shape:
:func:`emit_finding` writes a structured row to ``audit_log`` with
``event_type='finding'``. A future brain-daemon-side findings-dispatcher
will read those rows and route them per-kind (auto-fix / Discord /
GitHub Issue / log-only) using policy in ``app_settings`` — see the
tracking issue filed alongside this module.

For now: findings are persistent, queryable, and observable — but no
automatic delivery. The ``audit_log`` row IS the finding. Triage
manually via SQL until the dispatcher lands.

Why this shape (and not a direct Gitea→GitHub rewire):

- Detection and delivery are separate concerns. Jobs become
  detection-only; routing logic lives in one place (the future
  dispatcher in the brain daemon, per ``feedback_alert_auto_triage``).
- Findings persist regardless of whether any delivery channel works.
  The 8-day silent-failure regression is not repeatable.
- Per-kind delivery policy can land later, configurable via
  ``app_settings`` (matches ``feedback_db_first_config``).

Best-effort exception swallows use this too: when a broad ``except`` swallows a
non-fatal failure and returns a fallback, record it here —
``emit_finding(severity="info", dedup_key=...)`` — so the failure is visible on
the Findings dashboard without paging or blocking. ``info`` never pages: the
findings router's fetch floor (``_ROUTABLE_SEVERITIES`` = warn/warning/critical)
never selects ``info`` rows, so an ``info`` finding is dashboard-visible but
never routed. In a loop, count failures and emit ONE finding after the loop
(never one per iteration). This is the convention enforced by
``scripts/ci/lint_silent_excepts.py``.
"""

from __future__ import annotations

from typing import Any

from services.audit_event_schemas import validate_event_details
from services.audit_log import audit_log_bg

# "warning" is a drifted spelling of "warn" that crept into ~7 call sites
# (copy-pasted from Prometheus/Alertmanager conventions) before this
# normalization existed. Canonicalize here so every future emit_finding
# caller writes the one spelling the rest of the findings subsystem
# (ROUTABLE_SEVERITIES, the console severity filter/color map) already
# treats as equivalent to "warn".
_SEVERITY_ALIASES = {"warning": "warn"}


def emit_finding(
    *,
    source: str,
    kind: str,
    title: str,
    body: str,
    severity: str = "info",
    dedup_key: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit a typed finding to ``audit_log`` for later triage.

    Fire-and-forget — never raises. If audit logging is uninitialised
    (e.g. unit tests without the global logger), the call silently drops.

    Parameters mirror what the future dispatcher will route on:

    - ``source``: which job/probe raised it (e.g. ``audit_published_quality``)
    - ``kind``: typed finding category (``broken_external_link``,
      ``quality_regression``, ``anomaly``, etc.)
    - ``title``: short human-readable summary, suitable as a future
      issue title or Discord embed title
    - ``body``: full finding details, markdown-friendly
    - ``severity``: ``info`` | ``warn`` | ``critical`` (default ``info``).
      The dispatcher will use this to pick channel — ``critical`` =
      Telegram, ``warn`` = Discord, ``info`` = log-only — per
      ``feedback_telegram_vs_discord``. ``"warning"`` is accepted and
      silently normalized to ``"warn"`` (a spelling that drifted into
      several call sites); pass ``"warn"`` in new code.
    - ``dedup_key``: optional stable key for cooldown logic in the
      dispatcher (e.g. ``"broken-link:https://example.com"``).
    - ``extra``: additional structured data for the dispatcher to act on
      (e.g. the list of broken URLs so an auto-fix probe can retry).

    Query findings via:

    .. code-block:: sql

        SELECT timestamp, source, severity, details->>'kind' AS kind,
               details->>'title' AS title
        FROM audit_log
        WHERE event_type = 'finding'
        ORDER BY timestamp DESC
        LIMIT 50;
    """
    details: dict[str, Any] = {
        "kind": kind,
        "title": title,
        "body": body,
    }
    if dedup_key is not None:
        details["dedup_key"] = dedup_key
    if extra:
        details["extra"] = extra
    severity = _SEVERITY_ALIASES.get(severity.lower(), severity.lower())
    audit_log_bg(
        event_type="finding",
        source=source,
        details=validate_event_details("finding", details),
        severity=severity,
    )
