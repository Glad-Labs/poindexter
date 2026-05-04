"""Migration 0144: create ``pipeline_templates`` table.

Phase 4 of the dynamic-pipeline-composition spec. The table caches
LLM-composed pipeline graphs as named, slug-addressable templates so
the architect doesn't recompose from scratch every time the same intent
shows up.

Two ways rows land here:

1. **Hand-coded factories** — the Python factories registered in
   :mod:`services.pipeline_templates.TEMPLATES` (canonical_blog,
   dev_diary). Inserted during startup so the operator can list/inspect
   them via the same query path.
2. **Architect-LLM compositions** — :func:`services.pipeline_architect.cache_template`
   serializes a validated graph spec to ``graph_def`` JSONB and
   upserts here. Subsequent compose() calls with matching intents can
   short-circuit by looking up an existing slug.

Schema motivation:

- ``slug`` is the public handle. Matches ``pipeline_tasks.template_slug``
  via FK-on-text (no enforced FK so legacy NULL rows aren't blocked).
- ``graph_def`` JSONB stores the full spec from
  :func:`pipeline_architect.compose`. Hand-coded factories store
  ``{"factory": "<python_module_path>"}`` as a marker.
- ``version`` allows in-place revisions (architect re-composing a slug
  with a tweaked spec). Active rows are ``active = true``; old
  versions are kept for replay/audit but ignored by the runner.
- ``created_by`` is "architect_llm", "factory", or "operator" so audit
  trails distinguish hand-curated vs auto-composed templates.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Implements: Glad-Labs/poindexter#360.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


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
        if await _table_exists(conn, "pipeline_templates"):
            logger.info("Migration 0144: pipeline_templates already exists — skipping")
            return
        await conn.execute(
            """
            CREATE TABLE pipeline_templates (
              id           BIGSERIAL PRIMARY KEY,
              slug         TEXT NOT NULL UNIQUE,
              name         TEXT NOT NULL,
              description  TEXT NOT NULL DEFAULT '',
              version      INTEGER NOT NULL DEFAULT 1,
              active       BOOLEAN NOT NULL DEFAULT true,
              graph_def    JSONB NOT NULL DEFAULT '{}'::jsonb,
              created_by   TEXT NOT NULL DEFAULT 'operator',
              created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX idx_pipeline_templates_active "
            "ON pipeline_templates (active) WHERE active = true"
        )
        await conn.execute(
            "CREATE INDEX idx_pipeline_templates_created_by "
            "ON pipeline_templates (created_by)"
        )
        # Seed the two hand-coded factories so operator listings show
        # them alongside any future architect-composed slugs. The
        # graph_def is a marker — the runner reads from
        # ``services.pipeline_templates.TEMPLATES`` first and only falls
        # back to ``graph_def`` for architect-composed slugs.
        await conn.execute(
            """
            INSERT INTO pipeline_templates
              (slug, name, description, version, active, graph_def, created_by)
            VALUES
              ('canonical_blog', 'Canonical Blog Pipeline',
               '12-stage canonical content pipeline (regression baseline)',
               1, true,
               '{"factory": "services.pipeline_templates.canonical_blog"}'::jsonb,
               'factory'),
              ('dev_diary', 'Dev Diary Pipeline',
               'Bundle-narrating dev_diary template (atom-grain narrate)',
               1, true,
               '{"factory": "services.pipeline_templates.dev_diary"}'::jsonb,
               'factory')
            ON CONFLICT (slug) DO NOTHING
            """
        )
        logger.info(
            "Migration 0144: created pipeline_templates table + indexes + "
            "seeded canonical_blog and dev_diary factory rows"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS pipeline_templates")
        logger.info("Migration 0144 down: dropped pipeline_templates table")
