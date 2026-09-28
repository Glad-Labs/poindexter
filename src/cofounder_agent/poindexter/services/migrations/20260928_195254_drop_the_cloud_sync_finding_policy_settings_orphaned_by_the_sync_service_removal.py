"""Migration 20260928_195254: drop the findings.cloud_sync_returned_false.*
policy settings, orphaned when SyncService was removed

ISSUE: Glad-Labs/poindexter#1112

``SyncService`` (``poindexter.services.sync_service``) was the two-database-era
sync between the local brain DB and a hosted "cloud" copy. The commit that adds
this migration deletes it, together with its one live caller,
``publish_service._sync_published_post``. That helper was the only emitter of
the ``cloud_sync_returned_false`` and ``cloud_sync_exception`` findings.

``findings_alert_router`` loads every ``findings.<kind>.*`` row by prefix, but a
row only takes effect when a finding of that kind arrives. Nothing emits
``cloud_sync_returned_false`` any more, so its four policy rows can never apply.
``cloud_sync_exception`` never had a policy, and its entry in
``scripts/ci/consumer_contract_baseline.json`` is removed in the same commit.

Verified 2026-09-28, before writing this:

* A quoted-key grep over every tracked file finds the four keys only in their
  seed sites (``settings_defaults.DEFAULTS`` + ``METADATA`` and
  ``0000_baseline.seeds.sql``) and the generated
  ``docs/reference/app-settings.md``. All are removed or regenerated in this
  commit. They are not in the free-tier brain seed.
* On prod (read-only) the rows hold their seeded values (``360`` / ``discord`` /
  ``log_only`` / ``warn``), were never updated, and have a NULL ``last_read_at``.
  They were created 2026-05-31. The three findings this kind ever produced
  (2026-05-14 to 05-16) came before that, so these policies never governed one.
  There has been none since; ``cloud_sync_exception`` never fired at all.
* Why the sync went: on prod both worker containers run ``DEPLOYMENT_MODE=worker``
  with ``CLOUD_DATABASE_URL`` unset and ``DATABASE_URL`` pointing at the local
  Postgres, so ``push_post`` opened two pools on the same database and upserted
  each post over itself. The public site reads static JSON from R2 and has no
  database client. Full evidence is in Glad-Labs/poindexter#1112.

Removing seeded data edits the seed sources too, or the next seeding pass
re-inserts the row, and ``scripts/ci/settings_seed_drift_lint.py`` enforces that
for every key listed in ``ORPHANED_KEYS``. Historical ``audit_log`` rows for the
two finding kinds are left alone: they are the record of what happened, and
retention prunes them.

The ``sync_metrics`` table the class wrote is NOT dropped here. It is in the
baseline schema, holds 138 rows frozen since 2026-04-09, and nothing reads it
once the class is gone, but dropping it deletes data and nobody asked for that.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Listed explicitly rather than by prefix/LIKE so this can never widen to a key
# that is still live: every other ``findings.<kind>.*`` policy has an emitter.
ORPHANED_KEYS = (
    "findings.cloud_sync_returned_false.cooldown_minutes",
    "findings.cloud_sync_returned_false.delivery",
    "findings.cloud_sync_returned_false.fallback",
    "findings.cloud_sync_returned_false.min_severity",
)


async def up(pool) -> None:
    """Delete the orphaned rows. No-op on installs that never had them."""
    async with pool.acquire() as conn:
        deleted = await conn.fetch(
            "DELETE FROM app_settings WHERE key = ANY($1::text[]) RETURNING key",
            list(ORPHANED_KEYS),
        )
    logger.info(
        "Migration drop_the_cloud_sync_finding_policy_settings_orphaned_by_the_sync_service_removal: "
        "applied (%d/%d orphaned key(s) deleted: %s)",
        len(deleted),
        len(ORPHANED_KEYS),
        ", ".join(sorted(r["key"] for r in deleted)) or "none present",
    )


async def down(pool) -> None:
    """Re-create the rows with the values, category and types they held on prod.

    Structure-only restore: no finding of this kind is emitted any more, so the
    policy is inert and re-adding it changes no behaviour.
    """
    description = (
        "RETIRED 2026-09-28 -- no emitter. Delivery policy for the "
        "cloud_sync_returned_false finding, whose only source was "
        "publish_service._sync_published_post, deleted with SyncService "
        "(Glad-Labs/poindexter#1112). Restored by a migration rollback; "
        "safe to delete."
    )
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active, value_type)
            VALUES ($1, $2, 'observability', $3, false, true, $4)
            ON CONFLICT (key) DO NOTHING
            """,
            [
                (
                    "findings.cloud_sync_returned_false.cooldown_minutes",
                    "360",
                    description,
                    "integer",
                ),
                ("findings.cloud_sync_returned_false.delivery", "discord", description, "string"),
                ("findings.cloud_sync_returned_false.fallback", "log_only", description, "string"),
                ("findings.cloud_sync_returned_false.min_severity", "warn", description, "string"),
            ],
        )
    logger.info(
        "Migration drop_the_cloud_sync_finding_policy_settings_orphaned_by_the_sync_service_removal: "
        "reverted (%d orphaned key(s) restored)",
        len(ORPHANED_KEYS),
    )
