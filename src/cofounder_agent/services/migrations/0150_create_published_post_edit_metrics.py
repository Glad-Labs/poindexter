"""Migration 0150: create ``published_post_edit_metrics`` + auto-publish settings.

Per ``feedback_auto_publish_requires_edit_distance_track_record``:
auto-publish gates on edit-distance trending to zero across N
consecutive runs, NOT on quality_score alone. This migration builds
the data side of that gate:

1. ``published_post_edit_metrics`` table — one row per approve event,
   capturing pre-approve content snapshot + post-approve content
   snapshot + char/line diff counts. Lets the operator (and a future
   ML auto-tuner) see "across the last 7 dev_diary publishes, what
   was the median edit distance?" before deciding to flip auto-publish on.

2. ``dev_diary_auto_publish_threshold`` (default -1, DISABLED) — the
   quality_score floor. When < 0, the gate never fires regardless of
   the dry-run setting.

3. ``dev_diary_auto_publish_dry_run`` (default true) — forces the gate
   into observe-only mode. Logs "would have auto-published Y/N" via
   audit_log + capability_outcomes; never actually approves the task.
   The operator flips this to false ONLY after the dry-run logs show
   the gate's auto-publish decisions agree with operator manual approves
   across enough runs.

4. ``dev_diary_auto_publish_min_clean_runs`` (default 3) — additional
   gate: the trailing N publishes in this niche must have
   edit_distance < ``auto_publish_max_edit_distance`` (default 50
   chars). Both this AND the quality threshold must pass.

Both readings of feedback_human_approval are honored: the threshold
is precommitted operator policy (Reading B), AND the strict reading
applies until the operator explicitly opts in (Reading A holds by
default because dry_run defaults to true).

A/B testing utility: keep dry_run on, vary threshold to compare
auto-publish decisions vs. operator approves; switch off dry_run
once a stable threshold is found.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Memory: ``feedback_auto_publish_requires_edit_distance_track_record``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SETTINGS: list[tuple[str, str, str, str]] = [
    # (key, value, category, description)
    (
        "dev_diary_auto_publish_threshold",
        "-1",
        "publishing",
        "Quality_score floor for dev_diary auto-publish. Default -1 "
        "disables the gate entirely. Set to a value 0-100 (e.g. 85) "
        "to opt into auto-publish for runs scoring at or above the "
        "floor. ALSO requires dev_diary_auto_publish_dry_run=false.",
    ),
    (
        "dev_diary_auto_publish_dry_run",
        "true",
        "publishing",
        "When true (default), auto-publish gate runs in observe-only "
        "mode: logs 'would have auto-published Y/N' for each finalize "
        "but never actually approves. Set to false ONLY after the "
        "dry-run logs show consistent agreement with operator manual "
        "approves over multiple runs.",
    ),
    (
        "dev_diary_auto_publish_min_clean_runs",
        "3",
        "publishing",
        "Trailing N publishes that must have edit_distance < "
        "auto_publish_max_edit_distance for the gate to fire. "
        "Default 3 — stricter than 1 to avoid one-good-run bias.",
    ),
    (
        "dev_diary_auto_publish_max_edit_distance",
        "50",
        "publishing",
        "Char-level edit distance threshold for the 'clean run' "
        "criterion. Default 50 — trivial typo fixes pass; substantive "
        "rewording fails. Tune based on observed published_post_edit_metrics.",
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
    async with pool.acquire() as conn:
        # Part 1: edit-metrics table
        if not await _table_exists(conn, "published_post_edit_metrics"):
            await conn.execute(
                """
                CREATE TABLE published_post_edit_metrics (
                  id                BIGSERIAL PRIMARY KEY,
                  task_id           TEXT NOT NULL,
                  post_id           BIGINT,
                  niche_slug        TEXT,
                  category          TEXT,
                  approver          TEXT NOT NULL,
                  pre_approve_hash  TEXT NOT NULL,
                  post_approve_hash TEXT NOT NULL,
                  char_diff_count   INTEGER NOT NULL DEFAULT 0,
                  line_diff_count   INTEGER NOT NULL DEFAULT 0,
                  pre_approve_len   INTEGER NOT NULL DEFAULT 0,
                  post_approve_len  INTEGER NOT NULL DEFAULT 0,
                  approve_method    TEXT,
                  approved_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  metrics           JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await conn.execute(
                "CREATE INDEX idx_post_edit_metrics_niche "
                "ON published_post_edit_metrics (niche_slug, approved_at DESC) "
                "WHERE niche_slug IS NOT NULL"
            )
            await conn.execute(
                "CREATE INDEX idx_post_edit_metrics_category "
                "ON published_post_edit_metrics (category, approved_at DESC)"
            )
            await conn.execute(
                "CREATE INDEX idx_post_edit_metrics_task "
                "ON published_post_edit_metrics (task_id)"
            )
            logger.info(
                "Migration 0150: created published_post_edit_metrics + 3 indexes"
            )
        else:
            logger.info("Migration 0150: published_post_edit_metrics already exists — skipping")

        # Part 2: settings (default-disabled, dry-run-on)
        for key, value, category, description in _SETTINGS:
            await conn.execute(
                """
                INSERT INTO app_settings
                  (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, false, true)
                ON CONFLICT (key) DO UPDATE
                  SET description = EXCLUDED.description,
                      updated_at  = NOW()
                """,
                key, value, category, description,
            )
            logger.info("Migration 0150: seeded %s = %s", key, value)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS published_post_edit_metrics")
        for key, _, _, _ in _SETTINGS:
            await conn.execute("DELETE FROM app_settings WHERE key = $1", key)
        logger.info("Migration 0150 down: dropped table + removed settings")
