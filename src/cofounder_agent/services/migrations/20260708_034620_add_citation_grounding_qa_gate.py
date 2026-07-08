"""Migration 20260708_034620: add the citation_grounding QA gate + reseed canonical_blog.

Wires the grounded-LLM citation rail (``content.llm_reconcile_citations``, the
#765 follow-up) into the qa_gates telemetry system. Two convergence steps for
existing installs (fresh installs get the gate row from the Phase F baseline
seeds and the node from this same reseed):

  1. INSERT the ``citation_grounding`` qa_gates row (advisory,
     ``required_to_pass=false``). NOTE — unlike the standard rails, this row is
     NOT needed to keep the rail advisory. The rail is advisory-BY-CONSTRUCTION:
     ``content_llm_reconcile_citations._build_ungrounded_review`` always emits
     ``advisory=True`` and never consults ``qa_gates`` (it deliberately cannot
     graduate — it only fires on a detection, so a *required* + sometimes-silent
     rail would trip ``qa.aggregate``'s vacuous-pass guard and hard-reject every
     clean post). The row exists purely so ``qa_gates_db_writer.record_chain_run``
     can bump the run counter (else ``/d/qa-rails`` shows ``total_runs=0``) and so
     the rail is visible in ``poindexter qa-gates list``.

  2. Reseed the ``canonical_blog`` graph_def raw (unstamped) so the new
     ``llm_reconcile_citations`` node — inserted after ``reconcile_citations`` in
     ``canonical_blog_spec.py`` — reaches prod. The boot-time self-heal
     (``pipeline_architect.ensure_active_graph_defs_stamped``) re-stamps it
     against the current atom registry. Mirrors the opening_originality reseed
     (20260707_120000): the raw UPDATE + same-boot re-stamp converges fresh
     (baseline seeds the old-node graph_def → this UPDATE → re-stamp) and prod
     (existing graph_def → this UPDATE → re-stamp) alike.

Both steps run in one migration so the gate row is present the moment the node
first executes (migrations run before the worker claims tasks).

Seed-data note: the qa_gates row is ALSO in 0000_baseline.seeds.sql (fresh
installs + next-squash hygiene, per feedback_seed_data_in_baseline); this
migration is the existing-install convergence step. The app_settings defaults
(``citation_reconcile_llm_*``) are seeded every boot via ``settings_defaults.py``,
so they need no migration.

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
_GATE_ID = "d3d42c25-e59b-4009-98d1-4113fe5d483a"

_GATE_METADATA = json.dumps({
    "atom": "content.llm_reconcile_citations",
    "rail": "content_llm_reconcile_citations",
    "description": (
        "Grounded-LLM citation rail (#765 follow-up) — advisory-by-construction. "
        "Flags named sources the writer cited with no research-corpus match; "
        "emits only on a detection so it can never hard-gate. This row is for "
        "run-counter telemetry + /d/qa-rails visibility, not graduation."
    ),
})


async def up(pool) -> None:
    async with pool.acquire() as conn:
        gate_result = await conn.execute(
            """
            INSERT INTO qa_gates
                (id, name, stage_name, execution_order, reviewer,
                 required_to_pass, enabled, config, metadata)
            VALUES ($1, 'citation_grounding', 'qa', 165, 'citation_grounding',
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
            "add_citation_grounding_qa_gate up: qa_gates=%s, canonical_blog "
            "reseed=%s (boot self-heal re-stamps the graph_def to current "
            "contracts)",
            gate_result,
            graph_result,
        )


async def down(pool) -> None:
    # One-way: the graph_def reseed is re-stamped by the boot self-heal, and the
    # qa_gates row is advisory (harmless) if left. Same posture as
    # 20260707_120000_add_opening_originality_qa_gate.
    logger.info(
        "add_citation_grounding_qa_gate down: no-op (boot self-heal owns stamping)"
    )
