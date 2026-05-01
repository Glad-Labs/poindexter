"""RunNicheTopicSweepJob — periodic discovery sweep per active niche.

Layer 1 of the topic-discovery UX (Glad-Labs/poindexter — niche pivot).
Until this lands, ``TopicBatchService.run_sweep()`` had no caller, so
the topic_batches and topic_candidates tables stayed empty. Operators
who ran ``poindexter topics show-batch <niche>`` got "no open batch."

What this job does
==================

For each active niche, calls ``TopicBatchService.run_sweep(niche_id=...)``.
``run_sweep`` is itself idempotent — it short-circuits when the niche's
``discovery_cadence_minute_floor`` hasn't elapsed since the last batch,
or when an open (undecided) batch already exists. So this job's only
responsibility is "fan out to every active niche on a fast cadence;
let the service decide whether to actually generate a batch."

Schedule
--------
Default ``every 30 minutes``. The per-niche floor (``niches
.discovery_cadence_minute_floor``, default 60) gates the actual work,
so a 30-minute schedule never under-fires on a 60-minute floor and
adapts cleanly if an operator drops a niche's floor to 15.

Config (``plugin.job.run_niche_topic_sweep``)
---------------------------------------------
- ``enabled`` (default ``true``)
- ``interval_seconds`` (default 1800)
- ``config.notify_on_new_batch`` (default ``true``) — when a batch
  actually gets generated this cycle, fire the operator notification
  with the top-3 candidates. Layer 3 of the UX work.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


class RunNicheTopicSweepJob:
    name = "run_niche_topic_sweep"
    description = "Trigger TopicBatchService.run_sweep() per active niche"
    schedule = "every 30 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        from services.niche_service import NicheService
        from services.topic_batch_service import TopicBatchService

        notify = bool(config.get("notify_on_new_batch", True))

        niches = await NicheService(pool).list_active()
        if not niches:
            return JobResult(
                ok=True, detail="no active niches configured", changes_made=0,
            )

        svc = TopicBatchService(pool)
        new_batches = 0
        skipped = 0
        errors = 0

        for niche in niches:
            try:
                snapshot = await svc.run_sweep(niche_id=niche.id)
            except Exception as exc:
                # Per-niche failure shouldn't kill the sweep loop — log
                # and keep going so other niches still get their chance.
                logger.exception(
                    "[niche-topic-sweep] run_sweep failed for niche=%s (%s): %s",
                    niche.slug, niche.id, exc,
                )
                errors += 1
                continue

            if snapshot is None:
                # Cadence floor not elapsed, or open batch already exists.
                skipped += 1
                continue

            new_batches += 1
            logger.info(
                "[niche-topic-sweep] generated batch %s for niche %s "
                "(%d candidates)",
                snapshot.id, niche.slug, snapshot.candidate_count,
            )

            if notify:
                # Best-effort — never let notification failures take down
                # the sweep job. The batch is already persisted.
                try:
                    await _notify_new_batch(pool, niche, snapshot)
                except Exception as notify_err:
                    logger.warning(
                        "[niche-topic-sweep] notification failed for batch %s: %s",
                        snapshot.id, notify_err,
                    )

        detail = (
            f"niches={len(niches)} new_batches={new_batches} "
            f"skipped={skipped} errors={errors}"
        )
        return JobResult(
            ok=errors == 0,
            detail=detail,
            changes_made=new_batches,
        )


async def _notify_new_batch(pool: Any, niche: Any, snapshot: Any) -> None:
    """Layer 3 — notify the operator on Telegram + Discord with the top
    candidates so they can rank/pick from their phone.

    Lazy-import the notify helper so the sweep job is testable without
    pulling in the full telegram/discord stack.
    """
    from services.topic_batch_service import TopicBatchService

    svc = TopicBatchService(pool)
    view = await svc.show_batch(batch_id=snapshot.id)

    top = view.candidates[:3]
    lines = [
        f"📥 New topic batch for *{niche.slug}* ({len(view.candidates)} candidates)",
        f"Batch id: `{snapshot.id}`",
        "",
        "Top picks:",
    ]
    for i, c in enumerate(top, start=1):
        lines.append(f"{i}. {c.title}  _(score {c.effective_score:.2f})_")
        if c.summary:
            preview = c.summary[:120].replace("\n", " ").strip()
            lines.append(f"   {preview}")

    lines.extend([
        "",
        "Reply with `1` / `2` / `3` to pick, "
        "`re-rank` to re-sweep, or `skip` to close the batch.",
    ])

    message = "\n".join(lines)
    await _send_to_operator_channels(pool, message)


async def _send_to_operator_channels(pool: Any, message: str) -> None:
    """Send to the operator via the outbound dispatcher.

    Routes through ``services.integrations.operator_notify.notify_operator``,
    which selects the ``discord_ops`` row for non-critical messages. The
    helper is best-effort and silently falls back to a direct Discord
    webhook if the dispatcher framework isn't wired up yet (early boot,
    tests, CLI one-shots).
    """
    try:
        from services.integrations.operator_notify import notify_operator
        await notify_operator(message, critical=False)
    except Exception as e:
        logger.warning("[niche-topic-sweep] no notification path available: %s", e)
