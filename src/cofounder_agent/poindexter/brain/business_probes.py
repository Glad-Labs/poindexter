"""
Business Probes — operator-level monitoring that runs on the brain daemon cycle.

Plain ``(pool, notify_fn, *, info_fn=None) -> dict`` functions called by
``run_business_probes`` each cycle; each gates itself on its own interval,
and ``probe_severity`` decides whether its finding pages (``notify_fn``) or
is a Discord notice (``info_fn``).

Probes:
  - webhook_freshness: alert when revenue_events / subscriber_events stop
    receiving rows (Glad-Labs/poindexter#27 follow-up)
  - silent_alerter: page when no alert has gone out for hours while probes
    are failing — the alerting path itself may be dead
"""

import inspect
import logging
from datetime import UTC
from typing import Any

from poindexter.brain import probe_schedule, probe_severity

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


# Brain runs every 5 min, probes run on their own intervals. Last-run times
# survive a brain restart (see probe_schedule.py).
def _is_due(probe_name: str, interval_minutes: int) -> bool:
    """Check if a probe is due to run based on its interval."""
    return probe_schedule.schedule.is_due(probe_name, interval_minutes * 60)


async def _mark_run(pool, probe_name: str) -> None:
    """Record that a probe just ran, in memory and in brain_knowledge."""
    await probe_schedule.schedule.mark_run(pool, probe_name)


# ============================================================================
# WEBHOOK FRESHNESS — alert when revenue / subscriber tables go quiet
# ============================================================================
#
# Glad-Labs/poindexter#27 follow-up. A quiet table has two causes that are
# the system's fault — its producer stopped, or rows are dropped on the way
# in — and one that isn't: no sales or sends happened. This probe can't tell
# them apart; it makes the silence visible and names each table's producer,
# so the operator can. Both producers are polls now, not the webhook routes,
# which are unreachable from the internet.


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
        logger.warning(
            "[BUSINESS_PROBE] _row_age_days(%s.%s) failed: %s — skipping this "
            "table's freshness check this run",
            table, column, e,
        )
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


async def probe_webhook_freshness(pool, notify_fn, *, info_fn=None) -> dict:
    """Check that revenue_events + subscriber_events tables are seeing fresh rows.

    Every ``probe_webhook_freshness_interval_minutes`` (default 24h) the
    probe queries the newest row in each table. If either is older than
    its configured threshold, send an operator notification with a
    pointer to the provider admin URL the human should verify.

    A quiet webhook is a business/SEO-adjacent signal, not an outage
    (``probe_severity.PROBE_DEFAULT_SEVERITY["webhook_freshness"]`` is the
    default "warning") — it goes through ``info_fn`` (Discord), falling
    back to ``notify_fn`` when ``info_fn`` is omitted, exactly like
    ``health_probes.run_health_probes``'s notices. 31 pages in the 30 days
    to 2026-09-25, all within 15 minutes of a brain restart: ``_is_due``'s
    schedule was in-process until #4116 persisted it (probe_schedule.py),
    so every restart re-ran the "daily" check. Repeats, not 31 new
    findings.

    Best-effort: never raises, returns ``{"ok": False, "detail": ...}``
    on internal error so the brain cycle can keep going.
    """
    enabled = (await _read_setting(pool, "probe_webhook_freshness_enabled", "true")).lower()
    if enabled in ("false", "0", "no", "off"):
        return {"ok": True, "detail": "disabled via app_settings"}

    interval = int(await _read_setting(
        pool, "probe_webhook_freshness_interval_minutes", "1440",
    ) or 1440)
    await probe_schedule.schedule.load(pool)
    if not _is_due("webhook_freshness", interval):
        return {"ok": True, "detail": "not due yet"}

    revenue_threshold_days = float(await _read_setting(
        pool, "webhook_freshness_revenue_threshold_days", "30",
    ) or 30)
    subscriber_threshold_days = float(await _read_setting(
        pool, "webhook_freshness_subscriber_threshold_days", "7",
    ) or 7)
    # Gated because the check is only meaningful once SOMETHING writes
    # revenue_events on a cadence. That became true 2026-09-22: the
    # pro_delivery invoice poll (stack#3954) writes one row per Lemon
    # Squeezy charge, and checkout is live. Before that the table held a
    # single test-wiring row and this half re-fired a misleading "verify
    # your webhook config" alert every time the threshold was raised far
    # enough to buy a few months (30d -> 90d happened once that way).
    #
    # Raising a threshold to silence a probe converts it into a no-op
    # while every config surface still reads "enabled" — so if this fires
    # and the absence is real, the answer is to fix the producer or turn
    # the check off deliberately, NOT to buy another 90 days.
    revenue_check_enabled = (await _read_setting(
        pool, "probe_webhook_freshness_revenue_check_enabled", "false",
    )).lower() not in ("false", "0", "no", "off")

    revenue_age = (
        await _row_age_days(pool, "revenue_events") if revenue_check_enabled else None
    )
    subscriber_age = await _row_age_days(pool, "subscriber_events")

    alerts: list[str] = []
    if not revenue_check_enabled:
        logger.debug(
            "[BUSINESS_PROBE] revenue_events freshness check disabled "
            "(probe_webhook_freshness_revenue_check_enabled=false) — "
            "revenue isn't live yet, set true once real transactions flow"
        )
    elif revenue_age is None:
        # Table missing / query error. Don't spam — debug log only.
        logger.debug("[BUSINESS_PROBE] revenue_events not queryable — skipping")
    elif revenue_age >= revenue_threshold_days:
        if revenue_age == float("inf"):
            age_str = "ever (table empty)"
        else:
            age_str = f"{revenue_age:.1f}d"
        alerts.append(
            f"revenue_events: no row in {age_str} (threshold "
            f"{revenue_threshold_days:.0f}d). The producer is the "
            "pro_delivery invoice poll (LS GET /v1/subscription-invoices, "
            "every 5 min) — check the sync_pro_subscriptions job run "
            "metrics first, NOT the webhook config: the "
            "/api/webhooks/lemon-squeezy route is unreachable from the "
            "internet and has never fired. With checkout live, no row in "
            "this window means either no sales or a broken poll, and the "
            "job's invoices_seen metric tells you which."
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
            f"{subscriber_threshold_days:.0f}d). The producer is the "
            "sync_resend_delivery job (Resend GET /emails, hourly) — check "
            "its run metrics first, NOT the webhook config: the "
            "/api/webhooks/resend route is unreachable from the internet. "
            "No row in this window means either no newsletter sends or a "
            "broken poll."
        )

    await _mark_run(pool, "webhook_freshness")

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
        + "\n\nOperator action: check the PRODUCER named above before the "
        "provider admin page — a stalled poll and a genuine absence look "
        "identical from this table alone. If the absence is real (no "
        "sales / no sends), the honest fix is to restore the producer or "
        "disable that half of the check deliberately; raising "
        "`webhook_freshness_*_threshold_days` just buys silence and "
        "leaves every config surface still reading \"enabled\"."
    )
    try:
        sender = await probe_severity.sender_for(pool, "webhook_freshness", notify_fn, info_fn)
        await _maybe_await(sender(body))
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

async def probe_silent_alerter(pool, notify_fn, *, info_fn=None) -> dict:
    """Page if no alert_events have arrived in N hours AND probes are red.

    Cadence is governed by ``silent_alerter_probe_interval_minutes``
    (default 60) — there's no value in running this more often than
    the alert-staleness threshold. The threshold itself is
    ``silent_alerter_quiet_hours`` (default 6).

    Self-healing is OUT OF SCOPE: the upstream causes (Grafana
    misconfig, dead webhook target, dispatcher crash) are case-by-case
    and need a human to decide what to fix. The probe's job is to
    make sure the operator *finds out*.

    Always pages ``notify_fn``. ``info_fn`` is accepted so
    ``run_business_probes`` can pass it to every probe, and deliberately
    unused. ``probe_severity.PROBE_DEFAULT_SEVERITY`` classifies
    ``silent_alerter`` "critical" on purpose: it fires only when probes
    are red and no alert has gone out for hours, so the paging path itself
    may be broken. That is every page going missing at once, and it is
    worth using both channels to say so.
    """
    interval_minutes = await _setting_int(
        pool, "silent_alerter_probe_interval_minutes", 60,
    )
    await probe_schedule.schedule.load(pool)
    if not _is_due("silent_alerter", interval_minutes):
        return {"ok": True, "detail": "not due yet"}
    await _mark_run(pool, "silent_alerter")

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

async def run_business_probes(pool, notify_fn, *, info_fn=None) -> dict:
    """Run all business probes. Called every brain cycle (5 min).

    Each probe manages its own schedule internally. ``info_fn`` is passed
    through to each probe uniformly; ``webhook_freshness`` (warning) uses
    it, ``silent_alerter`` (critical) ignores it and always pages — see
    each probe's own docstring.
    """
    results = {}

    results["webhook_freshness"] = await probe_webhook_freshness(pool, notify_fn, info_fn=info_fn)
    results["silent_alerter"] = await probe_silent_alerter(pool, notify_fn, info_fn=info_fn)

    # Future probes:
    # results["email_triage"] = await probe_email_triage(pool, notify_fn)
    # results["revenue_monitor"] = await probe_revenue_monitor(pool, notify_fn)

    return results
