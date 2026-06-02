"""Seed vision alt-text settings (services.image_captioner).

Adds the DB-driven knobs for the qwen3-vl vision captioner used by the
caption_images pipeline stage + the alt-text backfill:

- ``vision_alt_enabled``     — master switch (kill-switch for the stage)
- ``vision_alt_model``       — Ollama vision model identifier
- ``vision_alt_max_tokens``  — GENERATION budget (NOT the alt char budget).
  qwen3-vl reasons before answering; a small cap returns empty content, so
  this must be generous. Final alt length is enforced by ``alt_text_budget``.

Idempotent: ON CONFLICT (key) DO NOTHING keeps any operator-tuned value.
Imports only stdlib so it stays light for the migrations-smoke env.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SEEDS = [
    ("vision_alt_enabled", "true"),
    ("vision_alt_model", "qwen3-vl:30b"),
    ("vision_alt_max_tokens", "2048"),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value in _SEEDS:
            await conn.execute(
                "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO NOTHING",
                key,
                value,
            )
    logger.info("seed_vision_alt_settings: applied (%d keys)", len(_SEEDS))


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            [k for k, _ in _SEEDS],
        )
    logger.info("seed_vision_alt_settings: reverted")
