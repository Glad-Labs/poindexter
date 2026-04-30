"""Migration 0113: niche-aware topic discovery tables.

Adds the data layer for the RAG pivot + niche-aware topic discovery
design (docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md).

Tables:
- niches                       — first-class niche configuration
- niche_goals                  — weighted goals per niche (TRAFFIC, EDUCATION, ...)
- niche_sources                — per-niche source plugin toggles + weights
- topic_batches                — operator-interaction unit
- topic_candidates             — external (HN, dev.to, web_search) candidates
- internal_topic_candidates    — RAG-derived candidates (different shape)
- discovery_runs               — observability: when sweeps fire + what they produce

All UUIDs default via gen_random_uuid (pgcrypto extension already loaded).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS niches (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                slug            TEXT UNIQUE NOT NULL,
                name            TEXT NOT NULL,
                active          BOOLEAN NOT NULL DEFAULT true,
                target_audience_tags TEXT[] NOT NULL DEFAULT '{}',
                writer_prompt_override TEXT,
                writer_rag_mode TEXT NOT NULL DEFAULT 'TOPIC_ONLY'
                    CHECK (writer_rag_mode IN ('TOPIC_ONLY','CITATION_BUDGET','STORY_SPINE','TWO_PASS')),
                batch_size      INT NOT NULL DEFAULT 5 CHECK (batch_size BETWEEN 1 AND 20),
                discovery_cadence_minute_floor INT NOT NULL DEFAULT 60 CHECK (discovery_cadence_minute_floor >= 1),
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS niche_goals (
                niche_id   UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
                goal_type  TEXT NOT NULL
                    CHECK (goal_type IN ('TRAFFIC','EDUCATION','BRAND','AUTHORITY','REVENUE','COMMUNITY','NICHE_DEPTH')),
                weight_pct INT NOT NULL CHECK (weight_pct BETWEEN 0 AND 100),
                PRIMARY KEY (niche_id, goal_type)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS niche_sources (
                niche_id    UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
                source_name TEXT NOT NULL,
                enabled     BOOLEAN NOT NULL DEFAULT true,
                weight_pct  INT NOT NULL CHECK (weight_pct BETWEEN 0 AND 100),
                PRIMARY KEY (niche_id, source_name)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS topic_batches (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                niche_id     UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
                status       TEXT NOT NULL CHECK (status IN ('open','resolved','expired')),
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at   TIMESTAMPTZ NOT NULL,
                resolved_at  TIMESTAMPTZ,
                picked_candidate_id UUID,
                picked_candidate_kind TEXT CHECK (picked_candidate_kind IN ('external','internal') OR picked_candidate_kind IS NULL)
            )
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_one_open_batch_per_niche
                ON topic_batches (niche_id) WHERE status = 'open'
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS topic_candidates (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                batch_id        UUID NOT NULL REFERENCES topic_batches(id) ON DELETE CASCADE,
                niche_id        UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
                source_name     TEXT NOT NULL,
                source_ref      TEXT NOT NULL,
                title           TEXT NOT NULL,
                summary         TEXT,
                score           NUMERIC NOT NULL,
                score_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
                rank_in_batch   INT NOT NULL,
                operator_rank   INT,
                operator_edited_topic TEXT,
                operator_edited_angle TEXT,
                decay_factor    NUMERIC NOT NULL DEFAULT 1.0,
                carried_from_batch_id UUID REFERENCES topic_batches(id),
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (batch_id, source_name, source_ref)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS internal_topic_candidates (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                batch_id        UUID NOT NULL REFERENCES topic_batches(id) ON DELETE CASCADE,
                niche_id        UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
                source_kind     TEXT NOT NULL
                    CHECK (source_kind IN ('claude_session','brain_knowledge','audit_event','git_commit','decision_log','memory_file','post_history')),
                primary_ref     TEXT NOT NULL,
                supporting_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                distilled_topic TEXT NOT NULL,
                distilled_angle TEXT NOT NULL,
                score           NUMERIC NOT NULL,
                score_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
                rank_in_batch   INT NOT NULL,
                operator_rank   INT,
                operator_edited_topic TEXT,
                operator_edited_angle TEXT,
                decay_factor    NUMERIC NOT NULL DEFAULT 1.0,
                carried_from_batch_id UUID REFERENCES topic_batches(id),
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS discovery_runs (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                niche_id        UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
                started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at     TIMESTAMPTZ,
                candidates_generated      INT,
                candidates_carried_forward INT,
                batch_id        UUID REFERENCES topic_batches(id),
                error           TEXT
            )
        """)
        # Helpful indexes
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_topic_candidates_batch ON topic_candidates(batch_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_internal_topic_candidates_batch ON internal_topic_candidates(batch_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_discovery_runs_niche_started ON discovery_runs(niche_id, started_at DESC)")
        logger.info("Created niche topic-discovery tables (0113)")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for tbl in (
            "discovery_runs", "internal_topic_candidates", "topic_candidates",
            "topic_batches", "niche_sources", "niche_goals", "niches",
        ):
            await conn.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
        logger.info("Dropped niche topic-discovery tables (0113 down)")
