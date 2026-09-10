"""ProbePipelineIdleJob — alert when the pipeline stops PRODUCING.

Every health signal the stack had was a *liveness* signal: containers up,
scheduler firing, jobs returning ok=True. On 2026-08-28 the content pipeline
created its last task at 22:00 and then produced nothing for ~46 hours, and
every one of those signals stayed green the entire time (poindexter#1036).

Nothing was broken. `run_niche_topic_sweep` fired every 30 minutes and
correctly reported ``ok=True … new_batches=0 skipped=1`` — it skips while an
open ``topic_batches`` row awaits an operator pick, and that row's
``expires_at`` is seeded 7 days out, so the stale-batch reaper would not have
freed it either. With one active niche, that single unactioned decision
halted all content generation. Every component was individually fine; the
business produced nothing.

This probe watches the output instead. It asks one question — *when did we
last create a content task?* — and so it catches every cause of idleness,
including ones nobody has thought of yet, rather than just the batch case.

Two deliberate design points:

**It only fires when output is expected.** An idle pipeline is correct when no
niche is active (the dev_diary niche is ``active=false`` today), so the probe
checks for at least one active niche first. Alerting on intentional quiet is
how a probe teaches operators to ignore it.

**It reports the reason, not just the symptom.** "No task in 20 hours" sends
someone digging; "no task in 20 hours, batch <id> has been open 46h, run
`poindexter topics show-batch`" is actionable from the notification itself.
The sweep already knows why it skipped — that knowledge just never left a log
line.

Severity is ``warn`` (Discord, per the Telegram-is-critical routing). An
operator who wants this louder raises it in the DB via
``findings.pipeline_idle.delivery`` rather than by editing code.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_ENABLED_KEY = "pipeline_idle_probe_enabled"
_MAX_IDLE_HOURS_KEY = "pipeline_idle_max_hours"
_DEFAULT_MAX_IDLE_HOURS = 12

_FINDING_KIND = "pipeline_idle"

# Newest task age + whether any niche is currently expected to produce.
# EXTRACT(EPOCH)/3600 rather than an interval so the value lands as a float
# the metrics sink can graph directly.
_IDLE_QUERY = """
    SELECT
        (SELECT MAX(created_at) FROM pipeline_tasks) AS newest_task,
        (SELECT EXTRACT(EPOCH FROM (now() - MAX(created_at))) / 3600.0
           FROM pipeline_tasks) AS idle_hours,
        (SELECT COUNT(*) FROM niches WHERE active) AS active_niches,
        (SELECT COUNT(*) FROM content_tasks
          WHERE status = 'awaiting_approval') AS approval_queue
"""

# Why it is idle, when the cause is the known one. LEFT-joined so a batch with
# no niche row still reports rather than vanishing from the result.
_OPEN_BATCH_QUERY = """
    SELECT b.id,
           n.slug,
           EXTRACT(EPOCH FROM (now() - b.created_at)) / 3600.0 AS open_hours,
           b.expires_at
      FROM topic_batches b
      LEFT JOIN niches n ON n.id = b.niche_id
     WHERE b.status = 'open'
     ORDER BY b.created_at
     LIMIT 1
"""


def _cfg_bool(site_config: Any, key: str, default: bool) -> bool:
    return site_config.get_bool(key, default) if site_config is not None else default


def _cfg_int(site_config: Any, key: str, default: int) -> int:
    return site_config.get_int(key, default) if site_config is not None else default


def build_idle_body(
    idle_hours: float, batch: Any, queue_size: int = 0, queue_limit: int = 0
) -> str:
    """The notification text. Split out so its content is testable.

    **Causes are ranked, and the order matters.** An open topic batch is the
    visible symptom, but on 08-28 it was not the thing to act on: the approval
    queue was full (5/5), so ``topic_auto_resolve`` correctly declined to
    resolve the batch, which left it open, which made the sweep skip. Telling
    the operator to clear the batch would have sent them to fix a downstream
    effect of their own unreviewed queue — and ``resolve-batch`` would just
    re-stall on the next cycle.

    So when the queue is full that is reported as the cause, and the batch is
    mentioned only as the consequence.
    """
    lines = [
        f"No content task has been created in {idle_hours:.1f} hours, and at "
        f"least one niche is active — so the pipeline is expected to be "
        f"producing and is not.",
        "",
        "Every liveness signal can be green while this is true: the sweep "
        "returns ok=True when it has nothing to do, and the containers and "
        "scheduler are unaffected (poindexter#1036).",
        "",
    ]
    queue_full = queue_limit > 0 and queue_size >= queue_limit
    if queue_full:
        lines += [
            f"**Cause: the approval queue is full ({queue_size}/{queue_limit}).** "
            f"This is back-pressure working as designed — `topic_auto_resolve` "
            f"refuses to promote new work behind a full HITL gate, so the open "
            f"topic batch stays open and `run_niche_topic_sweep` skips. Every "
            f"component is behaving correctly; the queue simply needs a human.",
            "",
            "**Approving or rejecting tasks restarts generation on its own** — "
            "resolving the topic batch would not, it would re-stall next cycle.",
            "",
            "```",
            "poindexter tasks list --status awaiting_approval",
            "poindexter tasks approve <id>   # or: reject <id>",
            "```",
            "",
            f"Raise `max_approval_queue` (currently {queue_limit}) if you want a "
            f"deeper buffer, or set it to 0 to disable the throttle entirely.",
        ]
        if batch is not None:
            lines += [
                "",
                f"_Consequence, not cause: topic batch `{batch['id']}` has been "
                f"open {float(batch['open_hours'] or 0.0):.1f}h behind this gate._",
            ]
    elif batch is not None:
        open_hours = float(batch["open_hours"] or 0.0)
        lines += [
            f"**Likely cause:** topic batch `{batch['id']}` "
            f"(niche `{batch['slug'] or 'unknown'}`) has been **open for "
            f"{open_hours:.1f} hours** awaiting an operator pick. "
            f"`run_niche_topic_sweep` skips while a batch is open, so this one "
            f"row halts generation for that niche.",
            "",
            f"Its `expires_at` is {batch['expires_at']}, so the stale-batch "
            f"reaper will not free it before then.",
            "",
            "Clear it with:",
            "```",
            "poindexter topics show-batch --niche <slug>",
            "poindexter topics resolve-batch <id>   # advance rank-1",
            "poindexter topics reject-batch <id>    # discard and re-sweep",
            "```",
        ]
    else:
        lines += [
            "**No open topic batch**, so the batch-blocking path is not the "
            "cause here. Check `run_niche_topic_sweep` in the worker log for "
            "its skip reason (cadence floor vs. error), and confirm the active "
            "niche still has enabled sources.",
        ]
    return "\n".join(lines)


class ProbePipelineIdleJob:
    """Emit a finding when no content task has been created for too long."""

    name = "probe_pipeline_idle"
    description = (
        "Watchdog for content-pipeline output — alerts when no task has been "
        "created for N hours while a niche is active (poindexter#1036)"
    )
    schedule = "every 1 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        if pool is None:
            return JobResult(ok=False, detail="no pool available", changes_made=0)

        site_config = config.get("_site_config")
        if not _cfg_bool(site_config, _ENABLED_KEY, True):
            return JobResult(ok=True, detail="disabled via app_settings", changes_made=0)

        max_idle = max(1, _cfg_int(site_config, _MAX_IDLE_HOURS_KEY, _DEFAULT_MAX_IDLE_HOURS))

        async with pool.acquire() as conn:
            row = await conn.fetchrow(_IDLE_QUERY)
            batch = None
            idle_hours = float(row["idle_hours"] or 0.0) if row else 0.0
            active_niches = int(row["active_niches"] or 0) if row else 0
            queue_size = int(row["approval_queue"] or 0) if row else 0
            if idle_hours > max_idle and active_niches > 0:
                batch = await conn.fetchrow(_OPEN_BATCH_QUERY)

        # Mirrors pipeline_throttle.is_queue_full's own default so the probe
        # never reports a limit the throttle isn't actually using.
        queue_limit = _cfg_int(site_config, "max_approval_queue", 3)

        metrics = {
            "idle_hours": round(idle_hours, 2),
            "active_niches": active_niches,
            "max_idle_hours": max_idle,
            "approval_queue": queue_size,
            "approval_queue_limit": queue_limit,
        }

        # An idle pipeline is CORRECT when nothing is meant to be producing.
        if active_niches == 0:
            return JobResult(
                ok=True,
                detail=f"idle {idle_hours:.1f}h but no active niche — expected",
                changes_made=0,
                metrics=metrics,
            )

        if idle_hours > max_idle:
            emit_finding(
                source="services.jobs.probe_pipeline_idle",
                kind=_FINDING_KIND,
                title=f"Content pipeline has produced nothing for {idle_hours:.1f} hours",
                body=build_idle_body(idle_hours, batch, queue_size, queue_limit),
                severity="warn",
                # Stable key: one page per stall, not one per hourly re-check.
                dedup_key=_FINDING_KIND,
                extra={**metrics, "open_batch_id": str(batch["id"]) if batch else None},
            )
            return JobResult(
                ok=True,
                detail=f"idle {idle_hours:.1f}h > {max_idle}h — finding emitted",
                changes_made=1,
                metrics=metrics,
            )

        return JobResult(
            ok=True,
            detail=f"last task {idle_hours:.1f}h ago (limit {max_idle}h)",
            changes_made=0,
            metrics=metrics,
        )
