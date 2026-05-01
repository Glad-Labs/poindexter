"""Migration 0128: per-day compression summary tables (audit_log, brain_decisions).

Phase B+C of the memory-compression pipeline. The companion handler
``retention.summarize_to_table`` (services/integrations/handlers/
retention_summarize_to_table.py) aggregates older raw rows into a
single per-day summary row + LLM-generated paragraph, then deletes
the originals — preserving "vague but accurate memories of the past"
without keeping every row forever.

This migration creates two destination tables (``audit_log_summaries``
and ``brain_decision_summaries``), updates the existing retention
policy rows so a future ``enabled = TRUE`` flip will route through
the new handler, and seeds three ``app_settings`` knobs that govern
the LLM call.

Per Matt's "build everything THEN enable everything" preference, the
retention_policies rows stay ``enabled = FALSE`` here. A separate
one-line change flips them after all phases are built.

Idempotent: ``CREATE TABLE IF NOT EXISTS``, ``ON CONFLICT DO NOTHING``
on the settings inserts, and the ``UPDATE … WHERE … IS DISTINCT FROM``
guards on the policy updates so re-runs are no-ops.

Goal context (Matt 2026-05-01): the system should "know where it
started" — sharper memories of recent stuff, vague but accurate
memories of the past. Don't just delete; compress.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


SQL_UP = """
-- Per-day audit_log compression bucket. One row = one calendar day's
-- worth of audit_log rows that have aged past the retention window.
CREATE TABLE IF NOT EXISTS audit_log_summaries (
    id                 BIGSERIAL PRIMARY KEY,
    bucket_start       TIMESTAMPTZ NOT NULL,
    bucket_end         TIMESTAMPTZ NOT NULL,
    row_count          INTEGER NOT NULL,
    event_type_counts  JSONB NOT NULL DEFAULT '{}'::jsonb,
    severity_counts    JSONB NOT NULL DEFAULT '{}'::jsonb,
    top_sources        JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_excerpts     JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary_text       TEXT NOT NULL,
    summary_method     VARCHAR(32) NOT NULL DEFAULT 'ollama',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_summaries_bucket
    ON audit_log_summaries (bucket_start);


-- Per-day brain_decisions compression bucket.
CREATE TABLE IF NOT EXISTS brain_decision_summaries (
    id                 BIGSERIAL PRIMARY KEY,
    bucket_start       TIMESTAMPTZ NOT NULL,
    bucket_end         TIMESTAMPTZ NOT NULL,
    row_count          INTEGER NOT NULL,
    outcome_counts     JSONB NOT NULL DEFAULT '{}'::jsonb,
    avg_confidence     DOUBLE PRECISION,
    decision_excerpts  JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary_text       TEXT NOT NULL,
    summary_method     VARCHAR(32) NOT NULL DEFAULT 'ollama',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brain_decision_summaries_bucket
    ON brain_decision_summaries (bucket_start);
"""


SQL_DOWN = """
DROP TABLE IF EXISTS brain_decision_summaries;
DROP TABLE IF EXISTS audit_log_summaries;
"""


# Retention-policy rewrites. Each tuple:
#   (name, age_column, config_jsonb, description)
#
# We point ``handler_name`` at ``summarize_to_table`` so the runner
# (which dispatches by ``handler_name``) executes the new compression
# handler when the row is enabled. ``summarize_handler`` is also set
# to the same value to keep the documentary column in sync — older
# code paths/dashboards that read ``summarize_handler`` see the same
# answer as the runner.
_POLICY_UPDATES = [
    (
        "audit_log",
        "timestamp",
        # config tells the handler which columns to extract / count /
        # filter on. ``excerpts_filter`` becomes the WHERE fragment for
        # the error_excerpts column.
        (
            '{"bucket": "day",'
            ' "summary_table": "audit_log_summaries",'
            ' "text_columns": ["event_type", "source", "severity", "details"],'
            ' "count_columns": ["event_type", "severity"],'
            ' "top_source_column": "source",'
            ' "excerpts_column": "error_excerpts",'
            ' "excerpts_filter": "severity = ' "'error'" '",'
            ' "excerpts_text_columns": ["event_type", "source", "details"]'
            '}'
        ),
        "Compress audit_log into per-day summary rows + LLM paragraph",
    ),
    (
        "brain_decisions",
        "created_at",
        (
            '{"bucket": "day",'
            ' "summary_table": "brain_decision_summaries",'
            ' "text_columns": ["decision", "reasoning", "outcome", "confidence"],'
            ' "count_columns": ["outcome"],'
            ' "confidence_column": "confidence",'
            ' "excerpts_column": "decision_excerpts",'
            ' "excerpts_text_columns": ["decision", "reasoning", "outcome"]'
            '}'
        ),
        "Compress brain_decisions into per-day summary rows + LLM paragraph",
    ),
]


_SETTINGS = [
    (
        "memory_compression_summary_model",
        "gemma3:27b-it-qat",
        "memory_compression",
        "Ollama model used by retention.summarize_to_table for the per-day "
        "summary paragraph. Same default as embedding_collapse_summary_model "
        "for consistency — a single model swap covers both compression paths.",
    ),
    (
        "memory_compression_summary_timeout_seconds",
        "60",
        "memory_compression",
        "Per-call timeout (seconds) for the LLM summary generation in "
        "retention.summarize_to_table. On timeout the handler falls back "
        "to a joined-preview summary so a slow LLM doesn't kill the whole "
        "retention pass.",
    ),
    (
        "memory_compression_excerpts_per_bucket",
        "12",
        "memory_compression",
        "How many sample rows feed the LLM prompt and land in the "
        "{event_type}_excerpts JSONB column for each day-bucket. "
        "12 is a balance between prompt size and giving the model "
        "enough context to write a useful summary.",
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
        # 1) Summary tables + indexes
        await conn.execute(SQL_UP)
        logger.info(
            "0128: created audit_log_summaries + brain_decision_summaries"
        )

        # 2) Retention policy rewrites — only if the table exists
        if await _table_exists(conn, "retention_policies"):
            updated = 0
            for name, age_column, config_json, description in _POLICY_UPDATES:
                # IS DISTINCT FROM avoids touching rows that are already
                # in the desired state (re-running the migration is a
                # no-op rather than bumping updated_at on every run).
                result = await conn.execute(
                    """
                    UPDATE retention_policies
                       SET handler_name = 'summarize_to_table',
                           summarize_handler = 'summarize_to_table',
                           age_column = $2,
                           config = $3::jsonb,
                           metadata = jsonb_set(
                               COALESCE(metadata, '{}'::jsonb),
                               '{description}',
                               to_jsonb($4::text),
                               true
                           )
                     WHERE name = $1
                       AND (
                           handler_name IS DISTINCT FROM 'summarize_to_table'
                           OR summarize_handler IS DISTINCT FROM 'summarize_to_table'
                           OR age_column IS DISTINCT FROM $2
                           OR config IS DISTINCT FROM $3::jsonb
                       )
                    """,
                    name, age_column, config_json, description,
                )
                # asyncpg execute() returns "UPDATE N"
                try:
                    n = int(str(result).rsplit(" ", 1)[-1])
                except (ValueError, IndexError):
                    n = 0
                updated += n
            logger.info(
                "0128: updated %d retention_policies rows to summarize_to_table",
                updated,
            )
        else:
            logger.info(
                "0128: retention_policies table missing — skipping policy updates"
            )

        # 3) app_settings seeds
        if await _table_exists(conn, "app_settings"):
            seeded = 0
            for key, value, category, description in _SETTINGS:
                result = await conn.execute(
                    """
                    INSERT INTO app_settings
                        (key, value, category, description, is_secret)
                    VALUES ($1, $2, $3, $4, FALSE)
                    ON CONFLICT (key) DO NOTHING
                    """,
                    key, value, category, description,
                )
                if result == "INSERT 0 1":
                    seeded += 1
            logger.info(
                "0128: seeded %d new memory_compression setting(s) "
                "(existing operator values left untouched)",
                seeded,
            )
        else:
            logger.info(
                "0128: app_settings table missing — skipping setting seeds"
            )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        # Roll the policies back to the prior ttl_prune state. Same
        # IS DISTINCT FROM guard so a partial down run is also a no-op.
        if await _table_exists(conn, "retention_policies"):
            for name, _age_column, _config_json, _desc in _POLICY_UPDATES:
                await conn.execute(
                    """
                    UPDATE retention_policies
                       SET handler_name = 'ttl_prune',
                           summarize_handler = NULL,
                           age_column = 'created_at',
                           config = '{}'::jsonb
                     WHERE name = $1
                    """,
                    name,
                )
        if await _table_exists(conn, "app_settings"):
            for key, _value, _category, _description in _SETTINGS:
                await conn.execute(
                    "DELETE FROM app_settings WHERE key = $1", key,
                )
        await conn.execute(SQL_DOWN)
        logger.info(
            "0128 rolled back: dropped summary tables, restored ttl_prune policies"
        )
