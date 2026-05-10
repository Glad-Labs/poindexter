"""Seed thresholds + qa_gates rows for the two new DeepEval rails.

Lane D sub-issue 1 of ``glad-labs-stack#329`` extends the DeepEval
rail beyond the existing brand-fabrication metric:

- ``deepeval_g_eval`` — chain-of-thought LLM-judge metric grading
  the post against an operator-defined criterion (default:
  groundedness + internal consistency + no invented facts).
- ``deepeval_faithfulness`` — DeepEval's built-in
  ``FaithfulnessMetric`` checks every claim in the post against
  the research bundle the writer was given.

Both run through ``deepeval_judge_model`` (default
``glm-4.7-5090`` — Matt's local thinking model, free at the point
of use). Operators on cloud OpenAI-compat keys can override per
``app_settings``.

Both gates ship as advisory (``required_to_pass=false``) — the
goal is to learn the false-positive rate against published-post
archives before promoting them to publish-blockers.

Idempotent: every INSERT uses ``ON CONFLICT DO NOTHING`` so re-
running the migration is a no-op.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


_SETTINGS = [
    (
        "deepeval_threshold_g_eval",
        "0.7",
        "qa",
        "Threshold (0–1) above which the DeepEval g-eval reviewer marks "
        "the post as approved. Default 0.7 — anything below means the "
        "judge model thinks the post is weakly grounded against the "
        "criterion. Edit deepeval_g_eval_criterion to retune what "
        "'good' means without a code change.",
    ),
    (
        "deepeval_threshold_faithfulness",
        "0.8",
        "qa",
        "Threshold (0–1) above which the DeepEval faithfulness reviewer "
        "marks the post as approved. Default 0.8 — at least 80%% of "
        "claims must be attributable to a chunk of the research bundle "
        "the writer was given. Posts that don't have research_sources "
        "skip the metric entirely.",
    ),
    (
        "deepeval_g_eval_criterion",
        (
            "The output is well-grounded in the input topic, internally "
            "consistent across paragraphs, and does not invent specific "
            "facts, names, statistics, or quotes that lack support."
        ),
        "qa",
        "Criterion text the DeepEval g-eval judge model uses to grade "
        "the post. Operators can rewrite this to emphasize different "
        "axes (brand voice, tone, technical accuracy) without a code "
        "change. Keep it concise — the judge derives its own "
        "step-by-step rubric from this single sentence.",
    ),
    (
        "deepeval_judge_model",
        "glm-4.7-5090",
        "qa",
        "LLM model identifier used by the DeepEval g-eval and "
        "faithfulness reviewers. Default 'glm-4.7-5090' (Matt's local "
        "thinking model — free at the point of use). Operators on "
        "cloud OpenAI-compat keys can override to e.g. 'gpt-4o-mini' "
        "for a stronger second opinion. Routed through DeepEval's "
        "standard provider configuration.",
    ),
]


_GATES = [
    (
        "deepeval_g_eval",
        750,
        (
            "DeepEval g-eval (LLM-judge): grades the post against "
            "deepeval_g_eval_criterion using a chain-of-thought "
            "evaluation. Advisory."
        ),
    ),
    (
        "deepeval_faithfulness",
        760,
        (
            "DeepEval FaithfulnessMetric: every claim in the post must "
            "be attributable to a chunk of the research bundle the "
            "writer was given. Skipped when there is no research "
            "context. Advisory."
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
            "scaffold": "services/deepeval_rails.py",
            "description": description,
            "seeded_by_migration": "20260510_022034",
        })
        await conn.execute(
            """
            INSERT INTO qa_gates (
                name, stage_name, execution_order, reviewer,
                required_to_pass, enabled, config, metadata
            )
            VALUES ($1, 'qa', $2, $1, false, true, '{}'::jsonb, $3::jsonb)
            ON CONFLICT (name) DO NOTHING
            """,
            name,
            execution_order,
            metadata,
        )

    logger.info(
        "Migration 20260510_022034: DeepEval g-eval + faithfulness "
        "thresholds seeded; qa_gates rows added (advisory)."
    )
