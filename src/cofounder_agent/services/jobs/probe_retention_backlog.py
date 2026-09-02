"""ProbeRetentionBacklogJob — is each retention policy actually keeping up?

poindexter#933, from Glad-Labs/poindexter#2871 where
``retention.checkpoint_prune`` failed to prune ~20k rows **for months** while
reporting success on every run: ``last_error`` NULL, ``last_run_at`` current,
``total_deleted`` non-zero, the Grafana panel green. Every signal we had was a
*liveness* signal. None of them was a *correctness* signal.

The policy did what it was told and what it was told was wrong, so **a
misconfigured policy and an idle one produced byte-identical telemetry**.

This probe asks the question none of those signals answer: how many rows
should each policy have removed and hasn't? Handlers declare that as a backlog
expression (see :mod:`services.integrations.retention_backlog`, which explains
why the expression states the *invariant* rather than re-running the policy's
own possibly-wrong predicate).

Persistence is the signal, not magnitude
----------------------------------------

A single non-zero reading means nothing. ~20 of the enabled policies
legitimately sit at ``deleted=0`` most runs, and a short-TTL high-volume table
like ``live_activity`` shows hundreds of overdue rows minutes after a
completely successful prune — that is inflow, not failure. Measured live: 1,699
overdue immediately after a run that deleted 2,953.

So a policy is only reported once its backlog has stayed above the threshold
for ``retention_backlog_consecutive_probes`` consecutive readings. A correct
policy drains; a broken one accumulates, and only the broken one can hold a
backlog across probes.

Sample AFTER the run, not on a fixed clock
------------------------------------------

The backlog only means "failed to drain" if it is measured once the policy has
had its chance to drain. Measured just BEFORE a pass it means "accumulated
since last time", which for any healthy high-inflow policy is large by
definition.

The first version scheduled this ``every 6 hours`` and asserted in a comment
that it was offset from ``RunRetentionJob`` "so a reading is taken shortly
after a pass". It was not. Both jobs registered as ``every 6 hours`` from
worker start and landed 26 minutes apart in the wrong direction — retention at
:41, this probe at :15 — so every sample was taken 5h34m after the previous
pass and 26 minutes before the next, i.e. at near-maximum backlog. It produced
a false ``retention_backlog`` finding for ``live_activity`` on its third
reading (2738 -> 2746 -> 2751) while that policy was in fact draining
completely every run (``last_run_deleted=2961``, no error).

So phase is not something to assert and hope for. This probe now ticks often
and records a reading for a policy ONLY when that policy has run within
``retention_backlog_sample_window_minutes``, and only once per pass (keyed on
the policy's own ``last_run_at``). Two schedules can drift; a policy's own
``last_run_at`` cannot lie about when it last ran.

Readings are kept as ``retention_backlog_sample`` rows in ``audit_log`` rather
than in a new table — the same store the job-metrics sink already uses, and it
gives the history for free.

Unmonitored is not healthy
--------------------------

Handlers with no backlog expression are counted and named in the result, never
folded into a passing zero. A policy silently exempt from the correctness check
would recreate the exact blind spot this probe exists to close.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from plugins.job import JobResult
from services.integrations.retention_backlog import BacklogResult, measure_all
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_ENABLED_KEY = "retention_backlog_probe_enabled"
_THRESHOLD_KEY = "retention_backlog_row_threshold"
_CONSECUTIVE_KEY = "retention_backlog_consecutive_probes"
_WINDOW_KEY = "retention_backlog_sample_window_minutes"

_DEFAULT_THRESHOLD = 100
_DEFAULT_CONSECUTIVE = 3
# How soon after a policy's own run its backlog still means "did not drain".
# Wide enough to survive a slow tick or a long retention pass, far narrower
# than the 6h gap that let a pre-run reading masquerade as a backlog.
_DEFAULT_WINDOW_MINUTES = 90

_FINDING_KIND = "retention_backlog"
_SAMPLE_EVENT = "retention_backlog_sample"

_POLICY_QUERY = """
    SELECT name, handler_name, table_name, age_column, ttl_days,
           filter_sql, config, last_run_at,
           EXTRACT(EPOCH FROM (now() - last_run_at))/60.0 AS minutes_since_run
      FROM retention_policies
     WHERE enabled
     ORDER BY name
"""

# Newest-first samples, capped by the consecutive window we actually need.
_SAMPLE_QUERY = """
    SELECT details
      FROM audit_log
     WHERE event_type = $1
     ORDER BY "timestamp" DESC
     LIMIT $2
"""


def _cfg_bool(site_config: Any, key: str, default: bool) -> bool:
    return site_config.get_bool(key, default) if site_config is not None else default


def _cfg_int(site_config: Any, key: str, default: int) -> int:
    return site_config.get_int(key, default) if site_config is not None else default


def _as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def eligible_for_sampling(
    rows: list[dict[str, Any]],
    last_sample: dict[str, Any],
    *,
    window_minutes: int,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Split policies into (measure now, out of window, already sampled).

    A policy is measured only when it has run within ``window_minutes`` — a
    backlog read before the pass measures inflow, not failure to drain — and
    only once per pass, keyed on its own ``last_run_at`` so a fast tick cannot
    stack three readings of the same post-run state and call them a trend.

    ``last_sample`` is the newest recorded sample's ``runs`` map
    (``{policy: last_run_at_iso}``).
    """
    measure: list[dict[str, Any]] = []
    out_of_window: list[str] = []
    already: list[str] = []
    for row in rows:
        name = str(row.get("name") or "<unnamed>")
        run_at = row.get("last_run_at")
        mins = row.get("minutes_since_run")
        if run_at is None or mins is None:
            # Never run. Not a backlog signal — RunRetentionJob's own liveness
            # covers a policy that is not executing at all.
            out_of_window.append(name)
            continue
        if float(mins) > window_minutes:
            out_of_window.append(name)
            continue
        if last_sample.get(name) == run_at.isoformat():
            already.append(name)
            continue
        measure.append(row)
    return measure, out_of_window, already


def breaching_policies(
    results: list[BacklogResult],
    prior_samples: list[dict[str, Any]],
    *,
    threshold: int,
    consecutive: int,
) -> list[BacklogResult]:
    """Policies over ``threshold`` on this reading AND every prior one needed.

    ``prior_samples`` is newest-first, each a ``{policy: count}`` map from an
    earlier probe. A policy qualifies only with ``consecutive`` total readings
    (this one plus ``consecutive - 1`` priors) all above the threshold, so a
    fresh install cannot page before it has the history to justify it.

    A policy absent from a prior sample breaks the streak: it was not measured
    then, and an unmeasured reading is not a breaching one.
    """
    if consecutive < 1:
        consecutive = 1
    needed_priors = consecutive - 1
    if len(prior_samples) < needed_priors:
        return []

    out: list[BacklogResult] = []
    for r in results:
        if r.status != "measured" or r.count is None or r.count <= threshold:
            continue
        if all(
            int(sample.get(r.policy, -1)) > threshold
            for sample in prior_samples[:needed_priors]
        ):
            out.append(r)
    return out


def build_body(
    breaching: list[BacklogResult],
    *,
    threshold: int,
    consecutive: int,
    unmonitored: list[BacklogResult],
    errored: list[BacklogResult],
) -> str:
    lines = [
        f"{len(breaching)} retention policy(ies) have held a backlog above "
        f"{threshold} rows across {consecutive} consecutive passes, measured "
        f"AFTER each run — they are running without error but not draining "
        f"what they are supposed to remove.",
        "",
        "A retention policy reports `last_error=NULL`, a current `last_run_at` "
        "and a non-zero `total_deleted` whether it is idle or misconfigured, so "
        "none of those can tell you this. The backlog is the difference.",
        "",
    ]
    for r in breaching:
        lines.append(f"- `{r.policy}` (`{r.handler}`): **{r.count}** rows overdue")
    if unmonitored:
        lines += [
            "",
            f"**{len(unmonitored)} policy(ies) are NOT covered by this check** — "
            f"their handler declares no backlog expression, so nothing verifies "
            f"they are keeping up:",
            "",
        ]
        lines += [f"- `{r.policy}` (`{r.handler}`)" for r in unmonitored]
    if errored:
        lines += [
            "",
            f"**{len(errored)} backlog measurement(s) failed** (reported as "
            f"unknown, never as zero):",
            "",
        ]
        lines += [f"- `{r.policy}` (`{r.handler}`): {r.detail}" for r in errored]
    return "\n".join(lines)


class ProbeRetentionBacklogJob:
    """Emit a finding for retention policies that run cleanly but do not drain."""

    name = "probe_retention_backlog"
    description = (
        "Measure each retention policy's overdue backlog and flag any that "
        "persists — the correctness signal liveness telemetry cannot give "
        "(poindexter#933)"
    )
    # Ticks often and samples selectively: a reading is only recorded once a
    # policy has actually run (see eligible_for_sampling), so this cadence sets
    # how promptly the post-run window is caught, not how often a policy is
    # measured. Measuring all 29 live policies takes ~0.03s.
    schedule = "every 30 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        if pool is None:
            return JobResult(ok=False, detail="no pool available", changes_made=0)

        site_config = config.get("_site_config")
        if not _cfg_bool(site_config, _ENABLED_KEY, True):
            return JobResult(ok=True, detail="probe disabled", changes_made=0)

        threshold = _cfg_int(site_config, _THRESHOLD_KEY, _DEFAULT_THRESHOLD)
        consecutive = max(1, _cfg_int(site_config, _CONSECUTIVE_KEY, _DEFAULT_CONSECUTIVE))
        window = _cfg_int(site_config, _WINDOW_KEY, _DEFAULT_WINDOW_MINUTES)

        try:
            async with pool.acquire() as conn:
                rows = [dict(r) for r in await conn.fetch(_POLICY_QUERY)]
                raw_samples = await conn.fetch(
                    _SAMPLE_QUERY, _SAMPLE_EVENT, max(consecutive - 1, 1)
                )
        except Exception as e:  # noqa: BLE001 — a probe must never crash a cycle
            detail = describe_exception(e)
            logger.warning("[probe_retention_backlog] query failed: %s", detail)
            return JobResult(ok=False, detail=f"query failed: {detail}", changes_made=0)

        if not rows:
            # A check that scanned nothing has not passed.
            return JobResult(
                ok=False,
                detail="no enabled retention policies — nothing was measured",
                changes_made=0,
            )

        prior = [_as_dict(r["details"]) for r in raw_samples]
        newest_runs = _as_dict(prior[0].get("runs")) if prior else {}

        to_measure, out_of_window, already = eligible_for_sampling(
            rows, newest_runs, window_minutes=window,
        )
        if not to_measure:
            # Not a pass and not a failure — no policy has run recently enough
            # for its backlog to mean anything yet. Say so rather than writing
            # an empty sample, which would break every persistence streak.
            return JobResult(
                ok=True,
                detail=(
                    f"no policy ran within {window}m — nothing sampled "
                    f"({len(out_of_window)} out of window, "
                    f"{len(already)} already sampled this pass)"
                ),
                changes_made=0,
                metrics={
                    "policies": len(rows), "sampled": 0,
                    "out_of_window": len(out_of_window),
                    "already_sampled": len(already),
                },
            )

        results = await measure_all(pool, to_measure)
        measured = [r for r in results if r.status == "measured"]
        unmonitored = [r for r in results if r.status == "unmonitored"]
        errored = [r for r in results if r.status == "error"]

        prior_samples = [_as_dict(d.get("backlogs")) for d in prior]
        breaching = breaching_policies(
            results, prior_samples, threshold=threshold, consecutive=consecutive,
        )

        # Record this reading regardless of outcome — it is the history the
        # next probe's persistence test depends on. `runs` carries each
        # policy's last_run_at so the next tick can tell a NEW pass from the
        # one it already sampled.
        sample = {r.policy: r.count for r in measured}
        runs = dict(newest_runs)
        for row in to_measure:
            run_at = row.get("last_run_at")
            if run_at is not None:
                runs[str(row.get("name"))] = run_at.isoformat()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO audit_log (event_type, source, details, severity) "
                    "VALUES ($1, 'retention_backlog_probe', $2::jsonb, 'info')",
                    _SAMPLE_EVENT,
                    json.dumps({
                        "backlogs": sample,
                        "runs": runs,
                        "threshold": threshold,
                        "window_minutes": window,
                        "unmonitored": [r.policy for r in unmonitored],
                        "errored": [r.policy for r in errored],
                    }),
                )
        except Exception as e:  # noqa: BLE001 — losing a sample must not fail the probe
            logger.warning(
                "[probe_retention_backlog] sample insert failed: %s",
                describe_exception(e),
            )

        metrics = {
            "policies": len(rows),
            "sampled": len(results),
            "out_of_window": len(out_of_window),
            "already_sampled": len(already),
            "measured": len(measured),
            "unmonitored": len(unmonitored),
            "errored": len(errored),
            "breaching": len(breaching),
            "max_backlog": max((r.count or 0 for r in measured), default=0),
        }

        if not breaching:
            return JobResult(
                ok=True,
                detail=(
                    f"{len(measured)} policy(ies) sampled after their run, "
                    f"none holding a backlog over {threshold} across "
                    f"{consecutive} passes ({len(unmonitored)} unmonitored, "
                    f"{len(errored)} errored, {len(out_of_window)} not yet run)"
                ),
                changes_made=0,
                metrics=metrics,
            )

        emit_finding(
            source="retention_backlog_probe",
            kind=_FINDING_KIND,
            title=(
                f"{len(breaching)} retention policy(ies) running clean but not "
                f"draining"
            ),
            body=build_body(
                breaching,
                threshold=threshold,
                consecutive=consecutive,
                unmonitored=unmonitored,
                errored=errored,
            ),
            severity="warn",
            dedup_key=f"{_FINDING_KIND}:" + ",".join(sorted(r.policy for r in breaching)),
            extra={
                "threshold": threshold,
                "consecutive_probes": consecutive,
                "policies": {r.policy: r.count for r in breaching},
                "unmonitored": [r.policy for r in unmonitored],
            },
        )
        return JobResult(
            ok=True,
            detail=f"{len(breaching)} policy(ies) holding a persistent backlog",
            changes_made=0,
            metrics=metrics,
        )
