"""Migration 20260825_021743: retire the ``premium_*`` license settings

Six ``app_settings`` keys were written by ``poindexter premium`` — the
Lemon Squeezy license-key activation CLI retired in this same commit —
and read by nothing.

``premium_active``'s seed description claimed UnifiedPromptManager loads
``prompt_templates`` rows where ``source='premium'`` when it is ``true``.
That gating died when poindexter#47 Phase 2 retired the
``prompt_templates`` table (``prompt_manager.load_from_db`` documents the
removal); the claim survived only in the seed text and a stale ``main.py``
comment. The other five keys (``premium_license_key`` /
``premium_instance_id`` / ``premium_email`` / ``premium_customer_name`` /
``premium_validated_at``) were license-state bookkeeping for the same dead
flow — no reader ever existed outside ``premium.py`` itself (full-repo
grep 2026-08-24).

Pro delivery replaced license keys outright (glad-labs-stack#3216,
2026-08-15): Lemon Squeezy subscriptions in, GitHub collaborator invite to
the private ``poindexter-pro`` repo out, operated via ``poindexter pro``.
There is nothing to activate machine-side anymore.

Only ``premium_active`` was seeded (``0000_baseline.seeds.sql``); that
seed row is removed in the same commit, so a fresh install never creates
it and this migration is the already-installed half of the fix. The other
five keys exist only on installs whose operator once ran
``poindexter premium activate``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Keys whose only writer (poindexter/cli/premium.py) is deleted in this
# commit and which no code ever read. Listed explicitly (no LIKE pattern)
# so this can never widen to a key that is still live — e.g. the
# ``plugin.llm_provider.primary.premium`` cost-tier key must survive.
ORPHANED_KEYS = (
    "premium_license_key",
    "premium_instance_id",
    "premium_active",
    "premium_email",
    "premium_customer_name",
    "premium_validated_at",
)


async def up(pool) -> None:
    """Delete the orphaned license rows."""
    async with pool.acquire() as conn:
        deleted = await conn.fetch(
            "DELETE FROM app_settings WHERE key = ANY($1::text[]) RETURNING key",
            list(ORPHANED_KEYS),
        )
    logger.info(
        "Migration retire_premium_license_settings_orphaned_by_the_pro_delivery_cutover: "
        "applied (%d/%d orphaned key(s) deleted: %s)",
        len(deleted),
        len(ORPHANED_KEYS),
        ", ".join(sorted(r["key"] for r in deleted)) or "none present",
    )


async def down(pool) -> None:
    """Re-create the one row the baseline used to seed, with its original text.

    Structure-only restore, which is the whole truth here: no code reads
    ``premium_active`` on either side of the migration, so the value is
    inert. The other five keys were never seeded — they only ever existed
    as ``poindexter premium activate`` output, and that command is gone,
    so there is nothing to restore for them. License state itself is not
    recoverable — acceptable precisely because nothing consumed it.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
            VALUES
              ('premium_active', 'false', 'experiments',
               'When ''true'', UnifiedPromptManager loads prompt_templates rows where '
               'source=''premium'' on top of source=''default''. When ''false'' (OSS '
               'default), only ''default'' rows load. Set to ''true'' after applying a '
               'Glad Labs Premium Prompts pack (Pro tier, delivered via Lemon Squeezy).',
               false, true)
            ON CONFLICT (key) DO NOTHING
            """
        )
    logger.info(
        "Migration retire_premium_license_settings_orphaned_by_the_pro_delivery_cutover: "
        "reverted (premium_active re-seeded; license-state keys are gone for good)",
    )
