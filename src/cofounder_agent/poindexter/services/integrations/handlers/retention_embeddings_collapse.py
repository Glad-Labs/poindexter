"""Handler: ``retention.embeddings_collapse``.

Compresses old embeddings by clustering semantically similar rows and
replacing each cluster with a single centroid + summary row. Reduces
pgvector index size while preserving retrieval quality for future semantic
search.

Config keys (from the ``config`` JSONB column on the retention_policies row):
- ``source_table`` (str, required) — embeddings.source_table discriminator.
- ``age_days`` (int, default 90) — candidate rows older than this are
  eligible.
- ``cluster_size`` (int, default 8) — target rows per cluster. Cluster
  *count* scales with the candidate pool (``k ~= candidates / cluster_size``,
  floored at 2), so a high-volume source doesn't collapse down to a
  handful of giant, mostly-lossy clusters. See ``_choose_cluster_count``.
- ``batch_size`` (int, default 500) — max candidate rows fetched per run
  (oldest-first). Bounds both memory and the number of LLM summarization
  calls a single run can trigger (~batch_size / cluster_size clusters);
  a source with a large backlog catches up incrementally across runs
  instead of clustering everything in one pass. Same pattern as
  ``embeddings_orphan_prune``'s ``batch_size``.
- ``summary_provider`` (str, default "ollama") — "ollama" calls the local
  budget-tier model; anything else uses the joined-preview fallback.
- ``summary_timeout_s`` (int, default 60) — per-cluster LLM timeout.
- ``prompt_template`` (str, optional) — per-policy-row summarizer prompt.
  When unset, the ``memory.collapse_old_embeddings.summary`` SKILL.md
  catalog key is resolved via UnifiedPromptManager (inline fallback when
  the manager is unreachable).

Returns: {
    "deleted":      int,   # raw rows removed
    "summarized":   int,   # summary rows written
    "source_table": str,
    "clusters":     int,   # clusters that produced a summary
    "batch_size":   int,   # candidate-fetch cap that was in effect
}

## Clustering

Pure-Python k-means on L2-normalized embedding vectors — cosine-equivalent,
no numpy/sklearn dependency. ``k = min(cluster_size, max(2, n // 2))`` so
small candidate sets always produce meaningful clusters. Deterministic via a
fixed seed so re-runs on identical data behave identically.

## Transaction safety

Each cluster's "write summary + delete originals" pair runs inside a single
``conn.transaction()`` — a failure anywhere rolls the whole cluster back,
leaving raw rows intact for the next run. Idempotent: ``is_summary = FALSE``
filters already-collapsed summaries out of the candidate query.

## LLM summarization

When ``summary_provider = "ollama"``, each cluster's preview texts are sent
to the configured ``summary_model`` for a 3-6 sentence dense factual summary.
Any LLM failure silently falls back to the joined-preview heuristic (no
crash, no abort). Prompt resolution order: per-policy-row
``config.prompt_template`` > ``memory.collapse_old_embeddings.summary``
catalog key > inline ``_DEFAULT_SUMMARY_PROMPT`` fallback.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Embedding vector helpers
# ---------------------------------------------------------------------------


def _parse_vector(raw: Any) -> list[float]:
    """Turn pgvector's string/list/tuple representation into list[float]."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [float(v) for v in raw]
    text = str(raw).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if not text:
        return []
    return [float(v) for v in text.split(",") if v.strip()]


def _l2_norm(v: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _normalize(v: Sequence[float]) -> list[float]:
    n = _l2_norm(v)
    if n == 0.0:
        return list(v)
    return [x / n for x in v]


def _mean(vectors: Sequence[Sequence[float]]) -> list[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    out = [0.0] * dim
    for v in vectors:
        for i in range(dim):
            out[i] += v[i]
    n = float(len(vectors))
    return [x / n for x in out]


def _sq_distance(a: Sequence[float], b: Sequence[float]) -> float:
    return sum((a[i] - b[i]) ** 2 for i in range(len(a)))


def _vector_literal(v: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


# ---------------------------------------------------------------------------
# K-means clustering
# ---------------------------------------------------------------------------


def _choose_cluster_count(n: int, cluster_size: int) -> int:
    """Cluster count for ``n`` candidates targeting ~``cluster_size`` rows each.

    Scales with volume: doubling ``n`` roughly doubles the cluster count,
    keeping average cluster size near ``cluster_size`` regardless of how
    large the candidate pool is. Floored at 2 (kmeans_cluster itself caps
    ``k`` back down to ``n`` for tiny pools). A prior version capped
    ``k`` at ``cluster_size`` directly — fine for small pools, but for a
    large pool it silently forced everything into ``cluster_size`` giant
    clusters instead of ``~n / cluster_size`` right-sized ones.
    """
    if n <= 0:
        return 0
    size = max(1, cluster_size)
    return max(2, n // size)


def kmeans_cluster(
    vectors: Sequence[Sequence[float]],
    k: int,
    *,
    max_iters: int = 20,
    seed: int = 1337,
) -> tuple[list[int], list[list[float]]]:
    """Lloyd's k-means on L2-normalized vectors (cosine-equivalent).

    Returns ``(assignments, centroids)``. Deterministic for a given seed.
    """
    if not vectors:
        return [], []

    n = len(vectors)
    if k <= 0:
        k = 1
    if k > n:
        k = n

    normed = [_normalize(v) for v in vectors]
    rng = random.Random(seed)

    seed_indices = rng.sample(range(n), k)
    centroids: list[list[float]] = [list(normed[i]) for i in seed_indices]

    assignments = [0] * n
    for _ in range(max_iters):
        changed = False
        for i, v in enumerate(normed):
            best_c = 0
            best_d = _sq_distance(v, centroids[0])
            for c_idx in range(1, k):
                d = _sq_distance(v, centroids[c_idx])
                if d < best_d:
                    best_d = d
                    best_c = c_idx
            if assignments[i] != best_c:
                assignments[i] = best_c
                changed = True

        new_centroids: list[list[float]] = []
        for c_idx in range(k):
            members = [normed[i] for i, a in enumerate(assignments) if a == c_idx]
            if members:
                new_centroids.append(_normalize(_mean(members)))
            else:
                new_centroids.append(centroids[c_idx])
        centroids = new_centroids

        if not changed:
            break

    return assignments, centroids


# ---------------------------------------------------------------------------
# Summary content + metadata
# ---------------------------------------------------------------------------


def build_summary_text(previews: Iterable[str], *, chars_per_member: int = 200) -> str:
    """Join the first ``chars_per_member`` chars of each preview.

    Lossy-but-fast fallback when LLM summarization is disabled or fails.
    """
    parts: list[str] = []
    for p in previews:
        if not p:
            continue
        snippet = p.strip().replace("\n", " ")
        if len(snippet) > chars_per_member:
            snippet = snippet[:chars_per_member].rstrip() + "..."
        parts.append(snippet)
    return " | ".join(parts)


_SUMMARY_PROMPT_KEY = "memory.collapse_old_embeddings.summary"

# Inline bootstrap fallback — must stay byte-identical to the
# ``## memory.collapse_old_embeddings.summary`` body in
# skills/ops/hygiene/SKILL.md (including the trailing newline the SKILL.md
# loader appends); the shared drift guard in
# tests/unit/services/test_prompt_fallback_drift.py enforces it.
_DEFAULT_SUMMARY_PROMPT = (
    "You are compressing a cluster of older memories so the system "
    "remembers the gist without storing every detail. Below are "
    "{n} excerpts from the same source ({source_table}), each "
    "separated by '---'.\n\n"
    "Write a single paragraph (3-6 sentences) summarizing what these "
    "excerpts collectively say. Preserve specific names, dates, "
    "decisions, errors, and outcomes. Drop boilerplate, repetition, "
    "and verbose phrasing. The summary will be embedded and used for "
    "future semantic search, so dense factual content beats prose.\n\n"
    "Excerpts:\n{joined}\n\n"
    "Summary:\n"
)


def _resolve_summary_prompt_template() -> str:
    """Pull the cluster-collapse summarizer template (no placeholders
    filled) via the manager's resolution seam — the caller
    (:func:`build_summary_text_via_llm`) fills ``{n}`` /
    ``{source_table}`` / ``{joined}`` itself.

    ``_resolve_template_with_meta`` honors the
    ``langfuse_prompt_overrides_enabled`` gate (poindexter#825; mirrors
    the #2103 fix in ``retention_summarize_to_table``). Inline fallback
    when the manager is unreachable, per
    ``feedback_prompts_must_be_db_configurable``.
    """
    try:
        from services.prompt_manager import get_prompt_manager

        pm = get_prompt_manager()
        return pm._resolve_template_with_meta(_SUMMARY_PROMPT_KEY)[0]
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[retention.embeddings_collapse] prompt_manager lookup for %r "
            "failed (%s) — using inline fallback",
            _SUMMARY_PROMPT_KEY, exc,
        )
        return _DEFAULT_SUMMARY_PROMPT


async def build_summary_text_via_llm(
    previews: Sequence[str],
    *,
    source_table: str,
    model: str,
    timeout_s: int,
    prompt_template: str | None = None,
    pool: Any = None,
) -> str | None:
    """Summarize a cluster via the LiteLLM dispatcher (poindexter#827 sweep).

    An explicit ``prompt_template`` (the per-policy-row config value)
    wins; when None, the template resolves from the SKILL.md catalog via
    :func:`_resolve_summary_prompt_template`.

    Routes through ``dispatch_complete`` so the collapse summarizer lands
    in ``cost_logs`` + Langfuse like every other LLM call — the direct
    OllamaClient path this replaces was one of the last dispatcher
    bypasses. Requires ``pool``; without one the LLM step is skipped
    (returns ``None``) and callers fall back to the deterministic
    :func:`build_summary_text`. Never raises.
    """
    if not previews:
        return None
    if pool is None:
        logger.warning(
            "[retention.embeddings_collapse] no pool — skipping LLM "
            "summarization, using joined-preview fallback",
        )
        return None

    pieces: list[str] = []
    for p in previews:
        if not p:
            continue
        s = p.strip().replace("\r\n", "\n")
        if len(s) > 800:
            s = s[:800].rstrip() + "..."
        pieces.append(s)

    if not pieces:
        return None

    prompt = (prompt_template or _resolve_summary_prompt_template()).format(
        n=len(pieces),
        source_table=source_table,
        joined="\n---\n".join(pieces),
    )

    try:
        from services.llm_providers.dispatcher import dispatch_complete

        completion = await dispatch_complete(
            pool=pool,
            messages=[{"role": "user", "content": prompt}],
            model=model,
            tier="budget",
            phase="retention_embeddings_collapse",
            temperature=0.3,
            max_tokens=400,
            timeout_s=float(timeout_s),
        )
        text = (getattr(completion, "text", "") or "").strip()
        if not text:
            return None
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()
        return text or None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[retention.embeddings_collapse] LLM summarization failed "
            "for source=%s (%d members): %s — falling back to joined-preview",
            source_table, len(pieces), exc,
        )
        return None


def build_summary_metadata(
    source_table: str,
    source_ids: Sequence[str],
    *,
    cluster_index: int,
    cluster_size: int,
    age_days: int,
) -> dict[str, Any]:
    return {
        "is_summary": True,
        "collapse_source": source_table,
        "collapsed_source_ids": list(source_ids),
        "collapsed_count": len(source_ids),
        "cluster_index": cluster_index,
        "cluster_size": cluster_size,
        "age_days_cutoff": age_days,
        "collapsed_at": datetime.now(timezone.utc).isoformat(),
    }


def _summary_source_id(source_table: str, source_ids: Sequence[str]) -> str:
    digest = hashlib.sha1(
        ("|".join(sorted(source_ids))).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:16]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"summary/{source_table}/{stamp}/{digest}"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _write_summary_and_delete(
    pool: Any,
    *,
    source_table: str,
    summary_id: str,
    summary_text: str,
    metadata: dict[str, Any],
    centroid: Sequence[float],
    embedding_model: str,
    raw_row_ids: Sequence[int],
) -> tuple[int, int]:
    """Write one summary row + delete N raw rows inside a transaction.

    Returns ``(summaries_written, deleted_count)``. On any failure the
    transaction rolls back and raw rows remain.
    """
    now = datetime.now(timezone.utc)
    content_hash = _content_hash(summary_text or summary_id)
    vector_str = _vector_literal(centroid)
    text_preview = (summary_text or summary_id)[:500]
    metadata_json = json.dumps(metadata)

    async with pool.acquire() as conn:
        async with conn.transaction():
            summary_row = await conn.fetchrow(
                """
                INSERT INTO embeddings (
                    source_table, source_id, content_hash, chunk_index,
                    text_preview, chunk_text, embedding_model, embedding,
                    metadata, writer, is_summary, created_at, updated_at
                )
                VALUES (
                    $1, $2, $3, 0,
                    $4, $9, $5, $6::vector, $7::jsonb,
                    'collapse_handler', TRUE, $8, $8
                )
                ON CONFLICT (source_table, source_id, chunk_index, embedding_model)
                DO UPDATE SET content_hash  = EXCLUDED.content_hash,
                              embedding     = EXCLUDED.embedding,
                              metadata      = EXCLUDED.metadata,
                              text_preview  = EXCLUDED.text_preview,
                              chunk_text    = EXCLUDED.chunk_text,
                              is_summary    = TRUE,
                              updated_at    = EXCLUDED.updated_at
                RETURNING id
                """,
                source_table,
                summary_id,
                content_hash,
                text_preview,
                embedding_model,
                vector_str,
                metadata_json,
                now,
                # Collapse summaries are retrieval targets like any other row,
                # so they carry the full summary text (poindexter#1033) rather
                # than only the 500-char display preview above.
                summary_text or summary_id,
            )
            if summary_row is None:
                raise RuntimeError(
                    "summary insert returned no row — refusing to delete originals"
                )

            verify = await conn.fetchval(
                "SELECT 1 FROM embeddings WHERE id = $1 AND is_summary = TRUE",
                summary_row["id"],
            )
            if not verify:
                raise RuntimeError("summary row verification failed — rolling back")

            deleted_count = 0
            for row_id in raw_row_ids:
                result = await conn.execute(
                    "DELETE FROM embeddings WHERE id = $1 AND is_summary = FALSE",
                    row_id,
                )
                try:
                    deleted_count += int(result.split()[-1])
                except (ValueError, IndexError):
                    pass  # silent-ok: parse failure; the partial-delete guard (deleted_count != len) warns below

            if deleted_count != len(raw_row_ids):
                logger.warning(
                    "[retention.embeddings_collapse] expected %d deletes, got %d "
                    "(source=%s) — committing partial delete",
                    len(raw_row_ids), deleted_count, source_table,
                )

    logger.info(
        "[retention.embeddings_collapse] source=%s wrote summary %s, "
        "deleted %d raw rows",
        source_table, summary_id, deleted_count,
    )
    return 1, deleted_count


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


@register_handler("retention", "embeddings_collapse")
async def embeddings_collapse(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Cluster old embeddings for ``source_table`` and replace with summaries."""
    if pool is None:
        raise RuntimeError("retention.embeddings_collapse: pool unavailable")

    config = row.get("config") or {}
    if not isinstance(config, dict):
        config = {}

    source_table = config.get("source_table")
    if not source_table:
        raise ValueError(
            "retention.embeddings_collapse: config.source_table is required"
        )

    age_days = int(config.get("age_days") or 90)
    cluster_size = int(config.get("cluster_size") or 8)
    batch_size = int(config.get("batch_size") or _DEFAULT_BATCH_SIZE)
    summary_provider = str(config.get("summary_provider") or "ollama").strip().lower()
    summary_timeout_s = int(config.get("summary_timeout_s") or 60)

    # Model is read from config JSONB; operators set it via
    # `poindexter retention config set <name> summary_model=<model>`.
    #
    # There is deliberately NO app_setting here. `embedding_collapse_summary_model`
    # was retired 2026-06-24 when the standalone collapse job folded into this
    # retention handler — it is absent from both `settings_defaults.DEFAULTS` and
    # `0000_baseline.seeds.sql`, so nothing re-seeds it, but installs provisioned
    # before that date still carry an orphan row that reads as live config and
    # is not. Do not "restore" it: the per-policy JSONB is the seam, so two
    # policies can collapse at different model sizes. The default below matches
    # the value that key used to hold, so behaviour was unchanged at the fold.
    #
    # Its sibling is NOT symmetric, despite the matching name:
    # `memory_compression_summary_model` IS live, baseline-seeded, and read from
    # app_settings by `retention_summarize_to_table` (which fails loud when it is
    # empty). Changing one path does not change the other.
    summary_model = str(
        config.get("summary_model") or "phi4:14b"
    ).strip().removeprefix("ollama/")

    # Per-policy-row prompt override; None → the SKILL.md catalog default
    # (memory.collapse_old_embeddings.summary) resolved downstream.
    summary_prompt_template = str(config.get("prompt_template") or "").strip() or None

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, age_days))

    # Fetch old raw rows. Oldest-first + LIMIT so a large backlog is worked
    # off incrementally across runs instead of loading (and clustering)
    # the entire eligible set in one pass.
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, source_id, text_preview, metadata,
                   embedding, embedding_model, writer, origin_path
            FROM embeddings
            WHERE source_table = $1
              AND created_at < $2
              AND is_summary = FALSE
            ORDER BY created_at ASC
            LIMIT $3
            """,
            source_table,
            cutoff,
            batch_size,
        )

    if len(rows) < 2:
        logger.debug(
            "[retention.embeddings_collapse] %s: only %d candidates — skip",
            row.get("name"), len(rows),
        )
        return {
            "deleted": 0, "summarized": 0, "source_table": source_table,
            "clusters": 0, "batch_size": batch_size,
        }

    # Parse vectors; skip rows with missing/malformed embeddings.
    parsed: list[dict[str, Any]] = []
    for r in rows:
        vec = _parse_vector(r["embedding"])
        if not vec:
            continue
        parsed.append({
            "id": r["id"],
            "source_id": r["source_id"],
            "text_preview": r["text_preview"] or "",
            "embedding": vec,
            "embedding_model": r["embedding_model"],
        })
    if len(parsed) < 2:
        return {
            "deleted": 0, "summarized": 0, "source_table": source_table,
            "clusters": 0, "batch_size": batch_size,
        }

    k = _choose_cluster_count(len(parsed), cluster_size)
    vectors = [p["embedding"] for p in parsed]
    assignments, centroids = kmeans_cluster(vectors, k)

    # Group by cluster assignment.
    clusters: dict[int, list[dict[str, Any]]] = {}
    for i, a in enumerate(assignments):
        clusters.setdefault(a, []).append(parsed[i])

    # Dominant embedding model across all rows.
    model_counts: dict[str, int] = {}
    for p in parsed:
        m = p.get("embedding_model") or ""
        model_counts[m] = model_counts.get(m, 0) + 1
    dominant_model = max(model_counts.items(), key=lambda kv: kv[1])[0] or "nomic-embed-text"

    total_deleted = total_summarized = total_clusters = 0

    for cluster_idx, members in clusters.items():
        if len(members) < 2:
            continue  # lone-member cluster is not a compression

        if not centroids or cluster_idx >= len(centroids):
            continue
        centroid = centroids[cluster_idx]
        if not centroid:
            continue

        member_ids = [m["source_id"] for m in members]
        row_ids = [m["id"] for m in members]
        summary_id = _summary_source_id(source_table, member_ids)

        previews = [m["text_preview"] for m in members]
        summary_text: str | None = None
        if summary_provider == "ollama" and summary_model:
            summary_text = await build_summary_text_via_llm(
                previews,
                source_table=source_table,
                model=summary_model,
                timeout_s=summary_timeout_s,
                prompt_template=summary_prompt_template,
                pool=pool,
            )
        if not summary_text:
            summary_text = build_summary_text(previews)

        metadata = build_summary_metadata(
            source_table,
            member_ids,
            cluster_index=int(cluster_idx),
            cluster_size=len(members),
            age_days=age_days,
        )

        try:
            summaries_written, deleted = await _write_summary_and_delete(
                pool,
                source_table=source_table,
                summary_id=summary_id,
                summary_text=summary_text,
                metadata=metadata,
                centroid=centroid,
                embedding_model=dominant_model,
                raw_row_ids=row_ids,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[retention.embeddings_collapse] cluster write failed "
                "source=%s cluster=%s: %s",
                source_table, cluster_idx, exc,
            )
            continue

        if summaries_written:
            total_summarized += summaries_written
            total_clusters += 1
        total_deleted += deleted

    logger.info(
        "[retention.embeddings_collapse] %s: summarized=%d deleted=%d clusters=%d "
        "(candidates=%d, batch_size=%d)",
        row.get("name"), total_summarized, total_deleted, total_clusters,
        len(parsed), batch_size,
    )
    return {
        "deleted": total_deleted,
        "summarized": total_summarized,
        "source_table": source_table,
        "clusters": total_clusters,
        "batch_size": batch_size,
    }
