"""Migration 0112: seed code-block density quality-gate settings (GH-234).

The content validator now warns when a tech-tagged post lacks enough
fenced code blocks to actually *demonstrate* the technique it's
describing. Pure-prose tech posts hurt EEAT signals — readers (and
ranking systems) treat "talks about Docker" as weaker than "shows
Docker working." See ``services/content_validator.py`` ::
``_check_code_block_density``.

This migration seeds the five ``app_settings`` rows that control the
behavior. All warning-level by design: the operator may legitimately
ship a non-code tech post (architecture overview, postmortem), so the
gate flags rather than rejects.

* ``code_density_check_enabled`` — master kill-switch. Default ``true``.
* ``code_density_tag_filter`` — comma-separated list of tags that
  qualify a post as "tech" for this rule. Default covers the common
  developer-content surface (technical, ai, programming, ml, python,
  javascript, rust, go).
* ``code_density_min_blocks_per_700w`` — fenced-block floor per 700
  prose words. Default ``1``.
* ``code_density_min_line_ratio_pct`` — minimum percentage of non-empty
  content lines that must live inside a fenced block, applied only to
  longer posts. Default ``20``.
* ``code_density_long_post_floor_words`` — word-count threshold below
  which the line-ratio sub-check is skipped. Default ``300`` per the
  issue spec ("for posts >300 words").

Idempotent: ``ON CONFLICT DO NOTHING`` leaves any operator-tuned value
alone. Safe to re-run. Down migration deletes the five rows.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_SEED_ROWS = (
    (
        "code_density_check_enabled",
        "true",
        "content",
        "GH-234: enable the code-block density quality gate. When true, "
        "tech-tagged posts that ship without enough fenced code blocks "
        "emit a (non-blocking) warning during multi_model_qa review.",
    ),
    (
        "code_density_tag_filter",
        "technical,ai,programming,ml,python,javascript,rust,go",
        "content",
        "GH-234: comma-separated list of tag/topic tokens that qualify "
        "a post as 'tech' for the code-block density rule. Matched "
        "case-insensitively against post tags + topic. Set to an empty "
        "string to disable the gate without touching the master flag.",
    ),
    (
        "code_density_min_blocks_per_700w",
        "1",
        "content",
        "GH-234: minimum fenced code blocks expected per 700 prose words "
        "in a tech post. The check skips posts under 200 prose words "
        "regardless. Set to 0 to disable just this sub-check.",
    ),
    (
        "code_density_min_line_ratio_pct",
        "20",
        "content",
        "GH-234: minimum percentage of non-empty content lines that "
        "must live inside a fenced code block, applied only to posts "
        "above code_density_long_post_floor_words. Set to 0 to disable "
        "just this sub-check.",
    ),
    (
        "code_density_long_post_floor_words",
        "300",
        "content",
        "GH-234: prose word-count threshold above which the line-ratio "
        "sub-check kicks in. Per the issue spec, short posts (<300 "
        "words) don't get the ratio check because a 200-word note "
        "doesn't need a code-heavy layout to be valuable.",
    ),
)


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
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Table 'app_settings' missing — skipping migration 0112"
            )
            return
        for key, value, category, description in _SEED_ROWS:
            await conn.execute(
                """
                INSERT INTO app_settings (
                    key, value, category, description, is_secret
                )
                VALUES ($1, $2, $3, $4, false)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
        logger.info(
            "Migration 0112: seeded %d code-density settings (if not already set)",
            len(_SEED_ROWS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        keys = [r[0] for r in _SEED_ROWS]
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1)", keys,
        )
        logger.info(
            "Migration 0112 rolled back: removed %d code-density settings",
            len(keys),
        )
