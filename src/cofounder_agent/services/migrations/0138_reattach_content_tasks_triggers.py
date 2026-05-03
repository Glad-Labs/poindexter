"""Migration 0138: re-attach INSTEAD OF triggers to ``content_tasks`` view.

Background
----------

Some recent migration (likely the 0131_post_approval_gates rebuild OR
the 0132 issue-27 migration) recreated the ``content_tasks`` view but
did NOT re-attach the three INSTEAD OF triggers that redirect writes
to the underlying base tables (``content_tasks_update_redirect``,
``content_tasks_insert_redirect``, ``content_tasks_delete_redirect``).

The trigger FUNCTIONS still exist (created in 0125 / 0102). They were
just orphaned — defined but no longer wired to any trigger.

Symptom (caught 2026-05-03 while seeding a topic for Matt):

::

    asyncpg.exceptions.ObjectNotInPrerequisiteStateError:
      cannot insert into view "content_tasks"
    asyncpg.exceptions.ObjectNotInPrerequisiteStateError:
      cannot update view "content_tasks"
      HINT: To enable updating the view, provide an INSTEAD OF UPDATE trigger.

Net effect: every task created via ``add_task``, every status update via
``update_task``, every delete — all silently fail with no row movement.
Pipeline produces zero new tasks for 2 days.

PR #191 patched the INSERT side by routing add_task / bulk_add_tasks
to ``pipeline_tasks`` directly (the canonical base table). The INSTEAD
OF triggers being missing wasn't on that PR's radar — but restoring
them fixes the UPDATE path AND makes the INSERT path resilient to
future regressions of the rewrite-callsite approach.

This migration is idempotent — `CREATE TRIGGER` guarded by an
`information_schema.triggers` check.

Companion to: Glad-Labs/poindexter#341 (the INSERT side).
"""

from __future__ import annotations

from services.logger_config import get_logger

logger = get_logger(__name__)


_REATTACH_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.triggers
         WHERE trigger_name = 'content_tasks_update_trigger'
           AND event_object_table = 'content_tasks'
    ) THEN
        CREATE TRIGGER content_tasks_update_trigger
            INSTEAD OF UPDATE ON content_tasks
            FOR EACH ROW
            EXECUTE FUNCTION content_tasks_update_redirect();
        RAISE NOTICE 'Created content_tasks_update_trigger';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.triggers
         WHERE trigger_name = 'content_tasks_insert_trigger'
           AND event_object_table = 'content_tasks'
    ) THEN
        CREATE TRIGGER content_tasks_insert_trigger
            INSTEAD OF INSERT ON content_tasks
            FOR EACH ROW
            EXECUTE FUNCTION content_tasks_insert_redirect();
        RAISE NOTICE 'Created content_tasks_insert_trigger';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.triggers
         WHERE trigger_name = 'content_tasks_delete_trigger'
           AND event_object_table = 'content_tasks'
    ) THEN
        CREATE TRIGGER content_tasks_delete_trigger
            INSTEAD OF DELETE ON content_tasks
            FOR EACH ROW
            EXECUTE FUNCTION content_tasks_delete_redirect();
        RAISE NOTICE 'Created content_tasks_delete_trigger';
    END IF;
END$$;
"""


async def up(conn) -> None:
    """Re-attach the three INSTEAD OF triggers if they're missing."""
    # The trigger functions must already exist — created in 0125
    # (and recreated in 0102 historically). If they've been dropped
    # too, this migration will fail loudly with a clear error message;
    # operator should re-run 0125 to recreate them.
    fn_check = await conn.fetch(
        """
        SELECT proname FROM pg_proc
         WHERE proname IN (
             'content_tasks_update_redirect',
             'content_tasks_insert_redirect',
             'content_tasks_delete_redirect'
         )
        """,
    )
    fn_names = {r["proname"] for r in fn_check}
    missing = {
        "content_tasks_update_redirect",
        "content_tasks_insert_redirect",
        "content_tasks_delete_redirect",
    } - fn_names
    if missing:
        raise RuntimeError(
            "Cannot re-attach content_tasks triggers — these redirect "
            "functions are missing from the DB: "
            + ", ".join(sorted(missing))
            + ". Re-run migration 0125 first.",
        )

    # Also bail if `content_tasks` is somehow a TABLE here (very old
    # dev DB that never converted). The triggers only make sense on
    # the view.
    relkind_row = await conn.fetchrow(
        """
        SELECT relkind FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relname = 'content_tasks'
        """,
    )
    if relkind_row is None:
        logger.info(
            "0138: content_tasks does not exist — skipping trigger reattach"
        )
        return
    if relkind_row["relkind"] != "v":
        logger.info(
            "0138: content_tasks is not a view (relkind=%s) — skipping; "
            "0125 should have converted it first",
            relkind_row["relkind"],
        )
        return

    await conn.execute(_REATTACH_SQL)
    logger.info("0138: content_tasks INSTEAD OF triggers reattached (or already present)")


async def down(conn) -> None:
    """Drop the triggers (functions remain, owned by 0125)."""
    await conn.execute(
        """
        DROP TRIGGER IF EXISTS content_tasks_update_trigger ON content_tasks;
        DROP TRIGGER IF EXISTS content_tasks_insert_trigger ON content_tasks;
        DROP TRIGGER IF EXISTS content_tasks_delete_trigger ON content_tasks;
        """,
    )
