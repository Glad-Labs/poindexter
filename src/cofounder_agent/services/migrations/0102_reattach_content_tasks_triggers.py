"""Migration 0102: Re-attach INSTEAD OF triggers on the ``content_tasks`` view.

Migration 0098 (HITL approval-gate columns) dropped + re-created the
``content_tasks`` view to surface the new gate columns. That worked,
but ``DROP VIEW`` orphans every ``CREATE TRIGGER`` bound to the view —
the trigger *functions* (``content_tasks_update_redirect``,
``content_tasks_insert_redirect``, ``content_tasks_delete_redirect``)
survived, but no actual triggers were attached to the new view.

Symptom: every ``UPDATE content_tasks`` since 0098 has failed with::

    asyncpg.exceptions.ObjectNotInPrerequisiteStateError:
      cannot update view "content_tasks"
    HINT: To enable updating the view, provide an INSTEAD OF UPDATE
      trigger or an unconditional ON UPDATE DO INSTEAD rule.

Caught 2026-04-28 when ``poindexter approve`` against the API silently
left tasks stuck at ``status='awaiting_approval'`` — the route logged
the error but returned 200 because it has a permissive try/except
around the update.

Fix: re-attach all three INSTEAD OF triggers. Functions are unchanged;
only the trigger bindings need restoring. Idempotent — DROP TRIGGER
IF EXISTS before each CREATE so a partial first-run doesn't block.

Side effect: every approve / reject / publish call sitting in the
queue at ``awaiting_approval`` since 2026-04-12 (when 0098 landed)
can now succeed on the next attempt. Pre-existing rows aren't
auto-fixed by this migration; the operator (or the next API call)
drives the transition.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_TRIGGERS = [
    (
        "content_tasks_update_trigger",
        "INSTEAD OF UPDATE",
        "content_tasks_update_redirect",
    ),
    (
        "content_tasks_insert_trigger",
        "INSTEAD OF INSERT",
        "content_tasks_insert_redirect",
    ),
    (
        "content_tasks_delete_trigger",
        "INSTEAD OF DELETE",
        "content_tasks_delete_redirect",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # INSTEAD OF triggers can only be attached to views. On fresh DBs
        # (migration smoke test, GH-229) content_tasks is a TABLE — see
        # the long note in 0098 for why the view recreation is skipped on
        # the post-niche-pivot canonical state. Without a view there's
        # nothing for these triggers to bind to. Skip cleanly so the
        # smoke test passes; the triggers are only meaningful on
        # deployments that still carry the view-based content_tasks
        # shape (legacy production prior to the niche pivot).
        is_view = await conn.fetchval(
            "SELECT 1 FROM information_schema.views "
            "WHERE table_schema='public' AND table_name='content_tasks'"
        )
        if not is_view:
            logger.info(
                "0102: content_tasks is a TABLE (post-niche-pivot canonical "
                "state) — INSTEAD OF triggers don't apply to tables. "
                "Skipping (operations write directly to pipeline_tasks)."
            )
            return

        # Also guard on the trigger functions existing — they may have
        # been dropped if the view shape was abandoned in a prior step.
        funcs_exist = await conn.fetchval(
            "SELECT COUNT(*) FROM pg_proc WHERE proname IN "
            "('content_tasks_update_redirect','content_tasks_insert_redirect',"
            "'content_tasks_delete_redirect')"
        )
        if (funcs_exist or 0) < len(_TRIGGERS):
            logger.info(
                "0102: trigger redirect functions missing (only %s of %s "
                "present) — skipping. Re-apply migrations 0068/0077/0078 "
                "first if the view-based path is needed.",
                funcs_exist, len(_TRIGGERS),
            )
            return

        for trigger_name, when_clause, function_name in _TRIGGERS:
            await conn.execute(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON content_tasks"
            )
            await conn.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                {when_clause} ON content_tasks
                FOR EACH ROW EXECUTE FUNCTION {function_name}()
                """
            )
        logger.info(
            "0102: re-attached %d INSTEAD OF triggers on content_tasks view",
            len(_TRIGGERS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for trigger_name, _when, _func in _TRIGGERS:
            await conn.execute(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON content_tasks"
            )
        logger.info("0102: detached INSTEAD OF triggers on content_tasks")
