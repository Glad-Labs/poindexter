"""Migration 20260928_232730: drop the local_database_pool_*_size settings,
orphaned when DatabaseService's dual-pool mode was retired

ISSUE: Glad-Labs/poindexter#1115

``DatabaseService`` used to be able to open a second, "local" pool beside the
primary one, and ``local_database_pool_min_size`` / ``local_database_pool_max_size``
sized it. The commit that adds this migration retires that mode. There is one
pool, sized by ``database_pool_min_size`` / ``database_pool_max_size``, which
stay. The only readers of the two local keys were
``DatabaseService._preread_pool_size_settings`` and ``initialize()``, both
rewritten in the same commit.

Verified 2026-09-28, before writing this:

* A quoted-key grep over every tracked file finds the two keys only in their
  seed sites (``settings_defaults.DEFAULTS`` + ``METADATA``), the
  ``settings_categories.py`` override map and ``DatabaseService`` itself. All
  are removed or rewritten in this commit. Neither key is in
  ``0000_baseline.seeds.sql``, the free-tier brain seed,
  ``scripts/settings_defaults_extract.json`` or the generated
  ``docs/reference/app-settings.md``.
* On prod (read-only) the rows hold their seeded ``2`` / ``20``, were created
  2026-09-26 by ``seed_all_defaults`` and never updated. ``last_read_at`` is
  NULL, but that is not evidence they were unread: ``DatabaseService`` reads
  them with a direct pre-read query that does not stamp. Until this change the
  Prefect flow's redundant second pool was sized by them.

Removing seeded data edits the seed sources too, or the next seeding pass
re-inserts the row, and ``scripts/ci/settings_seed_drift_lint.py`` enforces that
for every key listed in ``ORPHANED_KEYS``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Listed explicitly rather than by prefix/LIKE so this can never widen to a key
# that is still live: ``database_pool_min_size`` / ``database_pool_max_size``
# share the suffix and now size the only pool.
ORPHANED_KEYS = (
    "local_database_pool_min_size",
    "local_database_pool_max_size",
)


async def up(pool) -> None:
    """Delete the orphaned rows. No-op on installs that never had them."""
    async with pool.acquire() as conn:
        deleted = await conn.fetch(
            "DELETE FROM app_settings WHERE key = ANY($1::text[]) RETURNING key",
            list(ORPHANED_KEYS),
        )
    logger.info(
        "Migration drop_the_local_database_pool_size_settings_orphaned_by_the_dual_pool_retirement: "
        "applied (%d/%d orphaned key(s) deleted: %s)",
        len(deleted),
        len(ORPHANED_KEYS),
        ", ".join(sorted(r["key"] for r in deleted)) or "none present",
    )


async def down(pool) -> None:
    """Re-create the rows with the values, category and types they held on prod.

    Structure-only restore: nothing reads these keys once the dual-pool mode is
    gone, so the values are inert and re-adding them changes no behaviour.
    """
    description = (
        "RETIRED 2026-09-28 -- no reader. Sized DatabaseService's second "
        "('local') pool, removed with the dual-pool mode "
        "(Glad-Labs/poindexter#1115). database_pool_min_size / "
        "database_pool_max_size size the only pool. Restored by a migration "
        "rollback; safe to delete."
    )
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active, value_type)
            VALUES ($1, $2, 'infrastructure', $3, false, true, 'integer')
            ON CONFLICT (key) DO NOTHING
            """,
            [
                ("local_database_pool_min_size", "2", description),
                ("local_database_pool_max_size", "20", description),
            ],
        )
    logger.info(
        "Migration drop_the_local_database_pool_size_settings_orphaned_by_the_dual_pool_retirement: "
        "reverted (%d orphaned key(s) restored)",
        len(ORPHANED_KEYS),
    )
