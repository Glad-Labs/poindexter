"""Migration 20260506_131123: seed cli post create idempotency app_settings.

ISSUE: Glad-Labs/poindexter#338 (gate system polish — bullet
"Idempotency on `poindexter post create` — currently double-firing CLI
creates two posts").

Adds a small idempotency layer around the ``poindexter post create``
CLI so an operator who fat-fingers the command (double-tap, accidental
repeat, ambiguous output) doesn't end up with two near-duplicate
``posts`` rows + two parallel gate trees.

Schema change
-------------

One new column + supporting partial index on ``posts``:

- ``cli_idempotency_key TEXT NULL`` — set by the CLI to a stable
  hash derived from the create-intent inputs (slug, or
  sha256(topic+media+gates+operator)). Worker-created posts keep
  this NULL — only CLI-originated rows are deduped.
- ``idx_posts_cli_idempotency_key`` — partial index
  ``WHERE cli_idempotency_key IS NOT NULL`` so worker traffic doesn't
  pay for it. The CLI's lookup also constrains on
  ``created_at > NOW() - INTERVAL ...`` so old rows never collide
  with a legitimate new "same topic, weeks later" creation.

We deliberately did NOT introduce a separate ``cli_idempotency_keys``
table — a single nullable text column on the only consumer (``posts``)
is the smallest surface that solves it. If a second CLI surface ever
needs idempotency we can promote it to a table at that point.

Settings seeded
---------------

Three ``app_settings`` rows (category ``cli``):

- ``cli_post_create_idempotency_enabled`` (``true``) — master switch.
  Set to ``false`` to restore the old "every invocation inserts"
  behavior.
- ``cli_post_create_idempotency_window_minutes`` (``30``) — dedup
  window. A second invocation with the same idempotency key inside
  this window returns the existing post id; outside it, a fresh post
  is inserted.
- ``cli_post_create_idempotency_strategy``
  (``'slug_or_content_hash'``) — reserved for future variants
  (e.g. ``slug_only`` once the CLI accepts ``--slug`` first-class).

Idempotent: ``ADD COLUMN IF NOT EXISTS`` + ``CREATE INDEX IF NOT
EXISTS`` + ``ON CONFLICT (key) DO NOTHING`` so re-runs preserve any
operator-set value.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SEEDS: list[tuple[str, str, str]] = [
    (
        "cli_post_create_idempotency_enabled",
        "true",
        "Master switch for `poindexter post create` idempotency "
        "(#338). When 'true', a second invocation with the same "
        "computed key inside the window returns the existing post id "
        "instead of inserting a duplicate. Set 'false' to restore "
        "the legacy 'every invocation inserts' behavior.",
    ),
    (
        "cli_post_create_idempotency_window_minutes",
        "30",
        "Dedup window for `poindexter post create` (#338). A second "
        "invocation with the same idempotency key WITHIN this many "
        "minutes returns the existing post id. Outside the window the "
        "command creates a fresh post — protects fat-finger doubles "
        "while still allowing a legitimate re-creation of the same "
        "topic days later.",
    ),
    (
        "cli_post_create_idempotency_strategy",
        "slug_or_content_hash",
        "Reserved for future variants of the `poindexter post create` "
        "(#338) idempotency-key derivation. Today only "
        "'slug_or_content_hash' is implemented (slug if explicit, else "
        "sha256 of topic+media+gates+operator). Listed here so the "
        "knob exists in app_settings before the next strategy lands.",
    ),
]


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    """Apply the migration.

    Two parts: schema (column + index on ``posts``) then seeds (three
    ``app_settings`` rows). Both halves are guarded so the body is
    safe to re-run.
    """
    async with pool.acquire() as conn:
        # --- Schema: posts.cli_idempotency_key ---
        if await _table_exists(conn, "posts"):
            await conn.execute(
                "ALTER TABLE posts "
                "ADD COLUMN IF NOT EXISTS cli_idempotency_key TEXT"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_posts_cli_idempotency_key "
                "ON posts (cli_idempotency_key, created_at) "
                "WHERE cli_idempotency_key IS NOT NULL"
            )
            logger.info(
                "Migration 20260506_131123: posts.cli_idempotency_key "
                "column + partial index in place"
            )
        else:
            logger.info(
                "Table 'posts' missing -- skipping schema half of "
                "20260506_131123 (cli idempotency)"
            )

        # --- Seeds: app_settings rows ---
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Table 'app_settings' missing -- skipping seed half of "
                "20260506_131123 (cli idempotency)"
            )
            return

        inserted = 0
        for key, value, description in _SEEDS:
            result = await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, 'cli', $3, FALSE, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "Migration 20260506_131123: seeded %d/%d cli post-create "
            "idempotency settings (remaining were already set)",
            inserted, len(_SEEDS),
        )


async def down(pool) -> None:
    """Revert the migration.

    Drops the seed rows and the partial index. We deliberately leave
    the ``posts.cli_idempotency_key`` column in place — dropping a
    column is destructive and any historical idempotency keys are
    cheap to keep around (just a TEXT column, NULL on every old row).
    Document this here per ``docs/operations/migrations.md`` rather
    than re-running ``ALTER TABLE`` on a giant table during rollback.
    """
    async with pool.acquire() as conn:
        if await _table_exists(conn, "posts"):
            await conn.execute(
                "DROP INDEX IF EXISTS idx_posts_cli_idempotency_key"
            )
        if await _table_exists(conn, "app_settings"):
            for key, _value, _description in _SEEDS:
                await conn.execute(
                    "DELETE FROM app_settings WHERE key = $1",
                    key,
                )
        logger.info(
            "Migration 20260506_131123 rolled back: removed cli idempotency "
            "settings + partial index (column kept as no-op)"
        )
