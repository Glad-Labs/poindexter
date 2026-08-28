"""Migration 20260828_005606: drop the orphaned embedding_collapse_summary_model setting

``embedding_collapse_summary_model`` outlived the code that read it.

The standalone embeddings-collapse job folded into the ``embeddings_collapse``
retention handler on 2026-06-24. That handler reads its model from
``retention_policies.config->>'summary_model'`` (see
``handlers/retention_embeddings_collapse.py``), which is the deliberate seam —
per-policy, so two policies can collapse at different model sizes. The
app_setting was retired at the fold and no reader has existed since.

It survives only on installs provisioned before that date. Nothing re-creates
it: unlike the ``gate_auto_expire_*`` fossils dropped by
``20260809_020020``, this key is in **none** of the three seed sources
(``settings_defaults.DEFAULTS``, ``0000_baseline.seeds.sql``,
``brain/seed_app_settings.json``), so there is no seed half to remove and a
fresh install has never had it. This migration is the whole fix.

Why bother with a dead row: it reads as live config and is wrong twice over.
Its value on the Glad Labs operator install was ``ollama/llama3.2:3b`` — a
model the collapse path has not consulted since June — so it showed up in a
2026-08-27 licence sweep for non-permissive model defaults as a false positive,
costing the time to prove it inert. An operator retuning it would believe they
had changed collapse behaviour; they would have changed nothing.

``ProbeZeroReaderSettingsJob`` could never have surfaced it either. The probe
reports never-read keys past a 30-day grace, but ``last_read_at`` is only
stamped by ``SiteConfig.get`` / ``SettingsService.get``; and in any case the
report is capped at ``settings_zero_reader_max_report`` against a large
never-read backlog. Found by hand — see the PR.

NOTE for anyone auditing the sibling: ``memory_compression_summary_model`` is
NOT the symmetric twin its name suggests. It is LIVE — baseline-seeded, read
from app_settings by ``retention_summarize_to_table``, which fails loud when it
is empty. It also carries a NULL ``last_read_at``, because it is read via raw
SQL that the read telemetry never stamps. Do not extend this migration to it.

ISSUE: Glad-Labs/poindexter#1027 (adjacent — the model-default licence sweep
that surfaced this row)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Listed explicitly rather than by prefix/LIKE so this can never widen to a key
# that is still live — in particular NOT memory_compression_summary_model, which
# shares the naming convention and is load-bearing (see the docstring).
ORPHANED_KEYS = ("embedding_collapse_summary_model",)


async def up(pool) -> None:
    """Delete the orphaned row. No-op on installs that never had it."""
    async with pool.acquire() as conn:
        deleted = await conn.fetch(
            "DELETE FROM app_settings WHERE key = ANY($1::text[]) RETURNING key",
            list(ORPHANED_KEYS),
        )
    logger.info(
        "Migration drop_the_orphaned_embedding_collapse_summary_model_setting: "
        "applied (%d/%d orphaned key(s) deleted: %s)",
        len(deleted),
        len(ORPHANED_KEYS),
        ", ".join(sorted(r["key"] for r in deleted)) or "none present",
    )


async def down(pool) -> None:
    """Re-create the row with the value it held at retirement.

    Structure-only restore, which is the whole truth here: no code reads this
    key on either side of the migration, so the value is inert. An
    operator-tuned value is NOT recoverable — acceptable precisely because
    tuning it never did anything. To actually change collapse behaviour, set
    ``summary_model`` on the retention policy:
    ``poindexter retention config set <name> summary_model=<model>``.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
            VALUES
              ('embedding_collapse_summary_model', 'ollama/phi4:14b', 'content',
               'RETIRED 2026-06-24 — no reader. The embeddings_collapse retention '
               'handler reads summary_model from retention_policies.config instead. '
               'Restored by a migration rollback; safe to delete.',
               false, true)
            ON CONFLICT (key) DO NOTHING
            """
        )
    logger.info(
        "Migration drop_the_orphaned_embedding_collapse_summary_model_setting: "
        "reverted (%d orphaned key(s) restored)",
        len(ORPHANED_KEYS),
    )
