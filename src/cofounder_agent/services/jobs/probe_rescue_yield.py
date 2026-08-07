"""ProbeRescueYieldJob — alert when the QA rescue loop stops converting.

The qa.rewrite rescue cycle burned 0-for-116 over 30 days in 2026-07 without
anyone noticing: every rescue costs a reviser LLM call plus a full re-run of
the QA rail chain, and nothing measured whether any of it ever converted a
reject into an approve (Glad-Labs/poindexter#986). The causes were fixed
(#984 output-token caps, #985 review window + judge), so the loop *should*
convert now — this probe is the watchdog that says so, or says it stopped.

Daily, over a trailing window it computes from ``qa_pass_completed`` rows:

- **attempts** — rescue cycles scheduled (``details->>'rescue_deferred' =
  'true'``, one per qa.aggregate deferral);
- **rescued** — terminal APPROVE decisions that went through >= 1 rewrite
  (``terminal`` + ``approved`` + ``qa_rewrite_attempts >= 1``).

When attempts clear ``qa_rescue_yield_min_attempts`` and rescued is still
ZERO, it emits one advisory ``qa_rescue_yield_zero`` finding (stable
dedup_key — one page until the situation changes). Every run also returns
the counts as ``JobResult.metrics`` so the scheduler's ``job_run`` audit sink
makes the yield graphable; the QA Rails board carries the matching panel.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_ENABLED_KEY = "qa_rescue_yield_probe_enabled"
_WINDOW_DAYS_KEY = "qa_rescue_yield_window_days"
_MIN_ATTEMPTS_KEY = "qa_rescue_yield_min_attempts"
_DEFAULT_WINDOW_DAYS = 14
_DEFAULT_MIN_ATTEMPTS = 8

_FINDING_KIND = "qa_rescue_yield_zero"

_YIELD_QUERY = """
    SELECT
        COUNT(*) FILTER (
            WHERE details->>'rescue_deferred' = 'true'
        ) AS attempts,
        COUNT(*) FILTER (
            WHERE details->>'terminal' = 'true'
              AND details->>'approved' = 'true'
              AND COALESCE((details->>'qa_rewrite_attempts')::int, 0) >= 1
        ) AS rescued
    FROM audit_log
    WHERE event_type = 'qa_pass_completed'
      AND "timestamp" > NOW() - ($1 * INTERVAL '1 day')
"""


def _cfg_bool(site_config: Any, key: str, default: bool) -> bool:
    return site_config.get_bool(key, default) if site_config is not None else default


def _cfg_int(site_config: Any, key: str, default: int) -> int:
    return site_config.get_int(key, default) if site_config is not None else default


class ProbeRescueYieldJob:
    """Emit a finding when the QA rescue loop is 0-for-N over the window."""

    name = "probe_rescue_yield"
    description = (
        "Watchdog for QA rescue-loop yield — alerts on a 0-for-N conversion "
        "streak instead of letting rewrites burn silently (poindexter#986)"
    )
    schedule = "every 24 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        if pool is None:
            return JobResult(ok=False, detail="no pool available", changes_made=0)

        site_config = config.get("_site_config")
        if not _cfg_bool(site_config, _ENABLED_KEY, True):
            return JobResult(ok=True, detail="disabled via app_settings", changes_made=0)

        window_days = max(1, _cfg_int(site_config, _WINDOW_DAYS_KEY, _DEFAULT_WINDOW_DAYS))
        min_attempts = max(1, _cfg_int(site_config, _MIN_ATTEMPTS_KEY, _DEFAULT_MIN_ATTEMPTS))

        async with pool.acquire() as conn:
            row = await conn.fetchrow(_YIELD_QUERY, window_days)
        attempts = int(row["attempts"] or 0)
        rescued = int(row["rescued"] or 0)
        yield_pct = round(100.0 * rescued / attempts, 1) if attempts else None

        metrics = {
            "rescue_attempts": attempts,
            "rescued": rescued,
            "yield_pct": yield_pct,
            "window_days": window_days,
        }

        if attempts >= min_attempts and rescued == 0:
            emit_finding(
                source="services.jobs.probe_rescue_yield",
                kind=_FINDING_KIND,
                title=(
                    f"QA rescue loop is 0-for-{attempts} over the last "
                    f"{window_days} day(s)"
                ),
                body=(
                    f"qa.aggregate scheduled {attempts} rescue rewrite(s) in the "
                    f"last {window_days} day(s) and NONE converted to a terminal "
                    f"approve. Each attempt costs a reviser LLM call plus a full "
                    f"QA rail re-run, so a zero-yield streak is pure burn — the "
                    f"2026-07 collapse ran 0-for-116 unnoticed "
                    f"(Glad-Labs/poindexter#986).\n\n"
                    f"Check, in order: the reviser model "
                    f"(`qa_rewrite_model`, empty = writer pin), the judge "
                    f"calibration (`poindexter model-eval run --slot critic`), "
                    f"and whether the vetoes reaching rescue are actually "
                    f"text-fixable. Tune the alert via "
                    f"`{_MIN_ATTEMPTS_KEY}` / `{_WINDOW_DAYS_KEY}`; consider "
                    f"`qa_rewrite_max_attempts=1` while yield is zero."
                ),
                severity="warn",
                dedup_key=f"{_FINDING_KIND}:{window_days}d",
                extra=metrics,
            )
            return JobResult(
                ok=True,
                detail=f"0-for-{attempts} over {window_days}d — finding emitted",
                changes_made=1,
                metrics=metrics,
            )

        detail = (
            f"rescue yield {rescued}/{attempts}"
            + (f" ({yield_pct}%)" if yield_pct is not None else "")
            + f" over {window_days}d"
            + ("" if attempts >= min_attempts else f" (below min_attempts={min_attempts})")
        )
        return JobResult(ok=True, detail=detail, changes_made=0, metrics=metrics)
