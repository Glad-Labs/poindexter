"""Migration 0152: seed atom prompts into prompt_templates.

Per ``feedback_prompts_must_be_db_configurable``: every LLM prompt
is DB-configurable. The atom prompts (narrate_bundle,
review_with_critic, pipeline_architect) shipped this session as
inline Python constants; this migration seeds the canonical YAML
defaults into ``prompt_templates`` so:

1. The runtime DB-override path lights up (UnifiedPromptManager.load_from_db
   reads prompt_templates rows on top of YAML; operators editing the
   row tune the prompt without a code deploy).
2. Langfuse can see/edit the prompts (when the planned wrapper that
   syncs prompt_templates ↔ Langfuse ships).
3. The premium-prompt tier (migration 0141: source='premium' rows
   override source='default' when premium_active=true) works for
   atom prompts too.

The YAML files at ``prompts/atoms.yaml`` remain the canonical source
for first-boot defaults — this migration just makes them queryable
by SQL + visible to the Langfuse dashboard. The atom code already
calls ``get_prompt_manager().get_prompt(key)`` which prefers DB
overrides over YAML when both exist.

Idempotent: ON CONFLICT DO NOTHING on the unique key. If the operator
later edits the row directly, the next migration re-run preserves
the operator edit. To re-seed from YAML, the operator manually
deletes the row and re-runs.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Memory: ``feedback_prompts_must_be_db_configurable``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def _load_atom_yaml() -> list[dict]:
    """Load prompts/atoms.yaml relative to the cofounder_agent root."""
    # services/migrations -> services -> cofounder_agent -> prompts
    yaml_path = Path(__file__).resolve().parent.parent.parent / "prompts" / "atoms.yaml"
    if not yaml_path.exists():
        logger.warning("Migration 0152: prompts/atoms.yaml not found at %s", yaml_path)
        return []
    try:
        with open(yaml_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or []
    except Exception as exc:
        logger.warning("Migration 0152: yaml load failed: %s", exc)
        return []


async def up(pool) -> None:
    entries = _load_atom_yaml()
    if not entries:
        logger.info("Migration 0152: no atom prompts to seed")
        return
    async with pool.acquire() as conn:
        seeded = 0
        for entry in entries:
            key = entry.get("key", "").strip()
            template = entry.get("template", "")
            if not key or not template:
                continue
            await conn.execute(
                """
                INSERT INTO prompt_templates
                  (key, category, description, template, variables,
                   version, is_active, source)
                VALUES ($1, $2, $3, $4, $5, 1, true, 'default')
                ON CONFLICT (key) DO NOTHING
                """,
                key,
                entry.get("category", "utility"),
                entry.get("description", ""),
                template,
                "",  # variables — empty string for system prompts with no
                     # placeholders; UnifiedPromptManager.format() is a
                     # no-op when no placeholders exist.
            )
            seeded += 1
            logger.info("Migration 0152: seeded prompt_templates['%s']", key)
        logger.info(
            "Migration 0152: seeded %d atom prompt(s) into prompt_templates "
            "(skipped existing keys)",
            seeded,
        )


async def down(pool) -> None:
    entries = _load_atom_yaml()
    if not entries:
        return
    async with pool.acquire() as conn:
        for entry in entries:
            key = entry.get("key", "").strip()
            if key:
                await conn.execute(
                    "DELETE FROM prompt_templates WHERE key = $1 AND source = 'default'",
                    key,
                )
        logger.info(
            "Migration 0152 down: removed %d atom prompt rows from prompt_templates",
            len(entries),
        )
