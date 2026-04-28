"""Bulk-import existing prompts into Langfuse Prompt Management (#202 Phase 2b).

Reads from the in-tree YAML + DB prompt sources via UnifiedPromptManager,
pushes each as a versioned prompt into Langfuse with the ``production``
label so the Langfuse-first lookup in ``services/prompt_manager.py`` finds
them on the next worker restart.

Idempotent: re-running the script creates a new version of each prompt
(Langfuse versions prompts automatically), but the ``production`` label
moves to the latest version. Old versions remain in history for rollback.

Usage::

    docker exec poindexter-worker python -m scripts.import_prompts_to_langfuse

Or from the host (with bootstrap.toml resolved):

    cd src/cofounder_agent
    python -m scripts.import_prompts_to_langfuse
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any


async def main() -> int:
    # Lazy imports — keep top-of-file clean even when one of the deps
    # isn't available in the calling shell.
    try:
        from langfuse import Langfuse
    except ImportError:
        print(
            "ERROR: langfuse not installed. Run from inside the worker "
            "container (`docker exec poindexter-worker python -m scripts."
            "import_prompts_to_langfuse`) or `pip install langfuse>=3`.",
            file=sys.stderr,
        )
        return 1

    from services.prompt_manager import get_prompt_manager
    from services.site_config import SiteConfig

    # Build a SiteConfig + DB pool so prompt_manager can layer DB
    # overrides on top of YAML. We only need this for the load — the
    # writes go to Langfuse, not back to Postgres.
    pool = await _connect_pool()
    site_config = SiteConfig(pool=pool)
    try:
        await site_config.load(pool)
    except Exception as e:
        print(f"WARN: site_config load failed (continuing with YAML only): {e}",
              file=sys.stderr)

    pm = get_prompt_manager()
    try:
        await pm.load_from_db(pool, site_config=site_config)
    except Exception as e:
        print(f"WARN: prompt DB load failed (continuing with YAML only): {e}",
              file=sys.stderr)

    # Build the snapshot we'll push to Langfuse. Premium overrides win
    # when active; otherwise default DB > YAML. Mirrors the runtime
    # ``get_prompt`` priority so what we push matches what would have
    # been served had Langfuse not been in front.
    premium_active = pm._is_premium_active()
    snapshot: dict[str, dict[str, Any]] = {}
    for key, entry in pm.prompts.items():
        template = None
        if premium_active:
            template = pm._db_overrides_premium.get(key)
        if template is None:
            template = pm._db_overrides.get(key)
        if template is None:
            template = entry.get("template", "")

        meta = pm.metadata.get(key)
        snapshot[key] = {
            "template": template,
            "category": meta.category.value if meta else "uncategorized",
            "version": meta.version.value if meta else "v1.1",
            "description": meta.description if meta else "",
            "output_format": meta.output_format if meta else "text",
        }

    if not snapshot:
        print("No prompts found to import. Did UnifiedPromptManager init fail?",
              file=sys.stderr)
        return 1

    client = Langfuse()
    try:
        if not client.auth_check():
            print("Langfuse auth_check failed — verify LANGFUSE_HOST + keys.",
                  file=sys.stderr)
            return 1
    except Exception as e:
        print(f"Langfuse unreachable: {e}", file=sys.stderr)
        return 1

    print(f"Importing {len(snapshot)} prompts into Langfuse "
          f"(premium_active={premium_active})...")

    imported = 0
    skipped = 0
    failed = 0
    for key, info in sorted(snapshot.items()):
        try:
            client.create_prompt(
                name=key,
                prompt=info["template"],
                labels=["production"],
                tags=[info["category"]],
                config={
                    "category": info["category"],
                    "version": info["version"],
                    "description": info["description"],
                    "output_format": info["output_format"],
                    "imported_by": "import_prompts_to_langfuse.py",
                },
            )
            imported += 1
            print(f"  ✓ {key} ({info['category']})")
        except Exception as e:
            # ``create_prompt`` is idempotent at the name level — it
            # creates a new VERSION rather than failing. Real errors
            # are network or schema. Surface and continue.
            print(f"  ✗ {key}: {type(e).__name__}: {e}", file=sys.stderr)
            failed += 1

    client.flush()
    await pool.close()

    print(f"\nDone. imported={imported} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 2


async def _connect_pool() -> Any:
    """Connect to Postgres using the same DSN the worker uses."""
    import asyncpg
    from brain.bootstrap import resolve_database_url

    dsn = resolve_database_url()
    return await asyncpg.create_pool(dsn, min_size=1, max_size=2)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
