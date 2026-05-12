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
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
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


def _fmt_message(title: str, detail: str, source: str, severity: _Severity) -> str:
    return (
        f"{_severity_emoji(severity)} {title}\n"
        f"Source: {source}\n"
        f"Severity: {severity}\n"
        f"{detail}"
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
        ts = datetime.now(timezone.utc).isoformat()
        with _ALERTS_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {text}\n\n")
        return True, f"alerts.log ({_ALERTS_LOG})"
    except Exception as e:
        return False, f"alerts.log write failed: {e!r}"


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
_NOTIFY_AUDIT_SINK: "_NotifyAuditSink | None" = None


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
    """
    text = _fmt_message(title, detail, source, severity)
    results: dict[str, str] = {}

    # stderr always — visible to anyone watching a terminal or tail -f
    sys.stderr.write(f"\n{text}\n\n")
    sys.stderr.flush()

    # logger too — lands in whatever log aggregator is wired up
    {
        "info": logger.info,
        "warning": logger.warning,
        "error": logger.error,
        "critical": logger.critical,
    }.get(severity, logger.error)("[operator_notifier] %s: %s", title, detail)

    # Best-effort external channels — order matters: Telegram is the
    # loudest (push notification on Matt's phone), Discord is searchable
    # ops history.
    tg_ok, tg_reason = _try_telegram(text)
    results["telegram"] = tg_reason

    dc_ok, dc_reason = _try_discord(text)
    results["discord"] = dc_reason

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
