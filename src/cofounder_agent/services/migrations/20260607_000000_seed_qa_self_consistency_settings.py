"""Migration: seed app_settings + qa_gates for the qa.self_consistency atom.

Wires services/self_consistency_rail.py into the qa.* atom chain
(glad-labs-stack#621). Adds the three operator-tunable settings the rail
reads (self_consistency_enabled, self_consistency_sample_count,
self_consistency_threshold) and a qa_gates row (advisory-first, matching
every other new gate added in the 2026-06 batch).

Idempotent — ON CONFLICT DO NOTHING for app_settings; ON CONFLICT (name)
DO NOTHING for qa_gates.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_SETTINGS = [
    (
        "self_consistency_enabled",
        "false",
        "qa",
        "Enable the self-consistency rail (qa.self_consistency atom). "
        "Samples the writer model N times, embeds summaries, rejects if "
        "mean pairwise cosine similarity < self_consistency_threshold.",
    ),
    (
        "self_consistency_sample_count",
        "3",
        "qa",
        "Number of summary samples for the self-consistency rail. Higher = "
        "more accurate signal, higher Ollama cost. Default 3.",
    ),
    (
        "self_consistency_threshold",
        "0.55",
        "qa",
        "Minimum mean pairwise cosine similarity to pass the self-consistency "
        "rail. Range [0, 1]. Default 0.55.",
    ),
]

_GATE = {
    "name": "self_consistency",
    "stage_name": "qa",
    "execution_order": 310,   # After qa.consistency (300), before qa.web_factcheck (320).
    "reviewer": "self_consistency",
    "required_to_pass": False,   # Advisory-first.
    "enabled": True,
    "metadata": json.dumps({
        "description": "HalluCounter-style self-consistency gate",
        "rail": "self_consistency_rail",
        "atom": "qa.self_consistency",
    }),
}


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value, category, description in _SETTINGS:
            await conn.execute(
                """
                INSERT INTO app_settings
                  (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, false, true)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )

        await conn.execute(
            """
            INSERT INTO qa_gates
              (name, stage_name, execution_order, reviewer,
               required_to_pass, enabled, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            ON CONFLICT (name) DO NOTHING
            """,
            _GATE["name"], _GATE["stage_name"], _GATE["execution_order"],
            _GATE["reviewer"], _GATE["required_to_pass"], _GATE["enabled"],
            _GATE["metadata"],
        )

    logger.info(
        "Migration seed_qa_self_consistency_settings: seeded %d app_settings + 1 qa_gates row",
        len(_SETTINGS),
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        keys = [k for k, *_ in _SETTINGS]
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])", keys,
        )
        await conn.execute(
            "DELETE FROM qa_gates WHERE name = $1", _GATE["name"],
        )
    logger.info("Migration seed_qa_self_consistency_settings down: removed")
