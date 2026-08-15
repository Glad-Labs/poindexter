"""Tap runner — walk enabled external_taps rows and invoke each handler.

Same shape as :mod:`retention_runner`: one scheduled entry point,
per-row isolation so a bad tap doesn't kill the run, success/failure
counters recorded on the row.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from services.integrations import registry

logger = logging.getLogger(__name__)

# Per-handler wall-clock budget (seconds); DB-tunable via
# app_settings.tap_handler_timeout_seconds. NOT the same knob as
# ``tap_run_timeout_seconds``, which bounds the *embedding* taps in
# services/taps/runner.py — this one bounds the external_taps handlers
# dispatched below. Generous: a full walk of every tap is normally 3–9
# minutes, so 10 minutes for ONE handler catches an infinite hang (the
# 2026-08-15 incident: two internal_rag handlers wedged on LiteLLM→Ollama
# embedding calls held the hourly run for 80 minutes) without clipping a
# legitimately slow tap.
_DEFAULT_HANDLER_TIMEOUT_S = 600

# Exception types that mean "declined", not "broken" — DB-tunable via
# app_settings.tap_deferral_exception_types (CSV of class names).
#
# Both are the GPU scheduler working correctly: GpuBusyError is admission
# refusing the run outright (game_mode while the operator is gaming, holder
# ETA in the hours), GpuLockTimeoutError is a budgeted wait expiring while
# the video pipeline holds the lock. Neither is a fault, and counting them
# as failures is what made the tap-failure signal untrustworthy: of 43
# internal_rag "failures" in the 30 days to 2026-08-15, 16 were these two.
#
# sentry_integration.py already draws this exact line the other way round —
# GpuBusyError is in its DEFAULT_DROP_EXCEPTION_TYPES so admission skips
# never reach GlitchTip as errors. This is the same classification, applied
# to the tap runner.
_DEFAULT_DEFERRAL_EXCEPTION_TYPES = "GpuBusyError,GpuLockTimeoutError"

# Consecutive genuine failures before a tap's finding escalates from `info`
# (recorded, never routed) to `warn` (routed per findings.tap_failure.*).
# DB-tunable via app_settings.tap_failure_alert_after_consecutive.
_DEFAULT_ALERT_AFTER_CONSECUTIVE = 2


@dataclass
class TapResult:
    name: str
    ok: bool
    duration_ms: int
    records: int = 0
    error: str | None = None
    # Declined, not broken. `ok` stays True so one deferral cannot flip the
    # whole walk to failed — see the RunSummary docstring.
    deferred: bool = False


@dataclass
class RunSummary:
    """Outcome of one full walk.

    ``total_failed`` counts genuine failures ONLY; deferrals are counted
    separately in ``total_deferred`` and leave ``ok`` alone. Before that
    split, one tap declined by GPU admission flipped ``RunTapsJob`` to
    ``ok=False`` — which emitted a ``job_failure`` finding reporting an
    outage while the other eight taps had just collected 251 records.
    """

    taps: list[TapResult]
    total_records: int
    total_failed: int
    total_deferred: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "taps": [t.__dict__ for t in self.taps],
            "total_records": self.total_records,
            "total_failed": self.total_failed,
            "total_deferred": self.total_deferred,
        }


def _resolve_deferral_types(site_config: Any) -> frozenset[str]:
    """Exception class names that record as a deferral rather than a failure."""
    raw = _DEFAULT_DEFERRAL_EXCEPTION_TYPES
    if site_config is not None:
        raw = site_config.get(
            "tap_deferral_exception_types", _DEFAULT_DEFERRAL_EXCEPTION_TYPES
        )
    return frozenset(part.strip() for part in str(raw or "").split(",") if part.strip())


def _emit_tap_failure_finding(
    *, name: str, error: str, streak: int, alert_after: int,
) -> None:
    """Route ONE tap's failure to the findings pipeline, keyed on that tap.

    The whole point of this function (poindexter#1015) is the per-tap
    identity. The scheduler's generic ``job_failure`` escalation keys its
    dedup on ``job-fail:run_taps`` and its cooldown on
    ``(kind, source) = ("job_failure", "scheduler.run_taps")`` — one key for
    all nine taps and every failure mode. Over the 30 days to 2026-08-15 that
    key fired 53 times, 43 of them ``internal_rag``: a chronically failing tap
    owned the fingerprint, so anything else breaking arrived behind it and was
    deduped away.

    Both identities here are per-tap, deliberately:

    - ``dedup_key='tap-fail:<name>'`` — what alert_dispatcher fingerprints on.
    - ``source='tap.<name>'`` — what the router's cooldown keys on, since
      poindexter#1010 made that ``(kind, source)`` rather than kind alone.
      A kind-only cooldown here would rebuild the very bug this fixes one
      level up, letting one tap's cooldown mute another's outage.

    Severity escalates with the streak rather than firing on contact:
    ``info`` is dashboard-visible but structurally never routed (the router's
    fetch floor selects warn/critical only), so a lone transient blip — the
    2026-08-15 dev.to container-DNS ConnectError, healed by the next run —
    is recorded without paging. ``warn`` from the Nth consecutive failure on.
    """
    try:
        from utils.findings import emit_finding

        sustained = streak >= max(1, alert_after)
        emit_finding(
            source=f"tap.{name}",
            kind="tap_failure",
            title=(
                f"Tap '{name}' failed {streak}× in a row"
                if sustained
                else f"Tap '{name}' failed"
            ),
            body=(
                f"The `{name}` tap failed with:\n\n```\n{error}\n```\n\n"
                f"Consecutive failures: {streak}.\n\n"
                + (
                    "This is a sustained failure — the tap has not succeeded "
                    "since the streak began, so its data is going stale.\n\n"
                    if sustained
                    else f"Recorded but not routed: a tap alerts only from its "
                    f"{max(1, alert_after)}th consecutive failure "
                    "(tap_failure_alert_after_consecutive), so a transient "
                    "blip that heals on the next run stays off the ops "
                    "channel.\n\n"
                )
                + "Inspect with `poindexter taps show "
                f"{name}`; the handler's own logs are in `docker logs "
                "poindexter-worker`.\n\n"
                "Note: a tap declined by GPU admission (game_mode) or a "
                "lock-wait timeout records as `deferred`, not `failed`, and "
                "never reaches this finding."
            ),
            severity="warn" if sustained else "info",
            dedup_key=f"tap-fail:{name}",
            extra={
                "tap": name,
                "consecutive_failures": streak,
                "alert_after_consecutive": max(1, alert_after),
                "error": error[:500],
            },
        )
    except Exception:  # noqa: BLE001 — observability must never break the walk
        logger.warning(
            "[tap-runner] failed to emit tap_failure finding for %s", name,
            exc_info=True,
        )


async def run_all(
    pool: Any,
    *,
    site_config: Any = None,
    only_names: list[str] | None = None,
    handler_timeout_s: float | None = None,
) -> RunSummary:
    """Execute every enabled tap once.

    Args:
        pool: asyncpg pool.
        site_config: passed through to handlers for credential resolution
            (and consulted for ``tap_handler_timeout_seconds``).
        only_names: restrict to specific tap names.
        handler_timeout_s: per-handler wall-clock budget. ``None`` (the
            default) resolves ``app_settings.tap_handler_timeout_seconds``
            off ``site_config``; ``<= 0`` disables the bound. A handler
            that exceeds it is cancelled and recorded as that tap's
            failure — the walk continues to the next row, same per-row
            isolation as any other handler exception.

    Returns a :class:`RunSummary` describing per-tap outcomes.
    """
    if handler_timeout_s is None:
        handler_timeout_s = float(_DEFAULT_HANDLER_TIMEOUT_S)
        if site_config is not None:
            handler_timeout_s = float(
                site_config.get_int(
                    "tap_handler_timeout_seconds", _DEFAULT_HANDLER_TIMEOUT_S
                )
            )

    deferral_types = _resolve_deferral_types(site_config)
    alert_after = _DEFAULT_ALERT_AFTER_CONSECUTIVE
    findings_enabled = True
    if site_config is not None:
        alert_after = int(
            site_config.get_int(
                "tap_failure_alert_after_consecutive",
                _DEFAULT_ALERT_AFTER_CONSECUTIVE,
            )
        )
        findings_enabled = bool(
            site_config.get_bool("tap_failure_finding_enabled", True)
        )

    rows = await _load_enabled_taps(pool, only_names)
    results: list[TapResult] = []
    total_records = total_failed = total_deferred = 0

    for row in rows:
        name = row["name"]
        start = time.perf_counter()
        try:
            dispatch = registry.dispatch(
                "tap",
                row["handler_name"],
                None,
                site_config=site_config,
                row=dict(row),
                pool=pool,
            )
            if handler_timeout_s > 0:
                result = await asyncio.wait_for(dispatch, timeout=handler_timeout_s)
            else:
                result = await dispatch
            duration_ms = int((time.perf_counter() - start) * 1000)
            records = int(result.get("records", 0)) if isinstance(result, dict) else 0
            total_records += records
            await _record_success(pool, row["id"], duration_ms, records)
            results.append(
                TapResult(
                    name=name, ok=True, duration_ms=duration_ms, records=records,
                )
            )
            logger.info(
                "[tap-runner] %s: records=%d (%dms)", name, records, duration_ms,
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)

            # Two different events arrive here as a TimeoutError, and telling
            # them apart matters:
            #
            #   * OUR guard firing. `asyncio.wait_for` raises EXACTLY the
            #     builtin TimeoutError, which stringifies to nothing — hence
            #     the explicit reason (feedback_no_silent_defaults). This
            #     bounds a wedged handler (2026-08-15: internal_rag taps
            #     blocked on LiteLLM→Ollama embedding calls after a CUDA OOM
            #     held the hourly run for 80 minutes).
            #   * A handler raising its own TimeoutError SUBCLASS —
            #     gpu_scheduler.GpuLockTimeoutError(TimeoutError) is the live
            #     one. Since 3.11 asyncio.TimeoutError IS TimeoutError, so an
            #     `except asyncio.TimeoutError` ahead of this block swallowed
            #     those too and reported "handler exceeded 600s" for a lock
            #     wait that never reached 600s — a false diagnosis that also
            #     skipped deferral classification below.
            #
            # `type(exc) is TimeoutError` is exact, so a subclass keeps its
            # own identity and routes on its own name.
            if handler_timeout_s > 0 and type(exc) is TimeoutError:
                err = (
                    f"TimeoutError: handler exceeded {handler_timeout_s:g}s "
                    "(tap_handler_timeout_seconds) — cancelled"
                )
            else:
                err = f"{type(exc).__name__}: {exc}"

            if type(exc).__name__ in deferral_types:
                # Declined, not broken — the GPU scheduler refusing admission
                # (game_mode) or a lock wait expiring behind the video
                # pipeline. Recorded on the row and counted, but it neither
                # advances the failure streak nor flips the walk to failed.
                total_deferred += 1
                await _record_deferral(pool, row["id"], duration_ms, err)
                results.append(
                    TapResult(
                        name=name, ok=True, duration_ms=duration_ms,
                        error=err, deferred=True,
                    )
                )
                logger.info("[tap-runner] %s deferred: %s", name, err)
            else:
                total_failed += 1
                streak = await _record_failure(pool, row["id"], duration_ms, err)
                results.append(
                    TapResult(
                        name=name, ok=False, duration_ms=duration_ms, error=err,
                    )
                )
                logger.warning(
                    "[tap-runner] %s failed (streak=%d): %s",
                    name, streak, err, exc_info=True,
                )
                if findings_enabled:
                    _emit_tap_failure_finding(
                        name=name, error=err, streak=streak,
                        alert_after=alert_after,
                    )

    return RunSummary(
        taps=results,
        total_records=total_records,
        total_failed=total_failed,
        total_deferred=total_deferred,
    )


async def _load_enabled_taps(
    pool: Any, only_names: list[str] | None,
) -> list[dict[str, Any]]:
    if pool is None:
        return []
    if only_names:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM external_taps
                 WHERE enabled = TRUE AND name = ANY($1::text[])
              ORDER BY name
                """,
                only_names,
            )
    else:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM external_taps
                 WHERE enabled = TRUE
              ORDER BY name
                """,
            )
    # Same JSONB-string parsing fix as outbound_dispatcher and
    # retention_runner. Tap handlers expect dict-typed config/metadata
    # but asyncpg returns raw JSON strings without a registered codec.
    out = []
    for r in rows:
        d = dict(r)
        for k in ("config", "metadata"):
            v = d.get(k)
            if isinstance(v, str) and v:
                try:
                    d[k] = json.loads(v)
                except json.JSONDecodeError as exc:
                    # poindexter#455 — used to be silent. A malformed
                    # JSONB cell silently kept the string and tap
                    # handlers (which expect d["config"]["enabled"]
                    # etc) then either crashed with TypeError or quietly
                    # treated absent keys as defaults. Log so the bad
                    # row is identifiable; let handlers continue to
                    # raise downstream so the failure stays loud.
                    logger.warning(
                        "[tap-runner] tap %r has malformed JSONB in %r "
                        "(%s) — leaving raw string; handler will likely "
                        "raise downstream",
                        d.get("name"), k, exc,
                    )
        out.append(d)
    return out


async def _record_success(
    pool: Any, row_id: Any, duration_ms: int, records: int,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE external_taps
               SET last_run_at = now(),
                   last_run_duration_ms = $2,
                   last_run_status = 'success',
                   last_run_records = $3,
                   last_error = NULL,
                   consecutive_failures = 0,
                   total_runs = total_runs + 1,
                   total_records = total_records + $3
             WHERE id = $1
            """,
            row_id, duration_ms, records,
        )


async def _record_failure(
    pool: Any, row_id: Any, duration_ms: int, error: str,
) -> int:
    """Record a genuine failure and return the tap's new failure streak.

    The returned streak drives alert severity in :func:`run_all` — see
    :func:`_emit_tap_failure_finding`. Returns 1 on any DB shape where the
    counter is unavailable, which reads as "first failure" and therefore
    errs toward NOT paging.
    """
    async with pool.acquire() as conn:
        streak = await conn.fetchval(
            """
            UPDATE external_taps
               SET last_run_at = now(),
                   last_run_duration_ms = $2,
                   last_run_status = 'failed',
                   last_error = $3,
                   consecutive_failures = consecutive_failures + 1,
                   total_runs = total_runs + 1
             WHERE id = $1
         RETURNING consecutive_failures
            """,
            row_id, duration_ms, error,
        )
    return int(streak) if streak is not None else 1


async def _record_deferral(
    pool: Any, row_id: Any, duration_ms: int, reason: str,
) -> None:
    """Record a tap that was declined rather than broken.

    ``consecutive_failures`` is deliberately NOT touched: a deferral is the
    GPU scheduler doing its job (the operator is gaming, or the video
    pipeline holds the lock), so it must neither start nor advance a failure
    streak. ``total_runs`` still increments — the walk did reach this row —
    and ``last_error`` carries the reason so the row explains itself.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE external_taps
               SET last_run_at = now(),
                   last_run_duration_ms = $2,
                   last_run_status = 'deferred',
                   last_error = $3,
                   total_runs = total_runs + 1
             WHERE id = $1
            """,
            row_id, duration_ms, reason,
        )
