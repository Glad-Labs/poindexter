"""
Migration 0062: Create decision_log table.

Every ML/AI decision in the pipeline gets logged here — image selection,
model choice, topic scoring, publish/reject, etc. This is the training
data for self-improvement: outcomes feed back into future decisions.

The standard pattern is:
  1. Gather context
  2. Agent reasons → structured decision
  3. Execute the decision
  4. Log context + decision + outcome here
  5. Future agents query past decisions to improve
"""

MIGRATION_ID = "0062"
DESCRIPTION = "Create decision_log table for ML decision tracking"


async def up(conn) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- What decision was made
            decision_type TEXT NOT NULL,          -- 'image_source', 'model_selection', 'topic_score', 'publish_reject', etc.
            decision_point TEXT NOT NULL,          -- 'image_decision_agent', 'task_executor', 'content_router', etc.

            -- Context: what inputs the agent considered
            context JSONB NOT NULL DEFAULT '{}',   -- task_id, topic, category, content_preview, available_options, etc.

            -- Decision: what the agent chose
            decision JSONB NOT NULL DEFAULT '{}',  -- source, style, prompt, model, score, action, reasoning, etc.

            -- Outcome: what happened after the decision
            outcome JSONB DEFAULT NULL,            -- success, quality_score, engagement, user_approval, error, etc.
            outcome_recorded_at TIMESTAMPTZ DEFAULT NULL,

            -- Associations
            task_id TEXT DEFAULT NULL,             -- content_tasks.task_id if applicable
            post_id UUID DEFAULT NULL,             -- posts.id if applicable

            -- Metadata
            model_used TEXT DEFAULT NULL,          -- which LLM made the decision
            duration_ms INTEGER DEFAULT NULL,      -- how long the decision took
            cost_usd NUMERIC(10,6) DEFAULT 0,     -- cost of the decision (inference)

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        -- Index for querying decisions by type (e.g. all image decisions)
        CREATE INDEX IF NOT EXISTS idx_decision_log_type ON decision_log (decision_type);

        -- Index for querying decisions for a specific task
        CREATE INDEX IF NOT EXISTS idx_decision_log_task ON decision_log (task_id) WHERE task_id IS NOT NULL;

        -- Index for querying decisions by time (recent decisions for learning)
        CREATE INDEX IF NOT EXISTS idx_decision_log_created ON decision_log (created_at DESC);

        -- Index for finding decisions that need outcome recording
        CREATE INDEX IF NOT EXISTS idx_decision_log_pending_outcome ON decision_log (outcome_recorded_at)
            WHERE outcome_recorded_at IS NULL AND outcome IS NULL;
    """)


async def down(conn) -> None:
    await conn.execute("DROP TABLE IF EXISTS decision_log CASCADE;")
