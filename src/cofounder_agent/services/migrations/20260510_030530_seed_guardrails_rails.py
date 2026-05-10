"""Seed guardrails-ai rail settings + qa_gates rows.

Lane D sub-issue 3 of ``glad-labs-stack#329`` wires guardrails-ai into
the multi_model_qa review chain alongside the DeepEval rails:

- ``guardrails_brand`` — wraps content_validator's fabrication
  patterns as a guardrails ``Validator``. Same regex detections as
  the DeepEval brand rail but routed through a different framework;
  cross-framework agreement/disagreement is itself a learnable
  signal.
- ``guardrails_competitor`` — flags when any operator-listed
  competitor brand name appears in the post body. Fills a gap
  DeepEval doesn't cover (accidental promotion of competitors in
  branded content).

Both gates ship as advisory (``required_to_pass=false``) — same
"learn the false-positive rate before promoting to a hard veto"
posture as the DeepEval rails. The competitor gate is also no-op
by default because the empty competitor list short-circuits before
the validator runs.

Idempotent: every INSERT uses ``ON CONFLICT DO NOTHING`` so re-
running the migration is a no-op.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


_SETTINGS = [
    (
        "guardrails_enabled",
        "true",
        "qa",
        "Master switch for the guardrails-ai reviewer rails (brand "
        "fabrication + competitor mention). Default 'true' so the new "
        "gates run advisory out-of-the-box; flip to 'false' to disable "
        "the entire framework wrapper.",
    ),
    (
        "guardrails_competitor_list",
        "",
        "qa",
        "Comma-separated list of competitor brand names to flag if "
        "they appear in a post body (case-insensitive, word-boundary "
        "match). Empty string = no enforcement (the gate skips "
        "entirely). Operator seeds the list once — every post "
        "thereafter gets the check for free.",
    ),
]


_GATES = [
    (
        "guardrails_brand",
        770,
        (
            "guardrails-ai brand-fabrication validator: same regex "
            "patterns as content_validator but routed through "
            "guardrails-ai's Validator/Guard framework. Cross-framework "
            "signal alongside the DeepEval brand rail. Advisory."
        ),
    ),
    (
        "guardrails_competitor",
        780,
        (
            "guardrails-ai competitor-mention validator: flags when any "
            "name in app_settings.guardrails_competitor_list appears in "
            "the post body. Skipped entirely when the list is empty. "
            "Advisory."
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
            "scaffold": "services/guardrails_rails.py",
            "description": description,
            "seeded_by_migration": "20260510_030530",
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
        "Migration 20260510_030530: guardrails-ai enabled flag + "
        "competitor list seeded; qa_gates rows added (advisory)."
    )
