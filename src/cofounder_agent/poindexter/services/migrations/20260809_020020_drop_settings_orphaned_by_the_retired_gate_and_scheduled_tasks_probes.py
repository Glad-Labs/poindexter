"""Migration 20260809_020020: drop settings orphaned by two retired probes

Four ``app_settings`` keys outlived the code that read them.

**1-3. ``gate_auto_expire_*``** — read by ``brain/gate_auto_expire_probe.py``,
which was deleted along with ``brain/gate_pending_summary_probe.py`` in
commit ``a38f60591`` (poindexter#559 / #570) when the gate probes were
retired. The keys survived because they were also seeded by
``0000_baseline.seeds.sql``, and the Phase G fold-forward squash
(2026-07-11) re-froze them into the regenerated baseline — so every fresh
install has been getting three settings with no reader since. Their
descriptions still advertise a probe that has not existed since May
("Master switch for the brain gate auto-expire probe (#338)"), which is
worse than absence: an operator who flips ``gate_auto_expire_enabled`` to
``false`` believes they have disabled something.

**4. ``scheduled_tasks_probe_watch_tasks``** — read by
``brain/health_probes.py::probe_scheduled_tasks``, retired in this same
change. That probe asked the host Recovery Agent to enumerate **Windows**
Task Scheduler entries; the host has run Pop!_OS since the July migration
and systemd timers replaced Task Scheduler, so it has been aimed at a
surface that no longer exists.

``ProbeZeroReaderSettingsJob`` never surfaced any of them: there are ~810
never-read keys past the 30-day grace against a
``settings_zero_reader_max_report`` cap of 50, so they sat far below the
fold. Found by hand instead — see the PR.

The seeds are removed from ``0000_baseline.seeds.sql`` in the same commit,
so a fresh install never creates them and this migration is the
already-installed half of the fix.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Keys whose only reader has been deleted. Listed explicitly (no LIKE
# pattern) so this can never widen to a key that is still live.
ORPHANED_KEYS = (
    "gate_auto_expire_enabled",
    "gate_auto_expire_batch_size",
    "gate_auto_expire_notify_threshold",
    "scheduled_tasks_probe_watch_tasks",
)


async def up(pool) -> None:
    """Delete the orphaned rows."""
    async with pool.acquire() as conn:
        deleted = await conn.fetch(
            "DELETE FROM app_settings WHERE key = ANY($1::text[]) RETURNING key",
            list(ORPHANED_KEYS),
        )
    logger.info(
        "Migration drop_settings_orphaned_by_the_retired_gate_and_scheduled_tasks_probes: "
        "applied (%d/%d orphaned key(s) deleted: %s)",
        len(deleted),
        len(ORPHANED_KEYS),
        ", ".join(sorted(r["key"] for r in deleted)) or "none present",
    )


async def down(pool) -> None:
    """Re-create the rows with their original values.

    Structure-only restore, which is the whole truth here: no code reads
    these keys on either side of the migration, so the values are inert.
    Operator-tuned values are NOT recoverable — that is acceptable
    precisely because tuning them never did anything.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
            VALUES
              ('gate_auto_expire_enabled', 'true', 'gates',
               'Master switch for the brain gate auto-expire probe (#338). '
               'When false, the probe short-circuits without scanning gates.',
               false, true),
              ('gate_auto_expire_batch_size', '50', 'gates',
               'Cap per-cycle expiry to this many gates to avoid huge batches. '
               'Excess rolls over to the next cycle.',
               false, true),
              ('gate_auto_expire_notify_threshold', '1', 'gates',
               'Only ping the operator (Telegram coalesced) when batch size >= this. '
               'Default 1 = always notify on any expiry.',
               false, true),
              ('scheduled_tasks_probe_watch_tasks', '', 'observability',
               'CSV of host Scheduled Task names the scheduled_tasks probe checked '
               'via GET /tasks. Empty = advisory no-op.',
               false, true)
            ON CONFLICT (key) DO NOTHING
            """
        )
    logger.info(
        "Migration drop_settings_orphaned_by_the_retired_gate_and_scheduled_tasks_probes: "
        "reverted (%d orphaned key(s) restored)",
        len(ORPHANED_KEYS),
    )
