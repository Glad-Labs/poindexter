"""Migration: seed dev_diary atom graph_def (swap dev_diary onto the atom path).

ISSUE: Glad-Labs/poindexter#355 follow-through — operator request 2026-06-02
("canonical_blog and dev_diary should both be going through the atoms").

dev_diary's ``pipeline_templates`` row previously carried a node-less
factory-pointer shim (``{"factory": "services.pipeline_templates.dev_diary"}``),
which ``load_active_graph_def`` ignores (no ``"nodes"`` key → returns None → the
runner falls back to the legacy Python factory). This replaces that shim with
the real atom node/edge spec (``DEV_DIARY_GRAPH_DEF``: 4 atoms —
verify_task → narrate_bundle → source_featured_image → finalize_task).

With ``pipeline_use_graph_def=true`` (already the prod default), the runner now
finds ``"nodes"`` for dev_diary and compiles + runs the atoms via
``build_graph_from_spec`` instead of the factory — so canonical_blog AND
dev_diary both run through atoms. The per-task experiment/variant assignment
still flows through ``state`` (writer-model A/B harness preserved).

The legacy dev_diary factory in ``services/pipeline_templates/__init__.py``
stays as a fallback until this graph_def is verified in prod, then is retired in
a follow-up. Idempotent: ON CONFLICT (slug) DO UPDATE bumps version 1 → 2.
"""

from __future__ import annotations

import json
import logging

from services.dev_diary_spec import DEV_DIARY_GRAPH_DEF

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    payload = json.dumps(DEV_DIARY_GRAPH_DEF)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pipeline_templates
              (slug, name, description, version, active, graph_def, created_by)
            VALUES ('dev_diary', 'Dev Diary',
                    'Atom-composed dev_diary pipeline (#355 follow-through)',
                    2, true, $1::jsonb, 'migration')
            ON CONFLICT (slug) DO UPDATE
              SET graph_def   = EXCLUDED.graph_def,
                  name        = EXCLUDED.name,
                  description = EXCLUDED.description,
                  version     = EXCLUDED.version,
                  active      = EXCLUDED.active,
                  updated_at  = NOW()
            """,
            payload,
        )
    logger.info("Migration seed_dev_diary_graph_def: applied (dev_diary -> atom graph_def)")


async def down(pool) -> None:
    # Revert dev_diary to the node-less factory-pointer shim so the runner
    # falls back to the legacy Python factory.
    payload = json.dumps({"factory": "services.pipeline_templates.dev_diary"})
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, version = 1, "
            "updated_at = NOW() WHERE slug = 'dev_diary'",
            payload,
        )
    logger.info("Migration seed_dev_diary_graph_def down: reverted dev_diary to factory shim")
