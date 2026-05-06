"""Migration 20260506_051355: voice_messages embedding column + HNSW index

ISSUE: Glad-Labs/poindexter#390

Slice 3 of the Discord voice-agent rollout. Slice 1 (#384) shipped the
hands-free voice bot; Slice 2 (#391) added the ``voice_messages`` table
for linear last-N-turn memory; this slice adds **semantic recall** on
top of that table — the bot embeds each turn with nomic-embed-text:768
(same model used everywhere else in ``embeddings``) and runs a pgvector
cosine query before each LLM call to surface prior turns the user is
referring back to.

What this migration does:

- Adds ``embedding vector(768)`` to ``voice_messages`` (nullable —
  embed-on-save is best-effort and rows without an embedding are still
  valid linear-history rows).
- Adds ``discord_channel_id TEXT`` so recall can scope to the current
  channel (multi-channel voice bots blend transcripts otherwise — see
  acceptance criteria 3 + 4 on the issue).
- Creates an HNSW index on ``embedding`` with ``vector_cosine_ops`` —
  matches the shape of ``idx_embeddings_hnsw`` on the main embeddings
  table so query plans stay consistent. ``WITH (m = 16, ef_construction
  = 64)`` is pgvector's recommended default for ~1k-1M vectors.
- Seeds two new app_settings keys (``voice_agent_recall_k`` = 3,
  ``voice_agent_recall_min_similarity`` = 0.5) so operators can tune
  recall aggressiveness without shipping code. Defaults are also
  registered in ``services/settings_defaults.py`` for fresh installs.

All ALTERs use ``IF NOT EXISTS``; the index uses ``CREATE INDEX IF
NOT EXISTS``; the app_settings inserts use ``ON CONFLICT DO NOTHING``.
The whole migration is idempotent.

Note on table existence: ``voice_messages`` is currently created lazily
by ``scripts/discord-voice-bot.py`` (CREATE TABLE IF NOT EXISTS at
boot) rather than by an earlier migration. This migration is defensive
about that — if the table doesn't exist yet, the body becomes a no-op
and the bot's own bootstrap will create the table with the embedding
column already in its CREATE statement (matched up by Slice 3 PR).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SETTING_SEEDS: list[tuple[str, str, str]] = [
    (
        "voice_agent_recall_k",
        "3",
        "Top-K most-similar prior voice_messages turns to inject into "
        "the qwen3:8b system prompt as 'recalled context' on each user "
        "turn. Separate from the linear last-N memory already shown in "
        "the prompt — recall surfaces older turns the user is "
        "referring back to. Default 3 keeps the prompt token count "
        "modest (voice replies stay snappy). Slice 3 of the voice-"
        "agent rollout — see Glad-Labs/poindexter#390.",
    ),
    (
        "voice_agent_recall_min_similarity",
        "0.5",
        "Cosine-similarity floor for voice_messages recall. Hits below "
        "this threshold are filtered out before the top-K cut, so a "
        "thin/empty conversation history doesn't drag in unrelated "
        "noise. 0.5 is the same default used by the search-memory "
        "skill against the main embeddings table. Slice 3 of the "
        "voice-agent rollout — see Glad-Labs/poindexter#390.",
    ),
]


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        # Defensive table-existence check — the table may not exist yet on
        # a fresh install (the bot creates it lazily on first /join). If
        # missing, the column/index ALTERs are no-ops; the bot's own
        # CREATE TABLE statement (updated in this PR) will include the
        # embedding column from the start.
        table_exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'voice_messages')"
        )
        if table_exists:
            await conn.execute(
                """
                ALTER TABLE voice_messages
                    ADD COLUMN IF NOT EXISTS embedding vector(768)
                """
            )
            await conn.execute(
                """
                ALTER TABLE voice_messages
                    ADD COLUMN IF NOT EXISTS discord_channel_id TEXT
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_voice_messages_embedding_hnsw
                    ON voice_messages
                    USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_voice_messages_user_channel
                    ON voice_messages (discord_user_id, discord_channel_id, created_at DESC)
                """
            )
            logger.info(
                "20260506_051355: added voice_messages.embedding (vector(768)) "
                "+ discord_channel_id + HNSW index"
            )
        else:
            logger.info(
                "20260506_051355: voice_messages table not present yet "
                "(will be created with embedding column by discord-voice-bot "
                "on first /join) — skipping ALTER, only seeding settings"
            )

        inserted = 0
        for key, value, description in _SETTING_SEEDS:
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value, description, is_active)
                VALUES ($1, $2, $3, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "20260506_051355: seeded %d/%d voice-recall settings "
            "(remaining were already set)",
            inserted, len(_SETTING_SEEDS),
        )


async def down(pool) -> None:
    """Revert the migration.

    Drops the embedding column + HNSW index + the two seeded settings.
    Leaves ``discord_channel_id`` in place because rows may have been
    populated with channel data the operator still wants for analytics —
    Convention A says drop only what's strictly needed to roll back the
    semantic-recall capability.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "DROP INDEX IF EXISTS idx_voice_messages_embedding_hnsw"
        )
        await conn.execute(
            "DROP INDEX IF EXISTS idx_voice_messages_user_channel"
        )
        await conn.execute(
            "ALTER TABLE IF EXISTS voice_messages DROP COLUMN IF EXISTS embedding"
        )
        for key, _value, _description in _SETTING_SEEDS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                key,
            )
        logger.info(
            "20260506_051355: reverted voice_messages embedding column + "
            "HNSW index + recall settings"
        )
