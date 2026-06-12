"""
Business Probes — operator-level monitoring that runs on the brain daemon cycle.

These are Glad Labs private probes, NOT part of Poindexter open source.
They implement the Probe interface from probe_interface.py.

Probes:
  - webhook_freshness: alert when revenue/subscriber webhooks go quiet
    (Glad-Labs/poindexter#27 follow-up — webhooks were wired with test
    rows on Apr 25 but no real provider deliveries since)
  - (future) email_triage: Gmail inbox scan, flag actionable items
  - (future) revenue_monitor: storefront sales + bank balance
"""

import inspect
import logging
import time
from datetime import UTC
from typing import Any

logger = logging.getLogger("brain.business_probes")


async def _maybe_await(value: Any) -> Any:
    """Await `value` when notify_fn returned a coroutine; pass through otherwise.

    Why: brain's production `notify` is async (since #344) but legacy tests
    pass `MagicMock()` which returns a non-awaitable. Without this shim the
    production call site emits `RuntimeWarning: coroutine 'notify' was never
    awaited` and the alert silently dies — the bug this helper closes.
    """
    if inspect.isawaitable(value):
        return await value
    return value

# Schedule tracking — brain runs every 5 min, probes run on their own intervals
_last_run: dict[str, float] = {}


def _is_due(probe_name: str, interval_minutes: int) -> bool:
    """Check if a probe is due to run based on its interval."""
    last = _last_run.get(probe_name, 0)
    return (time.time() - last) >= interval_minutes * 60


def _mark_run(probe_name: str):
    """Record that a probe just ran."""
    _last_run[probe_name] = time.time()


# ============================================================================
# WEBHOOK FRESHNESS — alert when revenue / subscriber tables go quiet
# ============================================================================
#
# Glad-Labs/poindexter#27 follow-up. Both Lemon Squeezy and Resend webhook
# handlers shipped (signature-verified, registered, idempotent). But each
# table has exactly one row from 2026-04-25 — the test fires at
# handler-wire time. No real provider deliveries since.
#
# Two ways that can be the system's fault:
async def _read_setting(pool, key: str, default: str) -> str:
    """Read an app_settings value with a typed default. Never raises."""
    try:
        value = await pool.fetchval(
            "SELECT value FROM app_settings "
            "WHERE key = $1 AND is_active = TRUE",
            key,
        )
    except Exception:
        return default
    if value is None:
        return default
    return str(value).strip() or default


async def _row_age_days(pool, table: str, column: str = "created_at") -> float | None:
    """Return age (in days) of newest row, or None if table is empty / errored."""
    try:
        last = await pool.fetchval(
            # Identifier interpolation OK — `table` and `column` are
            # caller-supplied literals, not user input. Bandit B608 is
            # satisfied because no $ params are involved.
            f"SELECT MAX({column}) FROM {table}",  # nosec B608
        )
    except Exception as e:
        logger.debug("[BUSINESS_PROBE] _row_age_days(%s.%s) failed: %s", table, column, e)
        return None
    if last is None:
        # Empty table — return very-large sentinel so threshold comparison
        # treats it as "long since last delivery".
        return float("inf")
    import datetime as _dt
    now = _dt.datetime.now(_dt.UTC)
    if last.tzinfo is None:
        last = last.replace(tzinfo=_dt.UTC)
    delta = now - last
    return delta.total_seconds() / 86400.0


async def probe_webhook_freshness(pool, notify_fn) -> dict:
    """Check that revenue_events + subscriber_events tables are seeing fresh rows.

    Every ``probe_webhook_freshness_interval_minutes`` (default 24h) the
    probe queries the newest row in each table. If either is older than
    its configured threshold, send an operator notification with a
    pointer to the provider admin URL the human should verify.

    Best-effort: never raises, returns ``{"ok": False, "detail": ...}``
    on internal error so the brain cycle can keep going.
    """
    enabled = (await _read_setting(pool, "probe_webhook_freshness_enabled", "true")).lower()
    if enabled in ("false", "0", "no", "off"):
        return {"ok": True, "detail": "disabled via app_settings"}

    interval = int(await _read_setting(
        pool, "probe_webhook_freshness_interval_minutes", "1440",
    ) or 1440)
    if not _is_due("webhook_freshness", interval):
        return {"ok": True, "detail": "not due yet"}

    revenue_threshold_days = float(await _read_setting(
        pool, "webhook_freshness_revenue_threshold_days", "30",
    ) or 30)
    subscriber_threshold_days = float(await _read_setting(
        pool, "webhook_freshness_subscriber_threshold_days", "7",
    ) or 7)

    revenue_age = await _row_age_days(pool, "revenue_events")
    subscriber_age = await _row_age_days(pool, "subscriber_events")

    alerts: list[str] = []
    if revenue_age is None:
        # Table missing / query error. Don't spam — debug log only.
        logger.debug("[BUSINESS_PROBE] revenue_events not queryable — skipping")
    elif revenue_age >= revenue_threshold_days:
        if revenue_age == float("inf"):
            age_str = "ever (table empty)"
        else:
            age_str = f"{revenue_age:.1f}d"
        alerts.append(
            f"revenue_events: no row in {age_str} (threshold "
            f"{revenue_threshold_days:.0f}d). Verify Lemon Squeezy webhook "
            "config: https://app.lemonsqueezy.com/settings/webhooks"
        )

    if subscriber_age is None:
        logger.debug("[BUSINESS_PROBE] subscriber_events not queryable — skipping")
    elif subscriber_age >= subscriber_threshold_days:
        if subscriber_age == float("inf"):
            age_str = "ever (table empty)"
        else:
            age_str = f"{subscriber_age:.1f}d"
        alerts.append(
            f"subscriber_events: no row in {age_str} (threshold "
            f"{subscriber_threshold_days:.0f}d). Verify Resend webhook "
            "config: https://resend.com/webhooks"
        )

    _mark_run("webhook_freshness")

    if not alerts:
        logger.info(
            "[BUSINESS_PROBE] webhook_freshness OK — revenue_age=%s "
            "subscriber_age=%s",
            "—" if revenue_age is None else f"{revenue_age:.1f}d",
            "—" if subscriber_age is None else f"{subscriber_age:.1f}d",
        )
        return {"ok": True, "detail": "all webhook tables fresh"}

    body = (
        "WEBHOOK QUIET — provider deliveries appear to have stopped.\n\n"
        + "\n\n".join(alerts)
        + "\n\nOperator action: verify the provider admin pages above. "
        "If config is correct and the absence is real (no sales / no "
        "sends), tighten or loosen the thresholds via "
        "`poindexter settings set webhook_freshness_*_threshold_days`."
    )
    try:
        await _maybe_await(notify_fn(body))
    except Exception as e:
        logger.warning("[BUSINESS_PROBE] notify_fn failed: %s", e)
    logger.warning("[BUSINESS_PROBE] webhook_freshness fired %d alert(s)", len(alerts))
    return {"ok": True, "detail": f"fired {len(alerts)} alert(s)", "alerts": alerts}


# ============================================================================
# SILENT-ALERTER META-WATCHDOG — does the alerter itself still work?
# ============================================================================
#
# Matt 2026-05-12 05:25 UTC: "If we find silent failures we should add at
# least a way to make it fail loud, ideally make it self healing." This
# probe is the meta-failure case: the whole monitoring chain looks
# healthy (probes return ok, brain cycles), but no alerts have been
# raised in N hours despite real production breakage upstream
# (R2 publish broken 4 days, media gen broken 13 days — both eventually
# noticed by Matt by eye, not by Telegram). Two ways this happens:
#
#   1. Grafana → webhook → alert_events ingestion is broken (token
#      empty, contact point misconfigured, webhook URL stale).
#   2. The brain dispatcher itself died silently and stopped polling.
#
# The probe doesn't try to fix the underlying chain — that's case-by-
# case. It just pages the operator that "the alerter has been quiet
# for N hours while X probes are failing", which is the load-bearing
# signal: 0 alerts in a healthy system is fine; 0 alerts while probes
# are red is a self-silencing failure.

async def probe_silent_alerter(pool, notify_fn) -> dict:
    """Page if no alert_events have arrived in N hours AND probes are red.

    Cadence is governed by ``silent_alerter_probe_interval_minutes``
    (default 60) — there's no value in running this more often than
    the alert-staleness threshold. The threshold itself is
    ``silent_alerter_quiet_hours`` (default 6).

    Self-healing is OUT OF SCOPE: the upstream causes (Grafana
    misconfig, dead webhook target, dispatcher crash) are case-by-case
    and need a human to decide what to fix. The probe's job is to
    make sure the operator *finds out*.
    """
    interval_minutes = await _setting_int(
        pool, "silent_alerter_probe_interval_minutes", 60,
    )
    if not _is_due("silent_alerter", interval_minutes):
        return {"ok": True, "detail": "not due yet"}
    _mark_run("silent_alerter")

    if not await _setting_bool(pool, "silent_alerter_probe_enabled", True):
        return {"ok": True, "detail": "disabled via app_settings"}

    quiet_hours = await _setting_int(pool, "silent_alerter_quiet_hours", 6)

    # Two alert delivery paths to check:
    #
    #   1. ``alert_events.received_at``   — Grafana → webhook → brain
    #      dispatcher pipeline. This is the table the original watchdog
    #      v1 looked at.
    #   2. ``audit_log.timestamp WHERE event_type='operator_paged'``  —
    #      direct ``notify_operator()`` calls from brain probes. The
    #      v1 watchdog couldn't see these and produced a false-positive
    #      on 2026-05-12 15:29 UTC (compose drift was firing pages every
    #      cycle but the watchdog thought the alerter was dead).
    #
    # We treat "last alert" as the MAX over both paths so either delivery
    # mechanism counts as proof the alerter is alive.
    try:
        last_received = await pool.fetchval(
            "SELECT MAX(received_at) FROM alert_events"
        )
        last_paged = await pool.fetchval(
            """
            SELECT MAX(timestamp) FROM audit_log
             WHERE event_type = 'operator_paged'
            """
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[BUSINESS_PROBE] silent_alerter DB read failed: %s", e)
        return {"ok": False, "detail": f"db read failed: {e}"}

    from datetime import datetime
    now = datetime.now(UTC)
    candidates = [t for t in (last_received, last_paged) if t is not None]
    last_signal = max(candidates) if candidates else None
    if last_signal is None:
        # Neither delivery path has ever recorded a page. Only worth
        # flagging when probes are also failing — fresh installs and
        # genuinely-healthy systems shouldn't get a false alarm.
        quiet_hours_actual = 24 * 365  # effectively infinite
    else:
        quiet_hours_actual = (now - last_signal).total_seconds() / 3600

    if quiet_hours_actual < quiet_hours:
        return {
            "ok": True,
            "detail": f"recent alert {quiet_hours_actual:.1f}h ago < {quiet_hours}h threshold",
        }

    # Quiet — now correlate with probe state. If the system is genuinely
    # idle (no page-worthy probe failures), this is fine. Only page when
    # "quiet + red", where "red" means a severity that SHOULD have paged.
    #
    # 2026-05-23 tightening (Matt feedback after a false alarm): only
    # ERROR/CRITICAL probe events count as "should have paged". Warning-
    # severity probes intentionally don't trigger notify_operator() —
    # they're informational (e.g. ``probe.migration_drift_detected``
    # which fires every 5 min while a pending migration waits for the
    # next worker restart). Counting warnings here produced a false
    # alarm on 2026-05-23 08:46 UTC when only migration drift was active.
    try:
        recent_failures = await pool.fetch(
            """
            SELECT DISTINCT event_type, severity
            FROM audit_log
            WHERE timestamp > NOW() - INTERVAL '1 hour'
              AND severity IN ('error', 'critical')
              AND event_type LIKE 'probe.%'
            """
        )
        # Surfaced separately in the detail string so we can tell the
        # operator "things are quiet but only at warning severity" —
        # useful diagnostic without paging.
        recent_warnings = await pool.fetch(
            """
            SELECT DISTINCT event_type
            FROM audit_log
            WHERE timestamp > NOW() - INTERVAL '1 hour'
              AND severity = 'warning'
              AND event_type LIKE 'probe.%'
            """
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[BUSINESS_PROBE] silent_alerter probe-state read failed: %s", e
        )
        return {"ok": False, "detail": f"db read failed: {e}"}

    failure_count = len(recent_failures)
    warning_count = len(recent_warnings)
    if failure_count == 0:
        # Quiet + no page-worthy failures = healthy. If there are warnings
        # still firing, name them in the detail so the diagnostic isn't
        # silent about what IS active.
        if warning_count:
            warning_names = sorted({dict(r)["event_type"] for r in recent_warnings})
            return {
                "ok": True,
                "detail": (
                    f"quiet {quiet_hours_actual:.1f}h with only warning-"
                    f"severity probes active ({warning_count} type(s): "
                    f"{', '.join(warning_names[:5])}"
                    + (", …" if len(warning_names) > 5 else "")
                    + ") — informational, not page-worthy, no alarm"
                ),
            }
        return {
            "ok": True,
            "detail": (
                f"quiet {quiet_hours_actual:.1f}h but no probe failures — "
                f"system is genuinely idle, no page"
            ),
        }

    failure_names = sorted({dict(r)["event_type"] for r in recent_failures})
    body = (
        f"⚠️ ALERTER APPEARS SILENT — meta-watchdog fired.\n\n"
        f"Last alert_event received: {quiet_hours_actual:.1f}h ago "
        f"(threshold {quiet_hours}h).\n"
        f"ERROR/CRITICAL probe failures in last 1h: {failure_count} "
        f"distinct event types:\n"
        f"  - " + "\n  - ".join(failure_names[:8])
        + ("\n  - …" if len(failure_names) > 8 else "")
        + "\n\nThis is the silent-failure pattern: probes that SHOULD page "
        "are red but no Telegram/Discord pages have fired. Likely causes:\n"
        "  1. Brain alert_dispatch_loop crashed silently — check\n"
        "     `docker logs poindexter-brain-daemon | grep dispatcher`\n"
        "  2. notify_operator() target misconfigured (telegram_bot_token /\n"
        "     telegram_chat_id / discord_ops_webhook_url in app_settings)\n"
        "  3. Grafana → webhook ingestion broken (token? URL stale?) —\n"
        "     only relevant if the failing probes flow through alert_events\n"
        "  4. All real failures suppressed by dedup window — check\n"
        "     alert_events.dispatch_result for 'suppressed: …'\n"
    )
    try:
        await _maybe_await(notify_fn(body))
    except Exception as e:
        logger.warning("[BUSINESS_PROBE] silent_alerter notify_fn failed: %s", e)
    logger.warning(
        "[BUSINESS_PROBE] silent_alerter PAGED — quiet=%.1fh, probe_failures=%d",
        quiet_hours_actual, failure_count,
    )
    return {
        "ok": True,
        "detail": f"paged: quiet={quiet_hours_actual:.1f}h failures={failure_count}",
        "quiet_hours": quiet_hours_actual,
        "probe_failure_count": failure_count,
    }


async def _setting_int(pool, key: str, default: int) -> int:
    """Read an int-valued app_settings key. Returns ``default`` on miss / parse fail."""
    try:
        raw = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1 AND is_active = TRUE",
            key,
        )
        if raw is None or str(raw).strip() == "":
            return default
        return int(str(raw).strip())
    except Exception:  # noqa: BLE001
        return default


async def _setting_bool(pool, key: str, default: bool) -> bool:
    """Read a bool-valued app_settings key (``"true"``/``"false"``)."""
    try:
        raw = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1 AND is_active = TRUE",
            key,
        )
        if raw is None:
            return default
        return str(raw).strip().lower() == "true"
    except Exception:  # noqa: BLE001
        return default


# ============================================================================
# RUNNER — called from brain daemon's run_cycle
# ============================================================================

async def run_business_probes(pool, notify_fn) -> dict:
    """Run all business probes. Called every brain cycle (5 min).

    Each probe manages its own schedule internally.
    """
    results = {}

    results["webhook_freshness"] = await probe_webhook_freshness(pool, notify_fn)
    results["silent_alerter"] = await probe_silent_alerter(pool, notify_fn)

    # Future probes:
    # results["email_triage"] = await probe_email_triage(pool, notify_fn)
    # results["revenue_monitor"] = await probe_revenue_monitor(pool, notify_fn)

    return results
