"""`poindexter doctor` — unified health check-graph (#527, v1 deterministic).

This service is the **aggregator/reasoner** over the per-probe health
results the brain already persists to ``brain_knowledge`` every cycle
(``entity='probe.<name>'``, ``attribute='health_status'``,
``value=<json result dict>``, ``source='health_probe'``, ``updated_at``).

It does NOT re-invoke the probes (that would duplicate execution and force
cross-package probe imports from the CLI; the persisted results are <=5 min
fresh). A ``--live`` re-run is a documented v1.1 follow-up.

What it adds on top of the flat per-probe results:

* a **dependency check-graph** (:data:`DEPENDS_ON`) for root-cause
  correlation — a DB-down root produces ONE failure with its dependents
  marked ``suppressed`` instead of a 10-alarm symptom storm;
* a **health score** (0-100, weighted, ``app_settings``-tunable);
* a **systemic-vs-local** correlation flag;
* a **brain-freshness meta-check** — if the persisted results are stale
  (brain may be down) the doctor says so loudly and marks every probe
  result ``stale`` rather than reporting a falsely-healthy snapshot.

The hybrid LLM-diagnostician escalation (:func:`_escalate_unknown`) is a
typed no-op seam in v1 — deferred to Phase 2 / #429. **No LLM in v1.**

Design doc: ``docs/architecture/poindexter-doctor.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Result type (shape borrowed from OpenJarvis' doctor; extended with the
# fields the check-graph needs — age, root, remediation key).
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """A single normalized health check, ready for scoring + display.

    ``status`` is one of:

    * ``"ok"``        — the underlying probe reported ``ok: true``;
    * ``"warn"``      — ``ok: false`` with ``severity == "warning"``;
    * ``"fail"``      — ``ok: false`` with ``severity`` critical / unset;
    * ``"stale"``     — the brain looks down (results too old to trust);
    * ``"suppressed"``— failing/warning, but an upstream dependency failed
                         first, so this is reported under ``root`` rather
                         than as an independent alarm.
    """

    name: str
    status: str
    detail: str
    age_seconds: float
    metrics: dict[str, Any] = field(default_factory=dict)
    remediation: str | None = None  # REMEDIATIONS key, when one exists
    root: str | None = None  # set when suppressed by an upstream failure

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "age_seconds": round(self.age_seconds, 1),
            "metrics": self.metrics,
            "remediation": self.remediation,
            "root": self.root,
        }


@dataclass
class DoctorReport:
    """The full structured doctor result (``--json`` consumable)."""

    score: int
    systemic: bool
    brain_stale: bool
    checks: list[CheckResult]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "systemic": self.systemic,
            "brain_stale": self.brain_stale,
            "generated_at": self.generated_at,
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Static dependency check-graph (decided in the design doc: explicit map,
# not category inference — debuggable, and there are only a handful of roots).
#
# Roots (no entry here): ``db_ping``, ``ollama_models``, ``worker_error_rate``.
# A node's failing/warning status is *suppressed* (reported under the root)
# when ANY of its upstream deps is itself ``fail``.
#
# Names are the keys in ``brain/health_probes.py::PROBES``. Probes not listed
# here (and not roots) simply have no upstream dep and surface directly.
# ---------------------------------------------------------------------------

DEPENDS_ON: dict[str, list[str]] = {
    # --- DB-backed probes: every one of these queries Postgres, so a
    #     db_ping failure is the root cause of all of them reds at once. ---
    "stuck_tasks": ["db_ping"],
    "approval_queue": ["db_ping"],
    "failed_task_spike": ["db_ping"],
    "publish_rate": ["db_ping"],
    "cost_freshness": ["db_ping"],
    "podcast_health": ["db_ping"],
    "newsletter_health": ["db_ping"],
    "cadence_slo": ["db_ping"],
    "quality_trend": ["db_ping"],
    "topic_quality": ["db_ping"],
    "embeddings_freshness": ["db_ping"],
    "traffic_anomaly": ["db_ping"],
    "scheduled_tasks": ["db_ping"],
    "anomaly": ["db_ping"],
    "research_service": ["db_ping"],
    # --- Generation/quality probes: these drive an Ollama model, so an
    #     ollama_models failure is the root cause when they red. ---
    "content_gen": ["ollama_models"],
    # quality_score reads pipeline_versions (DB) AND reflects generation
    # output quality (Ollama) — both are genuine upstreams.
    "quality_score": ["db_ping", "ollama_models"],
    # --- Throughput depends on BOTH the worker being up and the DB being
    #     reachable (it counts rows the worker writes). ---
    "pipeline_throughput": ["worker_error_rate", "db_ping"],
}

# The three roots — surfaced directly, never suppressed. Derived rather than
# hardcoded a second time so the two stay in sync if DEPENDS_ON grows.
ROOTS: frozenset[str] = frozenset(
    dep for deps in DEPENDS_ON.values() for dep in deps
)


# ---------------------------------------------------------------------------
# Score weights — defaults in code, overridable per-key via app_settings
# ``doctor_weight_<status>`` (+ a per-root multiplier). Keeps the SaaS-tunable
# posture: an operator can dial how harshly a warn vs a fail dents the score.
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS: dict[str, float] = {
    # penalty subtracted from 100 per check of this status
    "fail": 20.0,
    "warn": 6.0,
    # a fail on a ROOT check costs the most — multiply the fail penalty
    "root_multiplier": 1.5,
    # suppressed + stale checks do NOT double-count — penalty 0
    "suppressed": 0.0,
    "stale": 0.0,
    "ok": 0.0,
}

# Brain-freshness: the brain runs a 300s cycle. >2x that (>600s) and the
# persisted probe results are stale enough that the brain itself may be down.
_DEFAULT_CYCLE_SECONDS = 300
_DEFAULT_STALE_MULTIPLIER = 2.0

# Correlation: this many INDEPENDENT (non-suppressed) degraded subsystems at
# once flips the systemic flag ("not a local blip").
_DEFAULT_SYSTEMIC_THRESHOLD = 3


def _normalize_status(result: dict[str, Any]) -> str:
    """Map a persisted probe result dict to a doctor status.

    ``ok: true`` -> ``"ok"``. ``ok: false`` splits on ``severity``:
    ``"warning"`` -> ``"warn"``; ``"critical"`` or absent -> ``"fail"``.
    """
    if result.get("ok"):
        return "ok"
    severity = (result.get("severity") or "").lower()
    if severity == "warning":
        return "warn"
    # critical, or no severity at all -> treat as a hard failure
    return "fail"


# ---------------------------------------------------------------------------
# Brain-freshness meta-check (the #524 dead-man's-switch surfaced in doctor).
# ---------------------------------------------------------------------------


async def _newest_signal_age_seconds(pool: Any) -> float | None:
    """Seconds since the brain last left a trace, or ``None`` if never.

    Reads the newest of:

    * the latest ``brain.cycle_heartbeat`` row in ``audit_log`` (the column
      is ``"timestamp"`` — quoted because it's a reserved word — NOT
      ``created_at``);
    * the max ``updated_at`` across the ``brain_knowledge`` probe rows.

    Whichever is more recent wins; either alone is enough to prove the brain
    cycled. Returns ``None`` when neither exists (fresh DB / brain never ran).
    """
    rows = await pool.fetch(
        """
        SELECT MAX(ts) AS newest FROM (
            SELECT MAX("timestamp") AS ts
              FROM audit_log
             WHERE event_type = 'brain.cycle_heartbeat'
            UNION ALL
            SELECT MAX(updated_at) AS ts
              FROM brain_knowledge
             WHERE source = 'health_probe'
        ) AS sources
        """
    )
    if not rows:
        return None
    newest = rows[0]["newest"]
    if newest is None:
        return None
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - newest).total_seconds()


# ---------------------------------------------------------------------------
# Load + normalize persisted probe results.
# ---------------------------------------------------------------------------


async def load_check_results(pool: Any) -> list[CheckResult]:
    """Read every persisted probe result and normalize to CheckResults.

    One row per ``brain_knowledge`` probe entry (``entity='probe.<name>'``,
    ``attribute='health_status'``, ``source='health_probe'``). The
    ``value`` column holds the JSON result dict; ``updated_at`` gives age.

    No graph reasoning here — that happens in :func:`apply_root_cause`. This
    function only does the per-row normalization (severity -> status, json
    decode, remediation-key lookup, age).
    """
    # Imported lazily so the service module is importable without the brain
    # package on sys.path (e.g. in unit tests that feed a fake pool). The
    # CLI process does have brain/ importable; tests patch this.
    remediation_keys = _load_remediation_keys()

    rows = await pool.fetch(
        """
        SELECT entity, value, updated_at
          FROM brain_knowledge
         WHERE source = 'health_probe'
           AND attribute = 'health_status'
        """
    )

    now = datetime.now(timezone.utc)
    checks: list[CheckResult] = []
    for row in rows:
        entity = row["entity"]  # e.g. "probe.db_ping"
        name = entity.split(".", 1)[1] if "." in entity else entity

        raw = row["value"]
        try:
            result = raw if isinstance(raw, dict) else json.loads(raw)
        except (TypeError, ValueError):
            result = {"ok": False, "detail": f"unparseable probe value: {raw!r}"}

        updated_at = row["updated_at"]
        if updated_at is None:
            age = 0.0
        else:
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            age = (now - updated_at).total_seconds()

        # Pull out the well-known keys; everything else is a metric.
        detail = str(result.get("detail", ""))
        metrics = {
            k: v
            for k, v in result.items()
            if k not in ("ok", "detail", "severity")
        }

        checks.append(
            CheckResult(
                name=name,
                status=_normalize_status(result),
                detail=detail,
                age_seconds=max(age, 0.0),
                metrics=metrics,
                remediation=name if name in remediation_keys else None,
            )
        )

    checks.sort(key=lambda c: c.name)
    return checks


def _load_remediation_keys() -> frozenset[str]:
    """Probe names that have a REMEDIATIONS entry in the brain.

    Best-effort: if the brain package isn't importable (e.g. a stripped
    test env) we return an empty set — the doctor still works, ``--fix``
    just has nothing to offer. The CLI's ``--fix`` re-imports the actual
    helpers; this is only used to flag which checks *could* be fixed.
    """
    try:
        from health_probes import REMEDIATIONS  # type: ignore[import-not-found]

        return frozenset(REMEDIATIONS.keys())
    except Exception:  # noqa: BLE001 — brain not on path is fine
        return frozenset()


# ---------------------------------------------------------------------------
# Root-cause suppression over the static graph.
# ---------------------------------------------------------------------------


def apply_root_cause(checks: list[CheckResult]) -> list[CheckResult]:
    """Mark failing/warning checks ``suppressed`` when an upstream dep failed.

    Rule: a check is suppressed (``status="suppressed"``, ``root=<dep>``)
    only when (a) the check itself is currently ``fail`` or ``warn`` AND
    (b) at least one of its :data:`DEPENDS_ON` upstreams is ``fail``. An
    ``ok`` check is never suppressed (a healthy dependent under a failed
    root is genuinely fine and shouldn't be hidden).

    Roots themselves are never suppressed — they surface directly.
    """
    by_name = {c.name: c for c in checks}

    for check in checks:
        if check.status not in ("fail", "warn"):
            continue
        for dep in DEPENDS_ON.get(check.name, []):
            upstream = by_name.get(dep)
            if upstream is not None and upstream.status == "fail":
                check.status = "suppressed"
                check.root = dep
                break
    return checks


def mark_all_stale(checks: list[CheckResult]) -> list[CheckResult]:
    """Override every check to ``stale`` (used when the brain looks down).

    A stale brain means the persisted results are old — reporting them as
    ok/fail would be a falsely-confident snapshot. We surface them as
    ``stale`` so the operator knows the data, not the system, is suspect.
    """
    for check in checks:
        check.status = "stale"
        check.root = None
    return checks


# ---------------------------------------------------------------------------
# Score + correlation.
# ---------------------------------------------------------------------------


def score(checks: list[CheckResult], weights: dict[str, float] | None = None) -> int:
    """Compute a 0-100 health score from normalized checks.

    Start at 100, subtract a per-status penalty for each check. A ``fail``
    on a ROOT check is multiplied by ``root_multiplier`` (root failures hurt
    most — they take subsystems down with them). ``suppressed`` and ``stale``
    contribute 0 so a symptom storm under one root doesn't double-count.
    Clamped to ``[0, 100]``.
    """
    w = {**_DEFAULT_WEIGHTS, **(weights or {})}
    penalty = 0.0
    for check in checks:
        base = w.get(check.status, 0.0)
        if check.status == "fail" and check.name in ROOTS:
            base *= w.get("root_multiplier", 1.0)
        penalty += base
    return max(0, min(100, round(100 - penalty)))


def correlate(checks: list[CheckResult], threshold: int = _DEFAULT_SYSTEMIC_THRESHOLD) -> bool:
    """Return True when >= ``threshold`` INDEPENDENT subsystems are degraded.

    Independent = ``fail`` or ``warn`` and NOT ``suppressed`` (a suppressed
    check is a symptom of a single root, not an independent failure). When
    enough distinct subsystems red at once, this is systemic rather than a
    local blip.
    """
    degraded = sum(1 for c in checks if c.status in ("fail", "warn"))
    return degraded >= threshold


# ---------------------------------------------------------------------------
# app_settings-tunable knobs.
# ---------------------------------------------------------------------------


async def _load_tunables(pool: Any) -> dict[str, Any]:
    """Read the doctor's app_settings knobs (weights + thresholds).

    Falls back to code defaults for any missing/blank key. ``app_settings``
    ``value`` is NOT NULL (it uses ``''`` as the unset sentinel), so a blank
    string here means "use the default", consistent with the rest of the
    codebase.
    """
    keys = [
        "doctor_weight_fail",
        "doctor_weight_warn",
        "doctor_weight_root_multiplier",
        "doctor_systemic_threshold",
        "doctor_cycle_seconds",
        "doctor_stale_multiplier",
    ]
    rows = await pool.fetch(
        "SELECT key, value FROM app_settings WHERE key = ANY($1::text[])",
        keys,
    )
    raw = {r["key"]: r["value"] for r in rows}

    def _f(key: str, default: float) -> float:
        v = (raw.get(key) or "").strip()
        try:
            return float(v) if v else default
        except (TypeError, ValueError):
            return default

    def _i(key: str, default: int) -> int:
        v = (raw.get(key) or "").strip()
        try:
            return int(float(v)) if v else default
        except (TypeError, ValueError):
            return default

    return {
        "weights": {
            "fail": _f("doctor_weight_fail", _DEFAULT_WEIGHTS["fail"]),
            "warn": _f("doctor_weight_warn", _DEFAULT_WEIGHTS["warn"]),
            "root_multiplier": _f(
                "doctor_weight_root_multiplier", _DEFAULT_WEIGHTS["root_multiplier"]
            ),
        },
        "systemic_threshold": _i(
            "doctor_systemic_threshold", _DEFAULT_SYSTEMIC_THRESHOLD
        ),
        "cycle_seconds": _i("doctor_cycle_seconds", _DEFAULT_CYCLE_SECONDS),
        "stale_multiplier": _f("doctor_stale_multiplier", _DEFAULT_STALE_MULTIPLIER),
    }


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


async def run_doctor(pool: Any) -> DoctorReport:
    """Aggregate persisted probe results into a single :class:`DoctorReport`.

    Pipeline:

    1. read the doctor's ``app_settings`` knobs (weights/thresholds);
    2. brain-freshness meta-check — if the newest persisted signal is older
       than ``stale_multiplier * cycle_seconds``, the brain looks down;
    3. load + normalize every persisted probe result;
    4. if the brain is stale, mark every check ``stale`` (don't trust the
       snapshot); otherwise apply root-cause suppression over the graph;
    5. compute score + systemic flag;
    6. fire the (no-op) LLM-escalation seam.
    """
    tunables = await _load_tunables(pool)

    age = await _newest_signal_age_seconds(pool)
    stale_after = tunables["stale_multiplier"] * tunables["cycle_seconds"]
    # No signal at all OR older than the stale window -> brain looks down.
    brain_stale = age is None or age > stale_after

    checks = await load_check_results(pool)

    if brain_stale:
        mark_all_stale(checks)
        # A brain-down meta-check synthesised at the front so it's never
        # silently absent from the report.
        checks.insert(
            0,
            CheckResult(
                name="brain_freshness",
                status="fail",
                detail=(
                    "brain results stale "
                    f"({'no signal' if age is None else f'{age:.0f}s old'} "
                    f"> {stale_after:.0f}s window) — brain daemon may be down"
                ),
                age_seconds=age or 0.0,
                metrics={"stale_after_seconds": stale_after},
            ),
        )
    else:
        apply_root_cause(checks)

    computed = score(checks, weights=tunables["weights"])
    systemic = correlate(checks, threshold=tunables["systemic_threshold"])

    report = DoctorReport(
        score=computed,
        systemic=systemic,
        brain_stale=brain_stale,
        checks=checks,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    _escalate_unknown(checks)
    return report


def _escalate_unknown(checks: list[CheckResult]) -> None:
    """No-op seam for the hybrid LLM diagnostician (deferred to #429).

    In Phase 2, a check that is ``fail`` with NO ``REMEDIATIONS`` entry — or
    one that stays red across N doctor runs after a ``--fix`` — escalates to
    the existing ``firefighter_service`` LLM for a ranked, cross-store
    (logs/metrics/traces) diagnosis. That path is gated on #429 (DataFabric)
    giving the diagnostician the reads it needs.

    v1 ships the deterministic core only and keeps this typed seam so the LLM
    path lands cleanly later. It intentionally does NOTHING today — the
    recover/deliver path stays deterministic (``feedback_calculated_vs_generated``).
    """
    return None


__all__ = [
    "CheckResult",
    "DoctorReport",
    "DEPENDS_ON",
    "ROOTS",
    "load_check_results",
    "apply_root_cause",
    "mark_all_stale",
    "score",
    "correlate",
    "run_doctor",
]
