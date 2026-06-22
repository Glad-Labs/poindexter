"""Single read seam for cost_logs spend — splits the API and electricity axes.

Replaces N hand-rolled ``SUM(cost_usd)`` queries (cost_guard, get_spend_totals,
get_budget_status, detect_anomalies) that each disagreed and ran on a polluted
meter. Relies on the P1 write invariant: a LOCAL inference/media row has
``cost_usd=0``, so the api axis ("everything not electricity") sums only
genuinely-paid cloud spend without an in-SQL locality heuristic.

Electricity is **measured-primary**: the brain daemon's PSU rows
(``cost_type LIKE 'electricity%'``) are the bill. When the measured feed didn't
cover the window (HX1500i sampling has been flaky), electricity falls back to
the per-call ``electricity_kwh`` estimate × ``electricity_rate_kwh`` — flagged
via ``electricity_source`` so a degraded reading is never silently wrong. The
two are mutually exclusive per window, so the brain-vs-per-call double-count
can't recur.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Window = Literal["day", "month"]

_WINDOW_SQL = {
    "day": "created_at >= date_trunc('day', NOW())",
    "month": "created_at >= date_trunc('month', NOW())",
}
# Expected number of measured samples in a window if the brain wrote one
# electricity row every gap_minutes; turns a raw count into a coverage %.
_WINDOW_MINUTES = {"day": 24 * 60, "month": 30 * 24 * 60}

# All SQL below interpolates only a hardcoded window literal (keyed by the
# Window enum), never user input — hence the uniform ``# nosec B608``.
_API_SQL = (
    "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_logs "
    "WHERE COALESCE(cost_type, 'inference') NOT LIKE 'electricity%' AND {w}"
)
_ELEC_SQL = (
    "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_logs "
    "WHERE cost_type LIKE 'electricity%' AND {w}"
)
_MEASURED_COUNT_SQL = (
    "SELECT COUNT(*) FROM cost_logs WHERE cost_type LIKE 'electricity%' AND {w}"
)
_EST_KWH_SQL = (
    "SELECT COALESCE(SUM(electricity_kwh), 0) FROM cost_logs "
    "WHERE COALESCE(cost_type,'inference') NOT LIKE 'electricity%' AND {w}"
)
_BYTYPE_SQL = (
    "SELECT COALESCE(cost_type, 'inference') AS t, COALESCE(SUM(cost_usd), 0) AS v "
    "FROM cost_logs WHERE {w} GROUP BY 1"
)


@dataclass
class SpendBreakdown:
    api_usd: float = 0.0
    electricity_usd: float = 0.0
    total_usd: float = 0.0
    electricity_source: Literal["measured", "estimated", "mixed", "none"] = "none"
    electricity_coverage_pct: float = 0.0
    by_type: dict[str, float] = field(default_factory=dict)


async def get_spend(
    pool: Any,
    *,
    window: Window = "day",
    strict: bool = False,
    site_config: Any = None,
) -> SpendBreakdown:
    """Return the spend breakdown for ``window`` ('day' | 'month').

    ``strict=True`` re-raises on DB error (fail-closed callers like the budget
    gate); the default swallows to a zeroed breakdown (fail-open callers like
    the spend throttle and dashboards). ``site_config`` supplies the electricity
    coverage / rate knobs; ``None`` uses the documented defaults.
    """
    w = _WINDOW_SQL[window]
    try:
        api = float(await pool.fetchval(_API_SQL.format(w=w)) or 0.0)  # nosec B608
        measured = float(await pool.fetchval(_ELEC_SQL.format(w=w)) or 0.0)  # nosec B608
        samples = int(await pool.fetchval(_MEASURED_COUNT_SQL.format(w=w)) or 0)  # nosec B608
        rows = await pool.fetch(_BYTYPE_SQL.format(w=w))  # nosec B608
    except Exception:
        if strict:
            raise
        return SpendBreakdown()

    def _cfg(key: str, default: float) -> float:
        if site_config is None:
            return float(default)
        try:
            return float(site_config.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    gap_min = _cfg("electricity_source_gap_minutes", 15.0)
    min_cov = _cfg("electricity_measured_min_coverage_pct", 80.0)
    expected = max(1.0, _WINDOW_MINUTES[window] / max(1.0, gap_min))
    coverage = min(100.0, 100.0 * samples / expected)

    if coverage >= min_cov and measured > 0:
        electricity: float = measured
        source: Literal["measured", "estimated", "mixed", "none"] = "measured"
    else:
        try:
            est_kwh = float(await pool.fetchval(_EST_KWH_SQL.format(w=w)) or 0.0)  # nosec B608
        except Exception:
            est_kwh = 0.0
        electricity = est_kwh * _cfg("electricity_rate_kwh", 0.16)
        source = "estimated" if electricity > 0 else "none"

    by_type = {r["t"]: float(r["v"] or 0.0) for r in rows}
    return SpendBreakdown(
        api_usd=api,
        electricity_usd=electricity,
        total_usd=api + electricity,
        electricity_source=source,
        electricity_coverage_pct=round(coverage, 1),
        by_type=by_type,
    )
