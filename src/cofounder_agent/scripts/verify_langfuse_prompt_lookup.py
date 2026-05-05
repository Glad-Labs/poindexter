"""Smoke test: prove UnifiedPromptManager hits Langfuse for a known key.

Activates SiteConfig + the prompt manager exactly the way the worker
does, then asks for a known atom prompt. Logs which source actually
served the value (Langfuse vs DB vs YAML) so we know Phase 1 of #47
is live before relying on it for production traffic.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def main() -> int:
    from poindexter.cli._bootstrap import ensure_secret_key, resolve_dsn

    ensure_secret_key()
    import asyncpg

    from services.prompt_manager import get_prompt_manager
    from services.site_config import SiteConfig

    pool = await asyncpg.create_pool(resolve_dsn(), min_size=1, max_size=2)
    site_config = SiteConfig(pool=pool)
    await site_config.load(pool)

    pm = get_prompt_manager()
    await pm.load_from_db(pool, site_config=site_config)

    key = "atoms.narrate_bundle.system_prompt"
    print(f"\nfetching {key} via UnifiedPromptManager.get_prompt(site_config=...)\n")
    text = pm.get_prompt(key, site_config=site_config)
    head = text[:240].replace("\n", " ")
    print(f"length: {len(text)} chars")
    print(f"head: {head}...")

    await pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
