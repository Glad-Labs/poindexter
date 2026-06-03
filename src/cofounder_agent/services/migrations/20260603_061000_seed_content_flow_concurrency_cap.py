"""Migration 20260603_061000_seed_content_flow_concurrency_cap: cap content-flow concurrency

Seeds the two app_settings keys that cap how many ``content_generation_flow``
runs Prefect executes simultaneously (Glad-Labs/poindexter#578).

Why
---
Stress test 2026-05-31: each concurrent content pipeline loads an LLM + SDXL
onto the single 5090, so simultaneous flow runs are a direct VRAM lever.

* **3 concurrent**: GPU ~92% util, VRAM ~19.5/32.6 GB (~60%) — stable,
  healthy headroom.
* **5 concurrent**: VRAM pinned at ~32.0/32.6 GB (~98%) — no OOM observed
  (Ollama self-gates), but one model-load from the edge.

The work-pool ``concurrency_limit`` is the native Prefect cap on simultaneous
runs, but it had no DB-configurable safe default and no guardrail: an operator
(or a stress test) could raise ``prefect_content_flow_concurrency`` to any
value and silently exhaust VRAM.

Keys seeded (DB-first config per project rule):

* ``prefect_content_flow_concurrency`` (``3``) — the work-pool concurrency
  applied by ``scripts/deploy_content_flow.py`` (was an undocumented hardcoded
  default of 1; 3 is the documented safe production value).
* ``content_flow_max_concurrency`` (``3``)     — hard safety ceiling for this
  GPU. The deploy script FAILS LOUD when the requested concurrency exceeds
  this ceiling (``resolve_safe_concurrency``), so a fat-fingered value aborts
  the deploy instead of pinning the GPU at ~98% VRAM. Raise only on a bigger
  GPU.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — never clobbers an
operator-tuned value, and a re-run on an up-to-date DB is a no-op. A fresh
DB also gets these keys from ``settings_defaults.seed_all_defaults``; first
writer wins.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Key -> seeded default. Mirrors services/settings_defaults.py.
_DEFAULTS = {
    "prefect_content_flow_concurrency": "3",
    "content_flow_max_concurrency": "3",
}


async def up(pool) -> None:
    """Insert each content-flow concurrency setting row if absent."""
    async with pool.acquire() as conn:
        for key, value in _DEFAULTS.items():
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES ($1, $2)
                ON CONFLICT (key) DO NOTHING
                """,
                key,
                value,
            )
            logger.info(
                "Migration seed_content_flow_concurrency_cap: %s (%s)",
                key,
                result,
            )


async def down(pool) -> None:
    """Remove the seeded rows.

    Only deletes a row when it still holds the seeded default — an operator
    who tuned a key keeps their value (the down-migration shouldn't destroy
    operator config).
    """
    async with pool.acquire() as conn:
        for key, value in _DEFAULTS.items():
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1 AND value = $2",
                key,
                value,
            )
        logger.info(
            "Migration seed_content_flow_concurrency_cap down: removed default "
            "rows (operator-tuned values preserved)"
        )
