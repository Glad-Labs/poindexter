"""Migration 20260530_000500_seed_structured_extraction_model: seed structured_extraction_model

Adds the ``structured_extraction_model`` app_setting that decouples the
model used for structured-JSON extraction calls (topic-discovery distill
+ candidate ranking) from the writer model.

Why
---
The 2026-05-28 content-generation stall: topic discovery's JSON calls
resolved to ``pipeline_writer_model`` (= ``glm-4.7-5090``, a *reasoning*
model). Under ``response_format=json_object`` that model emitted all its
tokens into a thinking channel and returned an EMPTY ``content`` field,
so ``json.loads("")`` crashed every discovery sweep. No topic batch
formed for ~2 days → the Prefect content flow drained an empty queue →
nothing generated.

``resolve_structured_model`` now reads this key (default ``gemma3:27b``,
a JSON-reliable instruct model) so an operator can keep a reasoning
writer model without breaking structured extraction.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — never clobbers an
operator-tuned value, and a re-run on an up-to-date DB is a no-op. A
fresh DB also gets this key from ``settings_defaults.seed_all_defaults``;
first writer wins.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_KEY = "structured_extraction_model"
_DEFAULT = "gemma3:27b"


async def up(pool) -> None:
    """Insert the structured_extraction_model row if absent."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES ($1, $2)
            ON CONFLICT (key) DO NOTHING
            """,
            _KEY,
            _DEFAULT,
        )
        logger.info(
            "Migration seed_structured_extraction_model: applied (%s)", result,
        )


async def down(pool) -> None:
    """Remove the seeded row.

    Only deletes the row when it still holds the seeded default — an
    operator who tuned it to a different model keeps their value (the
    down-migration shouldn't destroy operator config).
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1 AND value = $2",
            _KEY,
            _DEFAULT,
        )
        logger.info(
            "Migration seed_structured_extraction_model down: removed default row "
            "(operator-tuned values preserved)"
        )
