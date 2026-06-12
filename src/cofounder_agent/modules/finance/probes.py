"""Finance brain probe — flags a stalled Mercury poll (Glad-Labs/poindexter#565).

The hourly :class:`modules.finance.jobs.poll_mercury.PollMercuryJob` had no
brain probe — the ``FinanceModule.register_probes`` docstring literally
TODO'd this one. So a silent stall of the Mercury poll (worker wedged,
scheduler missed the tick, Mercury auth lost) surfaced on no dashboard and
paged no one. This probe closes that gap as the brain-side complement to the
``poindexter_finance_last_poll_success_timestamp_seconds`` Prometheus metric
(see ``modules/finance/metrics.py``): the metric drives the Grafana /
Alertmanager path for operators who run the observability stack; this probe
pages operators who only run the brain daemon.

Two failure modes, both DB-configurable (no hardcoded interval), both routed
through the existing operator-alert path:

1. **Stale poll** — no ``status='ok'`` row in ``finance_poll_runs`` within
   ``finance_poll_stale_multiplier`` × ``finance_poll_interval_seconds``
   (defaults 3 × 3600 = 3h). The multiplier × interval framing means the
   threshold tracks the poll cadence automatically: dial the poll down to
   30m and the stale window halves without a second setting to touch.
2. **Auth lost** — the most recent run terminated ``auth_failed``. A revoked
   / expired Mercury token never "stalls" (the job runs, fails fast, records
   the row), so the staleness check alone would miss it. This is a distinct,
   higher-signal page.

Routing: like every other brain probe (``brain/prefect_stuck_flow_probe.py``,
``brain/gate_pending_summary_probe.py``) the page goes through
``notify_operator`` (Telegram for critical, Discord for warning) and leaves
an ``audit_log`` row for the findings dashboard / daily roll-up. The probe is
registered on the worker-side ``BrainProbeRegistry`` via
``FinanceModule.register_probes`` so it shows up in ``/api/modules/probes``;
its ``check()`` returns a brain ``ProbeResult`` so it slots into the
registry-driven execution path the same way ``PrefectStuckFlowProbe`` does.

Standalone-friendly: reads ``finance_poll_runs`` + ``app_settings`` with
direct SQL only (no ``MercuryClient`` / worker imports), so the brain daemon
— which cannot import the FastAPI codebase — can execute it once the
module-probe bridge lands. ``notify_operator`` is imported lazily / via the
``notify_fn`` seam so unit tests don't need the brain dependency chain.

Disabled by default off ``mercury_enabled``: a deployment that doesn't bank
with Mercury (the public-OSS default) never pages on a poll that, correctly,
never runs.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("finance.poll_staleness_probe")


# ---------------------------------------------------------------------------
# DB-configurable tunables — every threshold lives in app_settings so the
# operator tunes without redeploying (config-in-DB principle). Defaults are
# seeded by migration 20260603_000000_seed_finance_poll_observability.py and
# duplicated here so the probe behaves sanely even before the seed runs.
# ---------------------------------------------------------------------------

ENABLED_KEY = "finance_poll_staleness_probe_enabled"
POLL_INTERVAL_SECONDS_KEY = "finance_poll_interval_seconds"
STALE_MULTIPLIER_KEY = "finance_poll_stale_multiplier"

DEFAULT_ENABLED = True
DEFAULT_POLL_INTERVAL_SECONDS = 3600  # PollMercuryJob.schedule = "every 1 hour"
DEFAULT_STALE_MULTIPLIER = 3.0  # tolerate up to 3 missed hourly ticks

# Same 5-minute cadence as the sibling brain probes.
PROBE_INTERVAL_SECONDS = 300


_TRUTHY = {"true", "1", "yes", "on"}
_FALSEY = {"false", "0", "no", "off"}


async def _read_setting(pool: Any, key: str, default: str) -> str:
    """Read a string app_settings value. Never raises — degrades to the
    default on a missing row or a DB hiccup (mirrors the sibling probes)."""
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[FINANCE_POLL] could not read %s from app_settings: %s "
            "— using default %r",
            key, exc, default,
        )
        return default
    if val is None:
        return default
    return str(val)


async def _read_float(pool: Any, key: str, default: float) -> float:
    raw = (await _read_setting(pool, key, str(default))).strip()
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return default
    # A non-positive threshold would make every poll instantly "stale" —
    # treat it as a misconfiguration and fall back to the default rather
    # than page continuously.
    return parsed if parsed > 0 else default


async def _read_bool(pool: Any, key: str, default: bool) -> bool:
    raw = (await _read_setting(
        pool, key, "true" if default else "false"
    )).strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSEY:
        return False
    return default


async def _mercury_enabled(pool: Any) -> bool:
    """Gate on the same master switch the poll job uses."""
    raw = (await _read_setting(pool, "mercury_enabled", "false")).strip().lower()
    return raw in _TRUTHY


async def _emit_audit_event(
    pool: Any,
    event_type: str,
    detail: str,
    *,
    severity: str = "warning",
    payload: dict[str, Any] | None = None,
) -> None:
    """Write an ``audit_log`` row for the findings dashboard / daily
    roll-up. Best-effort — observability writes never fail the probe."""
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity) "
            "VALUES ($1, 'finance.poll_staleness_probe', $2::jsonb, $3)",
            event_type,
            json.dumps({"detail": detail, **(payload or {})}),
            severity,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[FINANCE_POLL] audit_log insert skipped (%s)", exc)


NotifyFn = Callable[..., Any]


def _default_notify_fn() -> NotifyFn | None:
    """Resolve ``brain.operator_notifier.notify_operator`` lazily.

    The dual flat / package import mirrors the brain probes' container vs.
    test contexts. Returns ``None`` when neither path resolves (e.g. a
    worker-side unit run without the brain on sys.path) — the caller then
    only writes the audit row + logs, which is the safe degraded behaviour.
    """
    try:  # flat import when brain/ is on sys.path (container runtime)
        from operator_notifier import notify_operator  # type: ignore

        return notify_operator
    except ImportError:
        pass
    try:  # pragma: no cover — package-qualified path for the worker side
        from brain.operator_notifier import notify_operator  # type: ignore

        return notify_operator
    except ImportError:
        return None


async def run_finance_poll_staleness_probe(
    pool: Any,
    *,
    notify_fn: NotifyFn | None = None,
    now_epoch_fn: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Single execution of the finance poll-staleness probe.

    Args:
        pool: asyncpg pool for ``app_settings`` + ``finance_poll_runs`` +
            ``audit_log``.
        notify_fn: operator notifier (defaults to
            :func:`brain.operator_notifier.notify_operator`). Tests inject a
            spy. When ``None`` and the default can't be resolved, the probe
            still writes the audit row + logs — it just can't page.
        now_epoch_fn: ``() -> unix epoch float`` — defaults to wall clock.
            Tests inject a fixed clock so staleness math is deterministic.

    Returns a structured summary suitable for a brain ``probe_results`` map.
    """
    import time

    now_epoch_fn = now_epoch_fn or time.time

    if not await _read_bool(pool, ENABLED_KEY, DEFAULT_ENABLED):
        return {
            "ok": True,
            "status": "disabled",
            "stale": False,
            "auth_lost": False,
            "detail": f"Probe disabled via app_settings.{ENABLED_KEY}",
        }

    # A deployment with Mercury off never polls — don't page on the absence
    # of a poll that correctly isn't running.
    if not await _mercury_enabled(pool):
        return {
            "ok": True,
            "status": "mercury_disabled",
            "stale": False,
            "auth_lost": False,
            "detail": "mercury_enabled=false — finance poll is a no-op; "
                      "nothing to page on.",
        }

    interval_seconds = await _read_float(
        pool, POLL_INTERVAL_SECONDS_KEY, float(DEFAULT_POLL_INTERVAL_SECONDS)
    )
    multiplier = await _read_float(
        pool, STALE_MULTIPLIER_KEY, DEFAULT_STALE_MULTIPLIER
    )
    stale_threshold_seconds = interval_seconds * multiplier

    # Pull the freshest successful poll + the most recent run's terminal
    # status in one round-trip.
    try:
        row = await pool.fetchrow(
            """
            SELECT
                EXTRACT(EPOCH FROM (
                    SELECT MAX(started_at) FROM finance_poll_runs
                    WHERE status = 'ok'
                )) AS last_success_epoch,
                (
                    SELECT status FROM finance_poll_runs
                    ORDER BY started_at DESC LIMIT 1
                ) AS latest_status
            """
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[FINANCE_POLL] finance_poll_runs query failed: %s", exc)
        return {
            "ok": False,
            "status": "query_failed",
            "stale": False,
            "auth_lost": False,
            "detail": f"finance_poll_runs query failed: "
                      f"{type(exc).__name__}: {str(exc)[:160]}",
        }

    last_success_epoch = row["last_success_epoch"] if row else None
    latest_status = (row["latest_status"] if row else None) or "none"
    now = now_epoch_fn()

    # ----- Auth-lost detection (distinct, higher-signal) -----
    auth_lost = latest_status == "auth_failed"

    # ----- Staleness detection -----
    if last_success_epoch is None:
        # Mercury is enabled but no poll has EVER succeeded. That's a stall
        # from the operator's point of view — they turned it on and it has
        # produced nothing.
        stale = True
        age_seconds: float | None = None
    else:
        age_seconds = max(0.0, now - float(last_success_epoch))
        stale = age_seconds > stale_threshold_seconds

    effective_notify = notify_fn if notify_fn is not None else _default_notify_fn()

    paged = False
    if stale or auth_lost:
        if auth_lost:
            severity = "critical"
            title = "Mercury finance poll: auth lost"
            detail = (
                "The most recent Mercury poll terminated auth_failed — the "
                "Read-Only API token is likely revoked or expired. Balances + "
                "transactions are going stale. Re-mint at Mercury dashboard → "
                "Settings → API and run `poindexter settings set "
                "mercury_api_token <token> --secret`."
            )
            event_type = "finance.poll_auth_lost"
            payload = {"latest_status": latest_status}
        else:
            severity = "warning"
            if age_seconds is None:
                age_str = "never (no successful poll on record)"
            else:
                age_str = f"{age_seconds / 3600.0:.1f}h ago"
            title = "Mercury finance poll stalled"
            detail = (
                f"No successful Mercury poll within "
                f"{stale_threshold_seconds / 3600.0:.1f}h "
                f"({multiplier:g}× the {interval_seconds / 3600.0:.1f}h poll "
                f"interval). Last success: {age_str}; latest run status: "
                f"{latest_status}. Check the worker / PluginScheduler and "
                f"`/api/finance/healthcheck`. Tune the window via "
                f"`poindexter settings set {STALE_MULTIPLIER_KEY} <n>`."
            )
            event_type = "finance.poll_stale"
            payload = {
                "age_seconds": age_seconds,
                "stale_threshold_seconds": stale_threshold_seconds,
                "interval_seconds": interval_seconds,
                "multiplier": multiplier,
                "latest_status": latest_status,
            }

        await _emit_audit_event(
            pool, event_type, detail, severity=severity, payload=payload
        )

        if effective_notify is not None:
            try:
                result = effective_notify(
                    title=title,
                    detail=detail,
                    source="finance.poll_staleness_probe",
                    severity=severity,
                )
                # notify_operator is async on the brain side (#344); await it
                # when it returned a coroutine, pass through for sync test spies.
                import inspect

                if inspect.isawaitable(result):
                    await result
                paged = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("[FINANCE_POLL] notify_fn failed: %s", exc)
        else:
            logger.warning(
                "[FINANCE_POLL] %s — %s (no notifier resolved; audit row "
                "written)", title, detail,
            )

    status = (
        "auth_lost" if auth_lost
        else ("stale" if stale else "fresh")
    )
    summary = {
        "ok": True,
        "status": status,
        "stale": stale,
        "auth_lost": auth_lost,
        "paged": paged,
        "age_seconds": age_seconds,
        "stale_threshold_seconds": stale_threshold_seconds,
        "interval_seconds": interval_seconds,
        "multiplier": multiplier,
        "latest_status": latest_status,
        "detail": (
            f"status={status}, last_success="
            f"{'never' if last_success_epoch is None else f'{(age_seconds or 0)/3600.0:.1f}h ago'}, "
            f"threshold={stale_threshold_seconds/3600.0:.1f}h, paged={paged}"
        ),
    }
    if stale or auth_lost:
        logger.warning("[FINANCE_POLL] %s", summary["detail"])
    else:
        logger.debug("[FINANCE_POLL] %s", summary["detail"])
    return summary


# ---------------------------------------------------------------------------
# Probe-Protocol adapter — slots into the brain's registry-driven path the
# same way PrefectStuckFlowProbe / GatePendingSummaryProbe do. Registered on
# the worker-side BrainProbeRegistry by FinanceModule.register_probes so it
# also appears in /api/modules/probes.
# ---------------------------------------------------------------------------


class FinancePollStalenessProbe:
    """Brain ``Probe``-Protocol wrapper around
    :func:`run_finance_poll_staleness_probe`."""

    name: str = "poll_staleness"
    description: str = (
        "Pages when the hourly Mercury finance poll stalls (no successful "
        "run within finance_poll_stale_multiplier × finance_poll_interval_seconds, "
        "default 3×3600=3h) or the latest run lost Mercury auth. Routes through "
        "notify_operator + audit_log like the other brain probes. Gated by "
        "mercury_enabled, so a non-Mercury deployment never pages."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        del config  # thresholds come from app_settings, not registry config
        summary = await run_finance_poll_staleness_probe(pool)
        try:  # brain ProbeResult — dual import for container vs. test
            from probe_interface import ProbeResult  # type: ignore
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult  # type: ignore

        fired = bool(summary.get("stale") or summary.get("auth_lost"))
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", ""),
            metrics={
                k: summary[k]
                for k in (
                    "status",
                    "stale",
                    "auth_lost",
                    "paged",
                    "age_seconds",
                    "stale_threshold_seconds",
                    "latest_status",
                )
                if k in summary
            },
            severity=(
                "critical" if summary.get("auth_lost")
                else ("warning" if fired else "info")
            ),
        )


__all__ = [
    "run_finance_poll_staleness_probe",
    "FinancePollStalenessProbe",
    "ENABLED_KEY",
    "POLL_INTERVAL_SECONDS_KEY",
    "STALE_MULTIPLIER_KEY",
    "PROBE_INTERVAL_SECONDS",
]
