"""Voice-bot memory helpers — embed-on-save + semantic recall.

Slice 3 of the Discord voice-agent rollout (Glad-Labs/poindexter#390).

Lives in ``scripts/`` rather than ``src/cofounder_agent/services/`` for
the same reason ``_oauth_helper.py`` does: the discord-voice-bot script
runs in a Docker image that doesn't always have ``services/`` on
``PYTHONPATH``, and we want the unit tests to be able to import these
helpers without triggering the bot's heavy module-level init (Whisper,
Kokoro, asyncpg pool bootstrap).

What lives here
---------------
* ``embed_text(...)`` — single-text Ollama call, returns a 768-d vector
  or ``None`` on failure (best-effort: never raise).
* ``vector_to_pg_text(...)`` — pgvector-friendly text format, matches
  the convention enshrined in PR #267 (asyncpg's vector adapter is
  flaky across versions; passing ``[a,b,c]`` text + ``::vector`` cast
  is the portable path).
* ``save_message_with_embedding(...)`` — insert into voice_messages,
  then opportunistically embed and UPDATE the row. Failure of the
  embed step is logged + swallowed so the conversation stays alive.
* ``recall_similar_turns(...)`` — pgvector cosine search filtered to
  the current ``discord_user_id`` / ``discord_channel_id`` (so
  multi-channel bots don't blend transcripts). Returns top-K rows
  oldest-first so the LLM sees them in conversational order.
* ``format_recalled_context(...)`` — render the recall result as a
  plain-text block ready to splice into the qwen3:8b system prompt.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Embedding (Ollama)
# ---------------------------------------------------------------------------

DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_EMBED_TIMEOUT_S = 10.0


async def embed_text(
    text: str,
    *,
    ollama_url: str,
    model: str = DEFAULT_EMBED_MODEL,
    timeout_s: float = DEFAULT_EMBED_TIMEOUT_S,
    client: httpx.AsyncClient | None = None,
) -> list[float] | None:
    """Embed ``text`` via the Ollama ``/api/embed`` route.

    Returns the 768-d vector on success, ``None`` on any failure.
    Best-effort by design — embedding is a memory enhancement, not a
    correctness requirement, so the voice bot must keep going even if
    the embedder is offline.

    Args:
        text: Text to embed. Empty/whitespace-only inputs short-circuit
            to ``None`` (no point hitting the embedder).
        ollama_url: Base URL like ``http://host.docker.internal:11434``.
            Trailing ``/`` is tolerated.
        model: Embedding model name. Default ``nomic-embed-text`` to
            match the rest of the codebase (768-d).
        timeout_s: Per-call timeout. Voice loops are latency-sensitive;
            10s is the upper bound before we give up.
        client: Optional ``httpx.AsyncClient`` for tests / connection
            reuse. If ``None``, a one-shot client is created.
    """
    if not text or not text.strip():
        return None

    base = ollama_url.rstrip("/")
    payload = {"model": model, "input": text}

    try:
        if client is not None:
            resp = await client.post(
                f"{base}/api/embed", json=payload, timeout=timeout_s,
            )
        else:
            async with httpx.AsyncClient(timeout=timeout_s) as c:
                resp = await c.post(f"{base}/api/embed", json=payload)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("voice embed failed (non-fatal): %s", exc)
        return None

    embeddings = data.get("embeddings") or []
    if not embeddings or not isinstance(embeddings[0], list):
        logger.warning("voice embed returned no vectors: %r", data)
        return None
    return [float(x) for x in embeddings[0]]


def vector_to_pg_text(vec: list[float]) -> str:
    """Format a Python list as a pgvector text literal.

    pgvector accepts ``[a,b,c]`` strings cast via ``::vector``. This is
    the same trick PR #267 used to fix the qvec writer — asyncpg's
    auto-encoder for pgvector is version-dependent and unreliable, so
    we always pass strings.
    """
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


# ---------------------------------------------------------------------------
# Save with embedding
# ---------------------------------------------------------------------------


async def save_message_with_embedding(
    conn: Any,
    *,
    role: str,
    content: str,
    discord_user_id: str | None = None,
    discord_channel_id: str | None = None,
    ollama_url: str,
    embed_model: str = DEFAULT_EMBED_MODEL,
    httpx_client: httpx.AsyncClient | None = None,
) -> int | None:
    """Insert a turn into ``voice_messages`` and best-effort embed it.

    Returns the new row id (or ``None`` if even the INSERT failed).
    The embedding step is wrapped in try/except — a failure logs
    WARNING and leaves the row with a NULL embedding, which recall
    queries silently skip.

    Args:
        conn: An open asyncpg connection (caller manages lifecycle so
            this fits both pool-acquire and per-call-connect callers).
        role: ``"user"`` or ``"assistant"``.
        content: The message text.
        discord_user_id: Author's Discord user id (string for snowflake).
        discord_channel_id: Discord text/voice channel id — used by
            recall to scope to the current conversation.
        ollama_url: Base URL for the embedder.
        embed_model: Embedding model name.
        httpx_client: Optional pre-built client (tests / reuse).
    """
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO voice_messages
                (discord_user_id, discord_channel_id, role, content)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            discord_user_id, discord_channel_id, role, content,
        )
    except Exception as exc:  # noqa: BLE001 — memory is best-effort
        logger.warning("voice _save_message INSERT failed (non-fatal): %s", exc)
        return None

    row_id = int(row["id"])

    # Embed step is intentionally separated so the row is durable even
    # if the embedder is down. Recall queries WHERE embedding IS NOT
    # NULL so a missing vector just means "this row is invisible to
    # semantic search" — linear last-N still works.
    try:
        vec = await embed_text(
            content,
            ollama_url=ollama_url,
            model=embed_model,
            client=httpx_client,
        )
        if vec is not None:
            await conn.execute(
                "UPDATE voice_messages SET embedding = $1::vector WHERE id = $2",
                vector_to_pg_text(vec),
                row_id,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "voice embed-on-save UPDATE failed for row %s (non-fatal): %s",
            row_id, exc,
        )

    return row_id


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------


async def recall_similar_turns(
    conn: Any,
    *,
    query_text: str,
    ollama_url: str,
    discord_user_id: str | None = None,
    discord_channel_id: str | None = None,
    k: int = 3,
    min_similarity: float = 0.5,
    exclude_ids: list[int] | None = None,
    embed_model: str = DEFAULT_EMBED_MODEL,
    httpx_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Return up to ``k`` voice_messages similar to ``query_text``.

    Filters by ``(discord_user_id, discord_channel_id)`` so the recall
    is scoped to the current conversation. Skips rows with NULL
    embedding (failed embed-on-save) and rows in ``exclude_ids`` (the
    current exchange's own user/assistant turns — caller passes those
    in so the bot doesn't "recall" the thing it just said).

    Returns rows ordered **oldest → newest within the top-K** so the
    LLM sees them in conversational order, not similarity-rank order.
    Each row is a dict with keys ``id``, ``role``, ``content``,
    ``similarity``, ``created_at``.

    Empty result is the success path for a thin/empty conversation.
    """
    if k <= 0:
        return []
    if not query_text or not query_text.strip():
        return []

    qvec = await embed_text(
        query_text,
        ollama_url=ollama_url,
        model=embed_model,
        client=httpx_client,
    )
    if qvec is None:
        return []

    qvec_text = vector_to_pg_text(qvec)
    exclude_ids = exclude_ids or []

    # Build the filter clauses dynamically — asyncpg can't bind a
    # variadic exclude list elegantly, but ``= ANY($N::bigint[])`` does
    # the trick. Same for the optional user/channel scopes.
    where_parts: list[str] = ["embedding IS NOT NULL"]
    params: list[Any] = [qvec_text, float(min_similarity)]
    if discord_user_id is not None:
        params.append(discord_user_id)
        where_parts.append(f"discord_user_id = ${len(params)}")
    if discord_channel_id is not None:
        params.append(discord_channel_id)
        where_parts.append(f"discord_channel_id = ${len(params)}")
    if exclude_ids:
        params.append([int(x) for x in exclude_ids])
        where_parts.append(f"id <> ALL(${len(params)}::bigint[])")

    params.append(int(k))
    limit_idx = len(params)

    sql = f"""
        SELECT id, role, content, created_at,
               1 - (embedding <=> $1::vector) AS similarity
        FROM voice_messages
        WHERE {' AND '.join(where_parts)}
          AND 1 - (embedding <=> $1::vector) >= $2
        ORDER BY embedding <=> $1::vector
        LIMIT ${limit_idx}
    """

    try:
        rows = await conn.fetch(sql, *params)
    except Exception as exc:  # noqa: BLE001 — recall is best-effort
        logger.warning("voice recall query failed (non-fatal): %s", exc)
        return []

    results = [
        {
            "id": int(r["id"]),
            "role": r["role"],
            "content": r["content"],
            "similarity": float(r["similarity"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    # Resort oldest → newest within the top-K hits so the LLM gets a
    # conversational ordering, not a relevance ordering.
    results.sort(key=lambda r: r["created_at"])
    return results


def format_recalled_context(hits: list[dict[str, Any]]) -> str:
    """Render recall hits as a plain-text block for the system prompt.

    Empty input → empty string (caller skips injection). Each hit
    becomes ``[role] content`` on its own line, prefixed with a short
    header so the LLM understands these are older turns the user might
    be referring to (not the live conversation).
    """
    if not hits:
        return ""
    lines = [
        "Recalled context — older turns from this conversation that may",
        "be relevant to what the user just said. Use only if directly",
        "applicable; otherwise ignore.",
        "",
    ]
    for h in hits:
        role = (h.get("role") or "").strip() or "?"
        content = (h.get("content") or "").strip().replace("\n", " ")
        if len(content) > 240:
            content = content[:237] + "..."
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)
