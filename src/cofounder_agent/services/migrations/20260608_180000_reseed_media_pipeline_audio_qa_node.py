"""Migration: re-seed media_pipeline graph_def with the Phase-2 qa.audio node.

The Phase-1 re-seed chain (plans 4-6, migrations 120000-150000) produced:
  load_scripts → transcribe_narration → render_long → render_short → media_qa → END

Plan 8 / migration 20260608_170000 added the ``persist_media`` tail:
  … → media_qa → persist_media → END

This migration (Phase 2 of #1193) inserts a ``qa_audio`` node (``qa.audio``)
between ``transcribe_narration`` and ``render_long_video``, so audio quality
checks (silence, volume, duration) fire BEFORE the GPU-heavy render step.
The complete final graph is:

  load_scripts → transcribe_narration → qa_audio → render_long_video →
  render_short_video → media_qa → persist_media → END

Like all prior seeds, the template stays ``active=true`` but dormant behind
``media_pipeline_trigger_enabled`` (default ``false``) until Stage-2 is
deliberately enabled for prod.

IMPORTANT: imports only stdlib + the pure-data spec dict (no LangGraph /
template_runner) so the migrations-smoke CI step can apply it without a full
app boot.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Pure-data spec — no heavy deps, safe for migrations-smoke CI.
from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF  # noqa: E402


async def up(pool) -> None:
    graph_def_json = json.dumps(MEDIA_PIPELINE_GRAPH_DEF)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO pipeline_templates
                (slug, name, description, version, active, graph_def, created_by)
            VALUES ('media_pipeline', 'Media Pipeline', $1, 1, true, $2::jsonb, 'factory')
            ON CONFLICT (slug) DO UPDATE
               SET graph_def   = EXCLUDED.graph_def,
                   description  = EXCLUDED.description,
                   version      = EXCLUDED.version,
                   active       = EXCLUDED.active,
                   updated_at   = NOW()
            """,
            MEDIA_PIPELINE_GRAPH_DEF["description"],
            graph_def_json,
        )
    logger.info(
        "Migration reseed_media_pipeline_audio_qa_node up: re-seeded "
        "media_pipeline graph_def with Phase-2 qa.audio node (#1193). "
        "Final graph: load_scripts → transcribe_narration → qa_audio → "
        "render_long_video → render_short_video → media_qa → persist_media → END. "
        "result=%s",
        result,
    )


async def down(pool) -> None:  # noqa: ARG001
    # No-op reversal: this is a re-seed. The media_pipeline row is
    # intentionally retained — the spine migration owns the row's lifecycle.
    logger.info(
        "Migration reseed_media_pipeline_audio_qa_node down: no-op — "
        "qa.audio node re-seed; media_pipeline row intentionally retained.",
    )
