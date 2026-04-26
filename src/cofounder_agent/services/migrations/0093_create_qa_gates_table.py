"""Migration 0093: ``qa_gates`` table — declarative QA chain (GH-115).

The multi-model QA pipeline previously hardcoded reviewer order in
``services/multi_model_qa.py`` (programmatic_validator → llm_critic →
url_verifier → consistency → web_factcheck → vision_gate). Each gate's
enable flag was an individual ``app_settings`` row. Adding a custom
gate, reordering the chain, or wiring per-niche QA tweaks all required
code changes.

This table replaces that pattern. Each row describes ONE gate
instance: which reviewer plugin runs, in what order, whether failure
hard-blocks publishing, and per-instance config. The runtime walks
enabled rows ordered by ``(stage_name, execution_order)`` and dispatches
to the named reviewer.

Per-niche QA variations work via ``config.applies_to_styles`` — a
list of ``writing_style_id`` values the gate applies to. Empty list /
missing key = applies to all styles.

Adding a new gate is now: insert a row + write a reviewer entry point.
No edits to ``cross_model_qa.py`` or ``multi_model_qa.py``.

Migration is idempotent — uses ``CREATE TABLE IF NOT EXISTS`` and
``CREATE INDEX IF NOT EXISTS``. The seed migration (0094) uses
``ON CONFLICT DO NOTHING`` so reruns are safe.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


SQL_UP = """
CREATE TABLE IF NOT EXISTS qa_gates (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Stable slug — unique per gate instance. Matches the ``reviewer``
    -- field reported by ``ReviewerResult.reviewer`` so audit rows line
    -- up cleanly with table rows.
    name               text NOT NULL UNIQUE,
    -- Pipeline stage this gate executes in. Almost always ``qa`` today;
    -- reserved for future ``post_publish`` / ``pre_research`` chains.
    stage_name         text NOT NULL DEFAULT 'qa',
    -- Execution order within the stage. Use 100, 200, 300... so future
    -- inserts have room (insert at 150 to slot a gate between 100 and 200).
    execution_order    int  NOT NULL DEFAULT 100,
    -- Registered reviewer name. Looked up at runtime via the
    -- ``poindexter.reviewers`` entry_point group + the in-process
    -- builtin reviewer dispatch table. The builtin set is:
    --   programmatic_validator, llm_critic, url_verifier, consistency,
    --   web_factcheck, vision_gate, citation_verifier, topic_delivery,
    --   rendered_preview.
    reviewer           text NOT NULL,
    -- When true, gate failure rejects the post outright. When false,
    -- failure is logged + factored into the weighted score but does
    -- not veto. Mirrors the ``approved`` semantics in MultiModelQA but
    -- now configurable per row.
    required_to_pass   boolean NOT NULL DEFAULT true,
    -- Master enable flag. Disabled rows are skipped entirely — no
    -- inference cost, no audit row, no review entry.
    enabled            boolean NOT NULL DEFAULT true,
    -- Reviewer-specific config. Conventional keys:
    --   applies_to_styles   list[uuid] — empty/missing = all styles
    --   pass_threshold      int        — score below this = approved=False
    --   timeout_seconds     int        — override default per-gate timeout
    --   model               text       — override the gate's LLM
    -- Free-form so reviewers can add their own knobs without a schema
    -- change.
    config             jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Free-form operator metadata — descriptions, tags, owner.
    metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Observability counters. Updated by the QA runtime after each
    -- review so Grafana can chart per-gate rejection rate / latency
    -- without a join against pipeline_reviews.
    last_run_at        timestamptz,
    last_run_duration_ms int,
    last_run_status    text,           -- 'pass' | 'fail' | 'error'
    total_runs         bigint NOT NULL DEFAULT 0,
    total_rejections   bigint NOT NULL DEFAULT 0,
    last_error         text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

-- Primary read pattern: filter enabled rows in a stage, ordered by
-- execution_order. Composite index keeps the planner from sorting.
CREATE INDEX IF NOT EXISTS idx_qa_gates_stage_order
    ON qa_gates (stage_name, execution_order);

-- Secondary read pattern: dashboards querying just-enabled rows.
CREATE INDEX IF NOT EXISTS idx_qa_gates_enabled
    ON qa_gates (enabled);

CREATE OR REPLACE FUNCTION qa_gates_touch_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS qa_gates_touch_updated_at_trg ON qa_gates;
CREATE TRIGGER qa_gates_touch_updated_at_trg
    BEFORE UPDATE ON qa_gates
    FOR EACH ROW EXECUTE FUNCTION qa_gates_touch_updated_at();
"""


SQL_DOWN = """
DROP TRIGGER IF EXISTS qa_gates_touch_updated_at_trg ON qa_gates;
DROP FUNCTION IF EXISTS qa_gates_touch_updated_at();
DROP TABLE IF EXISTS qa_gates;
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_UP)
        logger.info("0093: Created qa_gates table + indexes + trigger")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_DOWN)
        logger.info("0093: Dropped qa_gates table")
