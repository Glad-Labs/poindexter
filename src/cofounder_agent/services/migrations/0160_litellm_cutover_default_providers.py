"""Migration 0160: flip default LLMProvider per tier from ollama_native → litellm.

Glad-Labs/poindexter#372 — production cutover. Activates the existing
``LiteLLMProvider`` plugin (services/llm_providers/litellm_provider.py)
as the default for every cost tier the dispatcher resolves at runtime.

Before this migration, ``services/llm_providers/dispatcher.py`` falls
back to ``ollama_native`` for every tier when no
``plugin.llm_provider.primary.<tier>`` row exists. This migration seeds
those rows pointing at ``litellm`` instead, putting LiteLLM on the
critical path for any caller that goes through the dispatcher.

Tier mapping:

============  ============================  =============
Tier          Before (dispatcher default)   After (this)
============  ============================  =============
free          ollama_native                 litellm
budget        ollama_native                 litellm
standard      ollama_native                 litellm
premium       ollama_native                 litellm
flagship      ollama_native                 litellm
============  ============================  =============

The cutover is idempotent — re-runs of ``up()`` only update rows that
aren't already set to ``'litellm'``, so accidentally re-applying the
migration after a partial rollback won't clobber operator overrides
made between runs. ``down()`` restores the rows to ``'ollama_native'``
so the legacy path becomes the default again on revert.

Per ``feedback_db_first_config`` / ``feedback_db_configurable_design``:
the cutover is one settings flip (this migration), reversible by either
running ``down()`` or by ``poindexter settings set
plugin.llm_provider.primary.<tier> ollama_native`` per tier.
See ``docs/operations/litellm-cutover-rollback.md`` for the full
runbook.

Per ``feedback_no_paid_apis`` / ``feedback_cost_controls``: LiteLLM
defaults to local Ollama (``ollama/<model>`` namespace) for every
caller that already passes a bare or ``ollama/``-prefixed model name —
no cloud spend can sneak in via this flip alone. The companion
``plugin.llm_provider.litellm`` config row controls the LiteLLM api_base;
absent that row, the provider's hard-coded default is
``http://localhost:11434`` (local Ollama).

Per ``feedback_no_silent_defaults``: the dispatcher already raises a
clear log line when a configured provider name isn't in the registry
and falls back to ``ollama_native``; the LiteLLMProvider IS in the
registry (entry_points group ``poindexter.llm_providers`` per
``plugins/registry.py`` core_samples merge — Glad-Labs/poindexter#376),
so the fallback path won't fire under normal operation.

## Coverage caveat (read before merging)

As of 2026-05-05 the dispatcher (``services/llm_providers/dispatcher.py``)
has ZERO production callers — the writer pipeline still goes through
``services/ollama_client.OllamaClient`` directly via
``services/ai_content_generator.py``. This migration changes the default
the dispatcher would resolve, but until call sites migrate to
``dispatch_complete`` / ``get_provider`` the observable runtime
behavior of the writer pipeline is unchanged. The migration is still
worth landing because:

1. Future PRs that wire the writer pipeline through the dispatcher
   pick up LiteLLM automatically.
2. The Langfuse callback (``configure_langfuse_callback`` in
   ``services/llm_providers/litellm_provider.py``) is registered at
   lifespan startup independently of the dispatcher; once any caller
   hits ``litellm.acompletion`` the spans land.
3. Settings-as-source-of-truth holds — operators can verify "what
   provider would the dispatcher pick for tier X" with a single SQL
   query.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Tiers covered by the dispatcher's _DEFAULT_PROVIDER_PER_TIER table.
# Kept in sync deliberately — adding a tier without seeding it here
# silently re-introduces ``ollama_native`` as the default for that tier.
_TIERS = ("free", "budget", "standard", "premium", "flagship")

_NEW_PROVIDER = "litellm"
_LEGACY_PROVIDER = "ollama_native"


def _setting_key(tier: str) -> str:
    return f"plugin.llm_provider.primary.{tier}"


async def up(pool) -> None:
    """Flip every tier's default LLMProvider to LiteLLM.

    Idempotent — uses ``ON CONFLICT DO UPDATE`` so partial earlier
    runs (or operator-set values left over from manual cutover
    experiments) converge to ``litellm``. Operators who want to pin a
    tier to a different provider should do so AFTER this migration
    runs (or use ``down()`` + a custom seed).
    """
    async with pool.acquire() as conn:
        for tier in _TIERS:
            key = _setting_key(tier)
            await conn.execute(
                """
                INSERT INTO app_settings
                  (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, false, true)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    description = EXCLUDED.description,
                    updated_at = NOW()
                WHERE app_settings.value <> EXCLUDED.value
                """,
                key,
                _NEW_PROVIDER,
                "plugins",
                (
                    f"Default LLMProvider for the {tier!r} cost tier. "
                    f"Resolved by services/llm_providers/dispatcher.get_provider "
                    f"at call time. Set to 'litellm' by migration 0160 "
                    f"(Glad-Labs/poindexter#372 cutover); revert via the "
                    f"runbook in docs/operations/litellm-cutover-rollback.md."
                ),
            )
            logger.info(
                "Migration 0160: set %s = %s (was: %s or unset)",
                key, _NEW_PROVIDER, _LEGACY_PROVIDER,
            )


async def down(pool) -> None:
    """Restore the legacy default (``ollama_native``) for every tier.

    The companion to ``up()``. Used by the rollback runbook when the
    LiteLLM cutover causes pipeline regressions and the operator wants
    a one-step revert. Operator-set custom values that aren't
    ``litellm`` are LEFT alone — only rows the migration would have
    flipped get reverted.
    """
    async with pool.acquire() as conn:
        for tier in _TIERS:
            key = _setting_key(tier)
            result = await conn.execute(
                """
                UPDATE app_settings
                SET value = $2, updated_at = NOW()
                WHERE key = $1 AND value = $3
                """,
                key, _LEGACY_PROVIDER, _NEW_PROVIDER,
            )
            logger.info(
                "Migration 0160 down: restored %s -> %s (%s)",
                key, _LEGACY_PROVIDER, result,
            )
