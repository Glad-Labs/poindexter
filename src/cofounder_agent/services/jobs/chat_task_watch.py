"""ChatTaskWatchJob — completion messages for conversation-linked runs.

Cofounder P3 (poindexter#949): a pipeline task started from chat is linked
via ``chat_task_links``. When it reaches a terminal status this job appends
ONE system message to the owning conversation — a ``task_result`` card
(status, topic, quality score; the console wires Approve/Reject/Trace onto
it) plus a plain-text line — and pings the operator once, then stamps
``completed_notified_at`` so the sweep never re-fires. The message lands
even if the operator closed the tab hours ago: that async return-and-report
is the point.

Notification channel is ``console_chat_watch_notify`` (``discord`` default —
a draft-ready ping is routine, not critical, per the operator's
Telegram=critical / Discord=routine split; ``telegram`` opts up, ``none``
silences). The deeplink rides ``console_public_url`` when set.
"""

from __future__ import annotations

from typing import Any

from plugins.job import JobResult
from services.chat_watch import TERMINAL_STATUSES
from services.logger_config import get_logger
from services.site_config import SiteConfig
from utils.exception_format import describe_exception

logger = get_logger(__name__)

_HAPPY_STATUSES = frozenset({"awaiting_approval", "approved", "published", "completed"})


class ChatTaskWatchJob:
    name = "chat_task_watch"
    description = "Append completion messages for chat-linked pipeline runs"
    schedule = "every 1 minute"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config: SiteConfig | None = config.get("_site_config")
        if site_config is None:
            return JobResult(ok=False, detail="no _site_config in config — skipping")
        if str(site_config.get("console_chat_enabled", "false")).lower() not in (
            "true", "1", "yes", "on",
        ):
            return JobResult(ok=True, detail="console_chat_enabled=false — no-op")

        batch_size = int(config.get("batch_size", 20))
        try:
            # quality_score lives on pipeline_versions (latest version per
            # task) — pipeline_tasks has no such column. Selecting
            # t.quality_score errored EVERY minute post-deploy (2026-08-01;
            # the unit fakes couldn't catch a column-name drift).
            rows = await pool.fetch(
                """
                SELECT l.conversation_id, l.pipeline_task_id,
                       t.task_id, t.status, t.topic,
                       (SELECT v.quality_score FROM pipeline_versions v
                         WHERE v.task_id = t.task_id
                         ORDER BY v.version DESC LIMIT 1) AS quality_score
                  FROM chat_task_links l
                  JOIN pipeline_tasks t
                    ON (t.task_id = l.pipeline_task_id
                        OR t.id::text = l.pipeline_task_id)
                 WHERE l.completed_notified_at IS NULL
                   AND t.status = ANY($1::text[])
                 ORDER BY l.created_at
                 LIMIT $2
                """,
                list(TERMINAL_STATUSES), batch_size,
            )
        except Exception as exc:  # noqa: BLE001 — job surface, report + retry next tick
            logger.error("[ChatTaskWatchJob] sweep query failed: %s", exc)
            return JobResult(ok=False, detail=describe_exception(exc))

        if not rows:
            return JobResult(ok=True, detail="no newly-terminal linked runs")

        notified = 0
        for row in rows:
            try:
                await self._notify_one(pool, site_config, row)
                notified += 1
            except Exception:  # noqa: BLE001 — one bad row must not stall the sweep
                logger.exception(
                    "[ChatTaskWatchJob] completion for task %s failed",
                    row["pipeline_task_id"],
                )
        return JobResult(
            ok=True,
            detail=f"notified {notified}/{len(rows)} linked run(s)",
            changes_made=notified > 0,
            metrics={"notified": notified, "swept": len(rows)},
        )

    async def _notify_one(
        self, pool: Any, site_config: SiteConfig, row: Any,
    ) -> None:
        from services import chat_conversation_store as store

        conversation_id = str(row["conversation_id"])
        task_id = row["task_id"]
        status = row["status"]
        topic = row["topic"] or "(untitled)"
        quality = (
            float(row["quality_score"]) if row["quality_score"] is not None else None
        )

        happy = status in _HAPPY_STATUSES
        if status == "awaiting_approval":
            text = (
                f'Draft ready for review: "{topic}"'
                + (f" (quality {quality:.0f})" if quality is not None else "")
                + f" — task {task_id[:8]} awaits your approval."
            )
        elif happy:
            text = f'Run finished ({status}): "{topic}" — task {task_id[:8]}.'
        else:
            text = f'Run ended {status}: "{topic}" — task {task_id[:8]}.'

        await store.add_message(
            pool, conversation_id, role="system",
            parts=[
                {
                    "type": "card",
                    "card": {
                        "kind": "task_result",
                        "task_id": task_id,
                        "status": status,
                        "topic": topic,
                        "quality_score": quality,
                    },
                },
                {"type": "markdown", "text": text},
            ],
        )
        # Stamp BEFORE the external ping: a Discord outage must not make the
        # sweep re-append the message every minute; the thread is the record,
        # the ping is best-effort.
        await pool.execute(
            """
            UPDATE chat_task_links
               SET completed_notified_at = now()
             WHERE conversation_id = $1::uuid AND pipeline_task_id = $2
            """,
            conversation_id, row["pipeline_task_id"],
        )

        channel = str(
            site_config.get("console_chat_watch_notify", "discord") or "discord"
        ).lower()
        if channel == "none":
            return
        console_url = str(site_config.get("console_public_url", "") or "").strip()
        link = f" {console_url}/console/#trace/{task_id}" if console_url else ""
        try:
            from services.integrations.operator_notify import notify_operator

            await notify_operator(
                f"[cofounder] {text}{link}",
                critical=(channel == "telegram"),
                site_config=site_config,
            )
        except Exception:  # noqa: BLE001 — ping is best-effort; thread already has it
            logger.exception("[ChatTaskWatchJob] operator ping failed")


__all__ = ["ChatTaskWatchJob"]
