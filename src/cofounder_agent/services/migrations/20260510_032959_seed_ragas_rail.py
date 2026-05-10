"""Seed Ragas reviewer rail (Lane D #329 sub-issue 2).

Wires ``services/ragas_eval.py`` into ``MultiModelQA.review`` as a
single reviewer that averages faithfulness + answer_relevancy +
context_precision into one ReviewerResult; per-metric breakdown
lands in the feedback string.

Default-disabled (`qa_gates.enabled = false` for `ragas_eval`)
because each call costs ~6K judge-model tokens against the
operator-tuned ``cost_tier='budget'`` model. Operators opt in via:

    UPDATE qa_gates SET enabled = true WHERE name = 'ragas_eval';
    UPDATE app_settings SET value = 'true' WHERE key = 'ragas_enabled';

Both flips are needed: the qa_gates row controls whether the
reviewer runs at all in the chain; ``ragas_enabled`` is the rail-
internal kill switch the reviewer consults before doing anything
expensive (mirrors the deepeval / guardrails master switches).

Idempotent — every INSERT uses ``ON CONFLICT DO NOTHING``.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


_SETTINGS = [
    (
        "ragas_enabled",
        "false",
        "qa",
        "Master switch for the Ragas reviewer rail (faithfulness + "
        "answer_relevancy + context_precision averaged into a single "
        "ReviewerResult). Default 'false' because each call costs "
        "~6K judge-model tokens; flip to 'true' when you want the RAG "
        "quality signal in the QA chain. Also requires the qa_gates "
        "row 'ragas_eval' to be enabled.",
    ),
    # Note: ragas_judge_model is left unseeded — the rail resolves it
    # via cost_tier='budget' first, falling back to ragas_judge_model
    # if explicitly set. The Lane B sweep (#329 epic dep) introduced
    # the cost-tier API; this migration honors that path.
]


_GATES = [
    (
        "ragas_eval",
        790,
        (
            "Ragas RAG-quality reviewer: faithfulness + answer_relevancy "
            "+ context_precision averaged into one score. Disabled by "
            "default because each call is ~6K judge tokens. Advisory "
            "when enabled."
        ),
    ),
]


async def run_migration(conn) -> None:
    for key, value, category, description in _SETTINGS:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active)
            VALUES ($1, $2, $3, $4, false, true)
            ON CONFLICT (key) DO NOTHING
            """,
            key,
            value,
            category,
            description,
        )

    for name, execution_order, description in _GATES:
        metadata = json.dumps({
            "epic": "glad-labs-stack#329",
            "scaffold": "services/ragas_eval.py",
            "description": description,
            "seeded_by_migration": "20260510_032959",
        })
        # Note: enabled=false here (the only gate so far that ships
        # off-by-default — Ragas is too expensive to run on every
        # post without operator opt-in).
        await conn.execute(
            """
            INSERT INTO qa_gates (
                name, stage_name, execution_order, reviewer,
                required_to_pass, enabled, config, metadata
            )
            VALUES ($1, 'qa', $2, $1, false, false, '{}'::jsonb, $3::jsonb)
            ON CONFLICT (name) DO NOTHING
            """,
            name,
            execution_order,
            metadata,
        )

    logger.info(
        "Migration 20260510_032959: ragas_enabled flag seeded; "
        "ragas_eval qa_gates row added (disabled by default — opt-in)."
    )
