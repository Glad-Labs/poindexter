"""Migration 0127: seed embedding-collapse summarization settings.

Pairs with the LLM-summary path added to
``services/jobs/collapse_old_embeddings.py``. Until 2026-05-01 the
``CollapseOldEmbeddingsJob`` summarized clusters by joining the first
~200 chars of each member's ``text_preview`` — fast and zero-LLM-cost,
but lossy enough that vector queries against the summary lost a lot
of original semantics.

Now the job has two summary providers: ``joined_preview`` (the
original heuristic) and ``ollama`` (a real LLM-generated summary
written by the local model). This migration seeds the three knobs
that govern the new path:

* ``embedding_collapse_summary_provider`` (default ``"ollama"``) —
  switch to ``"joined_preview"`` to disable the LLM call without
  disabling the whole collapse job.
* ``embedding_collapse_summary_model`` (default
  ``"gemma3:27b-it-qat"``) — chosen empirically: produced a 501-char
  factually-dense summary in ~12s on a smoke test of 5 audit-log
  excerpts, vs glm-4.7-5090 (a thinking model) which returned only
  the post-thinking salvage and burned compute on its reasoning trace.
* ``embedding_collapse_summary_timeout_seconds`` (default ``60``) —
  bumps the per-call timeout above the OllamaClient default so the
  bigger model has headroom; the 5-excerpt test ran in 12s, this
  gives 5x slack.

Idempotent: ``ON CONFLICT (key) DO NOTHING``.

Goal context (Matt 2026-05-01): the system should "know where it
started" — sharper memories of recent work, vague but accurate
memories of older work. Joined-preview captured surface tokens but
lost relationships, names, decisions. LLM summaries preserve the
relationships at the cost of one inference call per cluster.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_SETTINGS = [
    (
        "embedding_collapse_summary_provider",
        "ollama",
        "memory_compression",
        "Summarization backend for CollapseOldEmbeddingsJob. "
        "'ollama' calls the local LLM to produce a real summary; "
        "'joined_preview' falls back to the legacy joined-text heuristic "
        "(used when ollama is unreachable, also a no-LLM-cost mode). "
        "Defaults to 'ollama' as of migration 0127.",
    ),
    (
        "embedding_collapse_summary_model",
        "gemma3:27b-it-qat",
        "memory_compression",
        "Ollama model used for cluster-summary generation. Picked "
        "empirically: factually dense, no thinking-trace overhead, "
        "~12s/5-excerpt cluster on the RTX 5090. Override via "
        "app_settings if a smaller/larger model fits the operator's "
        "GPU bandwidth better.",
    ),
    (
        "embedding_collapse_summary_timeout_seconds",
        "60",
        "memory_compression",
        "Per-call timeout for the LLM summary generation. 60s allows "
        "headroom over the ~12s typical run; on timeout the cluster "
        "falls back to joined-preview rather than failing the whole "
        "collapse pass.",
    ),
]


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Table 'app_settings' missing — skipping migration 0127"
            )
            return

        seeded = 0
        for key, value, category, description in _SETTINGS:
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret)
                VALUES ($1, $2, $3, $4, FALSE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
            if result == "INSERT 0 1":
                seeded += 1
        logger.info(
            "Migration 0127: seeded %d new collapse-summary setting(s) "
            "(existing operator values left untouched)",
            seeded,
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "app_settings"):
            return
        for key, _value, _category, _description in _SETTINGS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1", key,
            )
        logger.info(
            "Migration 0127 rolled back: removed %d collapse-summary settings",
            len(_SETTINGS),
        )
