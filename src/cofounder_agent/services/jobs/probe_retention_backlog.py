"""ProbeRetentionBacklogJob — is each retention policy actually keeping up?

poindexter#933, from Glad-Labs/glad-labs-stack#2871 where
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

_DEFAULT_THRESHOLD = 100
_DEFAULT_CONSECUTIVE = 3

_FINDING_KIND = "retention_backlog"
_SAMPLE_EVENT = "retention_backlog_sample"

_POLICY_QUERY = """
    SELECT name, handler_name, table_name, age_column, ttl_days,
           filter_sql, config
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
        f"{threshold} rows for {consecutive} consecutive probes — they are "
        f"running without error but not draining what they are supposed to "
        f"remove.",
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
    # Offset from RunRetentionJob's cadence so a reading is taken shortly
    # after a pass, when a healthy policy should have drained.
    schedule = "every 6 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        if pool is None:
            return JobResult(ok=False, detail="no pool available", changes_made=0)

        site_config = config.get("_site_config")
        if not _cfg_bool(site_config, _ENABLED_KEY, True):
            return JobResult(ok=True, detail="probe disabled", changes_made=0)

        threshold = _cfg_int(site_config, _THRESHOLD_KEY, _DEFAULT_THRESHOLD)
        consecutive = max(1, _cfg_int(site_config, _CONSECUTIVE_KEY, _DEFAULT_CONSECUTIVE))

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

        results = await measure_all(pool, rows)
        measured = [r for r in results if r.status == "measured"]
        unmonitored = [r for r in results if r.status == "unmonitored"]
        errored = [r for r in results if r.status == "error"]

        prior_samples = [
            _as_dict(_as_dict(r["details"]).get("backlogs")) for r in raw_samples
        ]
        breaching = breaching_policies(
            results, prior_samples, threshold=threshold, consecutive=consecutive,
        )

        # Record this reading regardless of outcome — it is the history the
        # next probe's persistence test depends on.
        sample = {r.policy: r.count for r in measured}
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO audit_log (event_type, source, details, severity) "
                    "VALUES ($1, 'retention_backlog_probe', $2::jsonb, 'info')",
                    _SAMPLE_EVENT,
                    json.dumps({
                        "backlogs": sample,
                        "threshold": threshold,
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
            "policies": len(results),
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
                    f"{len(measured)} policy(ies) measured, none holding a "
                    f"backlog over {threshold} for {consecutive} probes "
                    f"({len(unmonitored)} unmonitored, {len(errored)} errored)"
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
