"""Migration 20260707_120000: add the opening_originality QA gate + reseed canonical_blog.

Adds the ``qa.opening_originality`` rail — a RAG self-echo net that flags a
draft whose OPENING near-duplicates an existing published post. Two convergence
steps for existing installs (fresh installs get the gate row from the Phase F
baseline seeds and the node from this same reseed):

  1. INSERT the ``opening_originality`` qa_gates row (advisory-first,
     ``required_to_pass=false``). CRITICAL: without this row the rail still runs,
     but ``MultiModelQA._mark_advisory_if_configured`` treats an *absent* gate
     name as required — so a near-duplicate opening would HARD-veto the post. The
     row makes the rail advisory (scores only) until an operator graduates it.

  2. Reseed the ``canonical_blog`` graph_def raw (unstamped) so the new
     ``qa_opening_originality`` node — inserted between ``qa_self_consistency``
     and ``qa_web_factcheck`` in ``canonical_blog_spec.py`` — reaches prod. The
     boot-time self-heal (``pipeline_architect.ensure_active_graph_defs_stamped``)
     re-stamps it against the current atom registry. Mirrors the media/podcast
     reseed (20260623_035500): the raw UPDATE + same-boot re-stamp converges
     fresh (baseline seeds the old-node graph_def → this UPDATE → re-stamp) and
     prod (existing graph_def → this UPDATE → re-stamp) alike.

Both steps run in one migration so the gate row is present the moment the node
first executes (migrations run before the worker claims tasks).

Seed-data note: the qa_gates row is ALSO in 0000_baseline.seeds.sql (fresh
installs + next-squash hygiene, per feedback_seed_data_in_baseline); this
migration is the existing-install convergence step, exactly like older gates
were added by migration before the Phase F squash folded them into the baseline.
The app_settings defaults (``opening_originality_enabled`` /
``opening_originality_max_similarity``) are seeded every boot via
``settings_defaults.py``, so they need no migration.

Imports the spec (pure data — typing-only, no LangGraph) so migrations-smoke
applies it without a full app boot, mirroring the canonical_blog reseed pattern.
"""
from __future__ import annotations

import json
import logging

from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

logger = logging.getLogger(__name__)

# Same id as the 0000_baseline.seeds.sql row so ON CONFLICT (id) converges:
# fresh install → baseline inserts, this no-ops; prod → this inserts.
_GATE_ID = "7a3f9e21-4c8b-4d15-9e6a-2b1c8f0d3a57"

_GATE_METADATA = json.dumps({
    "atom": "qa.opening_originality",
    "rail": "opening_originality",
    "description": (
        "RAG self-echo net — flags a draft whose opening near-duplicates an "
        "existing published post. Advisory-first."
    ),
})


async def up(pool) -> None:
    async with pool.acquire() as conn:
        gate_result = await conn.execute(
            """
            INSERT INTO qa_gates
                (id, name, stage_name, execution_order, reviewer,
                 required_to_pass, enabled, config, metadata)
            VALUES ($1, 'opening_originality', 'qa', 315, 'opening_originality',
                    false, true, '{}'::jsonb, $2::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            _GATE_ID,
            _GATE_METADATA,
        )
        graph_result = await conn.execute(
            "UPDATE pipeline_templates "
            "SET graph_def = $1::jsonb, updated_at = now() "
            "WHERE slug = 'canonical_blog' AND active = true",
            json.dumps(CANONICAL_BLOG_GRAPH_DEF),
        )
        logger.info(
            "add_opening_originality_qa_gate up: qa_gates=%s, canonical_blog "
            "reseed=%s (boot self-heal re-stamps the graph_def to current "
            "contracts)",
            gate_result,
            graph_result,
        )


async def down(pool) -> None:
    # One-way: the graph_def reseed is re-stamped by the boot self-heal, and the
    # qa_gates row is advisory (harmless) if left. Same posture as
    # 20260623_035500_reseed_media_podcast_graph_defs.
    logger.info(
        "add_opening_originality_qa_gate down: no-op (boot self-heal owns stamping)"
    )
