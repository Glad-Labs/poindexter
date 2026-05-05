"""Migration 0153: seed Langfuse prompt-management settings (empty placeholders).

Per Matt: every LLM prompt should be DB-configurable + Langfuse-visible.
UnifiedPromptManager.get_prompt now checks Langfuse first (label='production')
before falling through to the DB+YAML stack — but the Langfuse client
only initializes when these three settings have non-empty values.

This migration creates the placeholder rows so the operator can fill
them in via the existing settings CLI / UI without an additional
schema change. Until populated, prompt_manager logs an info message
and uses the DB+YAML fallback (existing behavior — no regression).

To activate Langfuse prompt management:

  1. Open the local Langfuse UI (http://100.81.93.12:3010 from the tailnet,
     or http://localhost:3010).
  2. Create or open the Glad Labs project; go to Settings → API Keys;
     generate a public + secret key pair.
  3. Set the three settings:
        UPDATE app_settings SET value = 'http://localhost:3010' WHERE key = 'langfuse_host';
        UPDATE app_settings SET value = 'pk-lf-...' WHERE key = 'langfuse_public_key';
        UPDATE app_settings SET value = 'sk-lf-...' WHERE key = 'langfuse_secret_key';
  4. Run scripts/import_prompts_to_langfuse.py to push current YAML +
     prompt_templates rows into Langfuse with the 'production' label.
  5. Edit prompts in the Langfuse UI; changes take effect on the next
     get_prompt call (Langfuse SDK caches with default 60s TTL).

Per ``feedback_no_secrets_prompts``: the secret_key field is marked
is_secret=true so it gets encrypted at rest by the existing
app_settings_auto_encrypt_trigger (migration 0130).

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Memory: ``feedback_prompts_must_be_db_configurable``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SEEDS: list[tuple[str, str, str, str, bool]] = [
    # (key, value, category, description, is_secret)
    (
        "langfuse_host",
        "",
        "observability",
        "Langfuse base URL for prompt management + tracing. Default empty "
        "= Langfuse disabled, prompts resolve via DB+YAML fallback. Set "
        "to e.g. http://localhost:3010 (local container) or your "
        "Langfuse Cloud URL to enable. UnifiedPromptManager picks up "
        "the change on the next worker restart.",
        False,
    ),
    (
        "langfuse_public_key",
        "",
        "observability",
        "Langfuse project public key (pk-lf-...). Pair with "
        "langfuse_secret_key to authenticate prompt-management + "
        "tracing API calls.",
        False,
    ),
    (
        "langfuse_secret_key",
        "",
        "observability",
        "Langfuse project secret key (sk-lf-...). Encrypted at rest "
        "via the app_settings auto-encrypt trigger.",
        True,
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value, category, description, is_secret in _SEEDS:
            await conn.execute(
                """
                INSERT INTO app_settings
                  (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, $5, true)
                ON CONFLICT (key) DO UPDATE
                  SET description = EXCLUDED.description,
                      category = EXCLUDED.category,
                      is_secret = EXCLUDED.is_secret,
                      updated_at = NOW()
                """,
                key, value, category, description, is_secret,
            )
            logger.info(
                "Migration 0153: seeded %s (placeholder, secret=%s)",
                key, is_secret,
            )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for key, _, _, _, _ in _SEEDS:
            await conn.execute("DELETE FROM app_settings WHERE key = $1", key)
        logger.info("Migration 0153 down: removed langfuse_* placeholders")
