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

# The single definition of the "paid-API axis": a genuinely-billable row is a
# non-electricity row. Local inference/media rows are $0 by the P1 write
# invariant (``dispatcher.py::_record_dispatch_cost`` writes ``cost_usd=0`` for
# any ``not _is_paid_llm_call``), so ``SUM(cost_usd)`` over this predicate is
# real cloud spend without any in-SQL locality heuristic. Exported so every
# spend consumer — notably the ``cost_guard`` cap — rents the SAME definition
# rather than re-deriving a provider-name denylist that drifts. The pre-2026-07
# ``NOT IN ('electricity','ollama','ollama_native')`` denylist did exactly that:
# it omitted ``'litellm'`` (the post-2026-05-16 router tag for local inference)
# and matched zero ``'ollama'`` rows. Keyed on ``cost_type`` it is router-
# agnostic and survives another router swap.
API_AXIS_PREDICATE = "COALESCE(cost_type, 'inference') NOT LIKE 'electricity%'"

# The complementary electricity axis: the brain daemon's measured PSU rows
# (``cost_type LIKE 'electricity%'``) are the real power bill. Exported as the
# single owner of the electricity predicate — the mirror of
# ``API_AXIS_PREDICATE`` — so per-axis consumers (anomaly detection, the metrics
# exporter) split cost_logs on the same two definitions everything else uses.
# Every row is on exactly one axis: api (non-electricity) or electricity.
ELECTRICITY_AXIS_PREDICATE = "cost_type LIKE 'electricity%'"

# The idle slice of the electricity axis: power the machine draws whether or not
# the pipeline is doing anything. Named here because the ledger owns cost-type
# semantics; consumers that need to reason about *controllable* spend (notably
# ``spend_throttle``) rent this rather than hardcoding the string.
IDLE_ELECTRICITY_COST_TYPE = "electricity_idle"

# All SQL below interpolates only hardcoded literals (the window literal keyed by
# the Window enum + the axis predicates above), never user input — hence the
# uniform ``# nosec B608``.
_API_SQL = (
    "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_logs "
    f"WHERE {API_AXIS_PREDICATE} AND {{w}}"  # nosec B608
)
_ELEC_SQL = (
    "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_logs "
    f"WHERE {ELECTRICITY_AXIS_PREDICATE} AND {{w}}"  # nosec B608
)
_MEASURED_COUNT_SQL = (
    f"SELECT COUNT(*) FROM cost_logs WHERE {ELECTRICITY_AXIS_PREDICATE} AND {{w}}"  # nosec B608
)
_EST_KWH_SQL = (
    "SELECT COALESCE(SUM(electricity_kwh), 0) FROM cost_logs "
    f"WHERE {API_AXIS_PREDICATE} AND {{w}}"  # nosec B608
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
    # The idle slice already counted inside ``total_usd``, so a consumer can
    # subtract it to get controllable spend. Populated ONLY on the measured
    # path, where ``electricity_usd`` really is the sum of the ``electricity%``
    # rows and this is one of its components. On the estimated path
    # ``electricity_usd`` is derived from per-call ``electricity_kwh`` over the
    # API axis — attributable to work, with no idle component in the total at
    # all — so this stays 0.0 and subtracting it is a correct no-op rather than
    # a double-subtraction of a number that was never added.
    idle_electricity_usd: float = 0.0


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
    # Only meaningful when ``electricity`` came from the measured rows — see the
    # field docstring on SpendBreakdown for why the estimated path has no idle
    # component to report.
    idle = (
        float(by_type.get(IDLE_ELECTRICITY_COST_TYPE, 0.0))
        if source == "measured"
        else 0.0
    )
    return SpendBreakdown(
        api_usd=api,
        electricity_usd=electricity,
        total_usd=api + electricity,
        electricity_source=source,
        electricity_coverage_pct=round(coverage, 1),
        by_type=by_type,
        idle_electricity_usd=idle,
    )
