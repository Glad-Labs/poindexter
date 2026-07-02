"""operator_notifier — dependency-free "tell the human" helper (#198).

When something fails loudly during startup — missing DATABASE_URL, missing
Telegram token, corrupt bootstrap file — we don't want to crash silently.
This module tries, in order:

    1. Telegram bot API (if TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are set)
    2. Discord webhook (if DISCORD_OPS_WEBHOOK_URL or fallback env is set)
    3. Append to ~/.poindexter/alerts.log (always, even if 1 and 2 work)
    4. stderr (always)

Designed to be importable from any process — brain daemon, worker,
MCP server, CLI — without dragging in asyncpg, httpx, FastAPI, or any
of the heavy stack. Uses only stdlib so it still works when dependencies
are broken.

Usage:

    from brain.operator_notifier import notify_operator

    try:
        db_url = os.environ["DATABASE_URL"]
    except KeyError:
        notify_operator(
            title="Brain daemon cannot start",
            detail="DATABASE_URL is not set. Run `poindexter setup` "
                   "or add DATABASE_URL to your environment.",
            source="brain_daemon",
            severity="critical",
        )
        raise SystemExit(2)
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

_Severity = Literal["info", "warning", "error", "critical"]

_ALERTS_LOG = Path.home() / ".poindexter" / "alerts.log"
_TELEGRAM_TIMEOUT = 10  # seconds — short so startup isn't blocked by a dead network
_DISCORD_TIMEOUT = 10

logger = logging.getLogger(__name__)


def _severity_emoji(severity: _Severity) -> str:
    return {
        "info": "ℹ️",
        "warning": "⚠️",
        "error": "🔴",
        "critical": "🚨",
    }.get(severity, "•")


# Credential shapes that must never reach a log line or chat channel, even
# when a caller pastes them into ``detail`` (e.g. a database URL in a
# cannot-start alert). Applied to every outbound message by _fmt_message.
_REDACT_PATTERNS = (
    # URL userinfo password: scheme://user:PASSWORD@host
    (re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://[^/@:\s]+:)[^@\s]+(?=@)"), r"\1***"),
    # key=value / key: value where the key looks secret-bearing
    (
        re.compile(
            r"(?i)\b((?:api[_-]?key|token|secret|password|passwd|pwd)"
            r"[a-z0-9_-]*)\s*([=:])\s*\S+",
        ),
        r"\1\2***",
    ),
)


def _redact_credentials(text: str) -> str:
    """Mask password/token-shaped substrings before the text leaves us."""
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _fmt_message(title: str, detail: str, source: str, severity: _Severity) -> str:
    return _redact_credentials(
        f"{_severity_emoji(severity)} {title}\n"
        f"Source: {source}\n"
        f"Severity: {severity}\n"
        f"{detail}",
    )


def _try_telegram(text: str) -> tuple[bool, str]:
    """Return (sent, reason). Never raises."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False, "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set"
    try:
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=_TELEGRAM_TIMEOUT)
        return True, "telegram"
    except Exception as e:
        return False, f"telegram send failed: {e!r}"


def _try_discord(text: str) -> tuple[bool, str]:
    """Return (sent, reason). Never raises."""
    url = (
        os.getenv("DISCORD_OPS_WEBHOOK_URL")
        or os.getenv("DISCORD_LAB_LOGS_WEBHOOK_URL")
        or ""
    ).strip()
    if not url:
        return False, "no DISCORD_*_WEBHOOK_URL set"
    try:
        # Discord has a 2000-char content limit.
        body = text if len(text) <= 1900 else text[:1897] + "…"
        payload = json.dumps({"content": body}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Poindexter-OperatorNotifier/1.0",
            },
        )
        urllib.request.urlopen(req, timeout=_DISCORD_TIMEOUT)
        return True, "discord"
    except Exception as e:
        return False, f"discord send failed: {e!r}"


def _append_alerts_log(text: str) -> tuple[bool, str]:
    """Append to ~/.poindexter/alerts.log. Never raises."""
    try:
        _ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).isoformat()
        with _ALERTS_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {text}\n\n")
        return True, f"alerts.log ({_ALERTS_LOG})"
    except Exception as e:
        return False, f"alerts.log write failed: {e!r}"


# ---------------------------------------------------------------------------
# Page cooldown — cross-cycle repeat suppression (audit 2026-07-01).
#
# Captured: 504 operator_paged events in one week, 92% from three brain
# probes re-paging the SAME chronic condition every 5-minute cycle
# (prefect_stuck_flow 266, operator_url 121, glitchtip_triage 78). The
# alert_dispatcher's alert_dedup_state table already collapses repeats on
# the alert_events path, but probes call notify_operator() directly and
# had no equivalent — so a condition that stayed bad re-paged forever.
#
# This gate suppresses the EXTERNAL sends (Telegram/Discord) for repeats
# of the same dedup key inside a cooldown window. First page always goes
# out; the condition clearing and re-firing after the window re-pages.
# ``critical`` severity always bypasses the gate — phone-ping pages are
# rare and must never be swallowed. stderr/logger/alerts.log still record
# every suppressed repeat (marked as such) so the local history stays
# complete, and the audit sink still fires so the silent-alerter watchdog
# and the operator_paged dashboards can count suppressions distinctly.
#
# State is in-memory (module-level) because this module must stay
# stdlib-only and importable before any DB exists. The brain daemon is a
# long-lived process, so the window survives across cycles; a daemon
# restart resets it, which costs at most one extra page per key.
# Cooldown is DISABLED (0) by default so bootstrap/CLI one-shot callers
# keep today's behaviour; the brain daemon opts in each cycle from
# ``app_settings.operator_page_cooldown_minutes``.
# ---------------------------------------------------------------------------

_PAGE_COOLDOWN_SECONDS: int = 0
_LAST_PAGED_AT: dict[str, float] = {}


def set_page_cooldown_seconds(seconds: int) -> None:
    """Set the repeat-suppression window (0 disables the gate).

    Called by the brain daemon each cycle with the current
    ``operator_page_cooldown_minutes`` app_setting so the window stays
    operator-tunable without a redeploy.
    """
    global _PAGE_COOLDOWN_SECONDS
    _PAGE_COOLDOWN_SECONDS = max(0, int(seconds))


def reset_page_cooldown_state() -> None:
    """Clear the last-paged ledger (tests)."""
    _LAST_PAGED_AT.clear()


def _cooldown_suppresses(key: str, severity: _Severity, now: float) -> bool:
    """True when this page is a repeat inside the cooldown window."""
    if _PAGE_COOLDOWN_SECONDS <= 0 or severity == "critical":
        return False
    last = _LAST_PAGED_AT.get(key)
    return last is not None and (now - last) < _PAGE_COOLDOWN_SECONDS


# Optional sink for "I just paged the operator" events. Set by
# brain_daemon at startup (see brain.brain_daemon.set_notify_audit_sink)
# so calls from probes / business logic land in audit_log with
# event_type="operator_paged". The silent-alerter watchdog
# (brain/business_probes.probe_silent_alerter) uses this to know
# whether the alert plane is actually delivering pages — without it,
# direct-to-Telegram notifications looked identical to a dead alerter.
#
# Kept as an injectable callable so this module stays stdlib-only;
# the wiring happens in brain_daemon where asyncpg is already imported.
_NOTIFY_AUDIT_SINK: _NotifyAuditSink | None = None


def set_notify_audit_sink(sink) -> None:
    """Wire a callable that records ``operator_paged`` audit events.

    Sink signature: ``sink(*, source: str, severity: str, title: str,
    detail: str, results: dict)``. Called once per ``notify_operator``
    invocation, after the external channels have been attempted, so
    ``results`` reflects which channels succeeded.

    The sink is called best-effort — exceptions are swallowed so a
    broken sink can't disable the operator alerting path.
    """
    global _NOTIFY_AUDIT_SINK
    _NOTIFY_AUDIT_SINK = sink


# Forward declaration for type checkers; runtime is the duck-typed callable above.
class _NotifyAuditSink:
    def __call__(
        self, *, source: str, severity: str, title: str,
        detail: str, results: dict,
    ) -> None: ...


def notify_operator(
    title: str,
    detail: str,
    source: str,
    severity: _Severity = "error",
    dedup_key: str | None = None,
) -> dict[str, str]:
    """Send an operator-visible alert to every available channel.

    Returns a dict of {channel: status} so callers can log what happened.
    Never raises — the whole point is graceful degradation.

    Args:
        title: One-line headline ("Brain daemon cannot start").
        detail: Multi-line explanation + actionable fix.
        source: Which subsystem is raising this ("brain_daemon", "worker",
                "mcp_server", "cli:setup").
        severity: info|warning|error|critical.
        dedup_key: Stable identity for the CONDITION being paged (e.g.
            ``"prefect_queue_backlog"``, ``"operator_url:grafana"``).
            Repeats of the same key inside the page-cooldown window skip
            the external Telegram/Discord sends (see
            :func:`set_page_cooldown_seconds`). Defaults to
            ``"{source}|{title}"`` — callers whose titles embed variable
            text (ages, counts) should pass an explicit key or every
            repeat looks novel.
    """
    text = _fmt_message(title, detail, source, severity)
    results: dict[str, str] = {}

    cooldown_key = dedup_key or f"{source}|{title}"
    now = time.monotonic()
    suppressed = _cooldown_suppresses(cooldown_key, severity, now)
    if suppressed:
        text = f"{text}\n(suppressed repeat — page cooldown, key={cooldown_key})"

    # stderr always — visible to anyone watching a terminal or tail -f
    sys.stderr.write(f"\n{text}\n\n")
    sys.stderr.flush()

    # logger too — lands in whatever log aggregator is wired up. Log the
    # redacted text (not raw title/detail) so credential-shaped substrings
    # a caller pasted into detail never reach the aggregator either.
    {
        "info": logger.info,
        "warning": logger.warning,
        "error": logger.error,
        "critical": logger.critical,
    }.get(severity, logger.error)("[operator_notifier] %s", text)

    # Best-effort external channels.
    #
    # Telegram is critical-alert-only per feedback_telegram_vs_discord.
    # Sending warnings/errors to Telegram phone-pings the operator on
    # routine probe failures, which trains them to ignore Telegram —
    # the exact opposite of the channel's purpose. Discord is the
    # searchable spam channel that receives EVERYTHING above info
    # so the audit trail stays complete.
    #
    # Severity routing (added 2026-05-20 after PR #485 fixed the
    # Discord hydration and immediately surfaced this latent
    # spam-to-Telegram behavior — see finding #187):
    #
    # | severity | Telegram | Discord | alerts.log |
    # |----------|----------|---------|------------|
    # | critical | ✓        | ✓       | ✓          |
    # | error    | ✓        | ✓       | ✓          |
    # | warning  | ✗ skip   | ✓       | ✓          |
    # | info     | ✗ skip   | ✓       | ✓          |
    #
    # ``error`` still hits Telegram because it represents an
    # operator-actionable failure (something broke, please look). The
    # split is at the ``warning``/``error`` boundary — warnings are
    # signal-grade noise (recurring probe failures, drift detections),
    # errors are anomalies that need eyes.
    _TELEGRAM_SEVERITIES = {"error", "critical"}
    if suppressed:
        # Repeat of a condition we paged recently — skip BOTH external
        # channels. alerts.log below still records the (marked) repeat so
        # the local history stays complete, and the audit sink still runs
        # so suppressions are countable on the dashboards.
        tg_ok, tg_reason = False, "suppressed (page cooldown)"
        results["telegram"] = tg_reason
        dc_ok, dc_reason = False, "suppressed (page cooldown)"
        results["discord"] = dc_reason
    else:
        if severity in _TELEGRAM_SEVERITIES:
            tg_ok, tg_reason = _try_telegram(text)
            results["telegram"] = tg_reason
        else:
            tg_ok, tg_reason = False, "skipped (severity below error)"
            results["telegram"] = tg_reason

        dc_ok, dc_reason = _try_discord(text)
        results["discord"] = dc_reason

        # Start (or restart) the cooldown window only on a page that
        # actually went out to an external channel — a failed send should
        # not swallow the retry on the next cycle.
        if tg_ok or dc_ok:
            _LAST_PAGED_AT[cooldown_key] = now

    # Persistent local record — written even if Telegram/Discord succeed,
    # so you have a complete history even when external channels drop.
    log_ok, log_reason = _append_alerts_log(text)
    results["alerts_log"] = log_reason

    if not (tg_ok or dc_ok or log_ok):
        # If literally nothing worked, stderr is all we've got. At least
        # say so there so the operator knows the alert didn't reach them.
        sys.stderr.write(
            "WARNING: notify_operator could not reach Telegram, Discord, "
            "or the alerts log. Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID "
            "or DISCORD_OPS_WEBHOOK_URL so you hear about future failures.\n"
        )
        sys.stderr.flush()

    # Record the page in audit_log when a sink is wired up. The
    # silent-alerter watchdog reads these to distinguish "alerter is
    # broken" from "no alerts in the last N hours because nothing's
    # wrong". Wrapped in a broad except so a flaky sink can never
    # take down the notification path — by the time we get here,
    # the operator has already been paged via the external channels
    # above; audit recording is purely observability.
    if _NOTIFY_AUDIT_SINK is not None:
        try:
            _NOTIFY_AUDIT_SINK(
                source=source,
                severity=severity,
                title=title,
                detail=detail,
                results=results,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[operator_notifier] audit sink failed (page itself was "
                "sent): %s", e,
            )

    return results
