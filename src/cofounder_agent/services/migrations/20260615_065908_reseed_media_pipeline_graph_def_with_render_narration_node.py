"""Migration: re-seed media_pipeline graph_def with the render_narration node (#689).

The prior re-seed chain (migrations 20260608_120000–180000) produced:

  load_scripts → transcribe_narration → qa_audio → render_long_video →
  render_short_video → media_qa → persist_media → END

This migration (per-media narration, #689) inserts a ``render_narration`` node
(``media.render_narration``) between ``load_scripts`` and
``transcribe_narration``. It regenerates the long-form AND short-form video
narration audio from their OWN scripts + CTAs (``media.cta.video`` /
``media.cta.video_short``) into ``long_narration_audio_path`` /
``short_narration_audio_path`` — the channels the transcribe / audio-QA / render
nodes now read. This fixes the silent-video bug: Stage-2 never carried narration
audio across from Stage-1, so every rendered video shipped with a silent track.
The complete final graph is:

  load_scripts → render_narration → transcribe_narration → qa_audio →
  render_long_video → render_short_video → media_qa → persist_media → END

Like all prior seeds, the template stays ``active=true`` but dormant behind
``media_pipeline_trigger_enabled`` until Stage-2 is deliberately enabled.

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
        "Migration reseed_media_pipeline_graph_def_with_render_narration_node up: "
        "re-seeded media_pipeline graph_def with the render_narration node (#689). "
        "Final graph: load_scripts → render_narration → transcribe_narration → "
        "qa_audio → render_long_video → render_short_video → media_qa → "
        "persist_media → END. result=%s",
        result,
    )


async def down(pool) -> None:  # noqa: ARG001
    # No-op reversal: this is a re-seed. The media_pipeline row is
    # intentionally retained — the spine migration owns the row's lifecycle.
    logger.info(
        "Migration reseed_media_pipeline_graph_def_with_render_narration_node down: "
        "no-op — render_narration node re-seed; media_pipeline row intentionally "
        "retained.",
    )
