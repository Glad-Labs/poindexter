"""CollapseOldEmbeddingsJob — cap pgvector growth via cluster+summary (GH-81).

The embeddings table grows unboundedly as the Claude Code session tap
(#80), brain knowledge writer, and audit tap keep emitting rows that
rarely get read after a certain age. This job compresses the tail of
that distribution: for each allow-listed ``source_table``, old raw
rows are grouped, clustered by cosine similarity, and replaced with a
single centroid+summary row per cluster inside a transaction.

Summaries are regular ``embeddings`` rows with ``is_summary = TRUE``
and metadata listing the collapsed source_ids. Search endpoints
(``MemoryClient.search``, ``find_similar_posts``) don't need to
change — they return summaries alongside raw rows. Callers that want
raw-only can filter on ``metadata->>'is_summary'`` at query time.

## Config (app_settings)

- ``embedding_collapse_enabled`` (bool, default ``false``)
- ``embedding_collapse_age_days`` (default ``90``)
- ``embedding_collapse_cluster_size`` (default ``8``)
- ``embedding_collapse_source_tables`` (comma list, default
  ``"claude_sessions,brain,audit"``)

## Safety

- ``embedding_collapse_enabled = false`` by default → dormant on
  existing deployments until opt-in.
- Source-table allow-list is enforced against a hard-coded set of
  NEVER-collapse tables (``posts``, ``issues``, ``memory``) so a
  typo in the allow-list can't drop irreplaceable pipeline state.
- Each cluster's "write summary + delete originals" pair runs inside
  a single ``conn.transaction()`` — a failure anywhere rolls the
  whole cluster back and leaves raw rows in place.
- ``is_summary = FALSE`` is a filter on both candidate selection and
  cluster membership, so re-runs are idempotent (already-collapsed
  summaries are invisible to the next pass).

## Clustering

Pure-Python k-means on the embedding vectors — no numpy/sklearn
dependency. Dimensions (768 floats) are a few KB per row; even with
hundreds of candidates the O(k * n * d) cost per iteration is well
under a second. The effective k is
``min(cluster_size, max(2, len(rows) // 2))`` so groups smaller than
``2 * cluster_size`` fall back to fewer clusters.

Summary content is the joined first ~200 chars of each cluster
member's ``text_preview``. The issue explicitly says LLM summary is
optional; joined-previews preserve enough signal for a vector query
to retrieve the cluster and operators can upgrade to an Ollama call
later without schema changes.
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

from plugins.job import JobResult

logger = logging.getLogger(__name__)


# Tables that must NEVER be collapsed regardless of operator config.
# Collapsing these would break live RAG callers that expect one row
# per authoritative artifact (posts, issues, long-lived memory).
_NEVER_COLLAPSE: frozenset[str] = frozenset({"posts", "issues", "memory"})


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

async def _get_setting(pool: Any, key: str, default: str) -> str:
    """Read a string value from ``app_settings`` with a fallback."""
    try:
        row = await pool.fetchrow(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
        if row and row["value"] is not None:
            return str(row["value"])
    except Exception as exc:  # noqa: BLE001 — never crash the job
        logger.debug("[COLLAPSE] setting read failed for %s: %s", key, exc)
    return default


def _parse_bool(raw: str) -> bool:
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(raw: str, default: int) -> int:
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return default


def _parse_source_list(raw: str) -> list[str]:
    items = [s.strip() for s in str(raw).split(",")]
    return [s for s in items if s and s not in _NEVER_COLLAPSE]


# ---------------------------------------------------------------------------
# Embedding parsing
# ---------------------------------------------------------------------------

def _parse_vector(raw: Any) -> list[float]:
    """Turn pgvector's string/list/tuple representation into a list[float]."""
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


# ---------------------------------------------------------------------------
# Pure-Python k-means (cosine via L2-normalized vectors)
# ---------------------------------------------------------------------------

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


def kmeans_cluster(
    vectors: Sequence[Sequence[float]],
    k: int,
    *,
    max_iters: int = 20,
    seed: int = 1337,
) -> tuple[list[int], list[list[float]]]:
    """Lloyd's k-means. Returns (assignments, centroids).

    Vectors are L2-normalized so Euclidean distance on normalized
    vectors is equivalent to cosine distance. Deterministic given a
    fixed ``seed`` — the job stamps one in so re-runs on identical
    data behave identically (useful for the idempotency test).
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

    # Seed centroids from random distinct rows.
    seed_indices = rng.sample(range(n), k)
    centroids: list[list[float]] = [list(normed[i]) for i in seed_indices]

    assignments = [0] * n
    for _ in range(max_iters):
        changed = False
        # Assign step.
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

        # Update step.
        new_centroids: list[list[float]] = []
        for c_idx in range(k):
            members = [normed[i] for i, a in enumerate(assignments) if a == c_idx]
            if members:
                new_centroids.append(_normalize(_mean(members)))
            else:
                # Empty cluster — keep previous centroid to avoid NaN.
                new_centroids.append(centroids[c_idx])
        centroids = new_centroids

        if not changed:
            break

    return assignments, centroids


# ---------------------------------------------------------------------------
# Summary content + metadata
# ---------------------------------------------------------------------------

def build_summary_text(previews: Iterable[str], *, chars_per_member: int = 200) -> str:
    """Join the first ``chars_per_member`` chars of each preview."""
    parts: list[str] = []
    for p in previews:
        if not p:
            continue
        snippet = p.strip().replace("\n", " ")
        if len(snippet) > chars_per_member:
            snippet = snippet[:chars_per_member].rstrip() + "..."
        parts.append(snippet)
    return " | ".join(parts)


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


def _vector_literal(v: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


def _summary_source_id(source_table: str, source_ids: Sequence[str]) -> str:
    """Derive a stable, unique source_id for the summary row."""
    # usedforsecurity=False — this SHA1 is a deterministic short ID for the
    # summary row's source_id, not an integrity/authenticity check. Keeps
    # bandit's B324 (weak-hash-for-security) from firing on a non-security
    # path. Algorithm choice is compatibility-driven (short + deterministic).
    digest = hashlib.sha1(
        ("|".join(sorted(source_ids))).encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:16]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"summary/{source_table}/{stamp}/{digest}"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------

class CollapseOldEmbeddingsJob:
    """Cluster+summarize old rows per source_table to cap pgvector growth."""

    name = "collapse_old_embeddings"
    description = (
        "Cluster old embeddings per source_table, write one summary row "
        "per cluster, and delete the originals inside a transaction."
    )
    schedule = "every 7 days"
    # Safe to overlap worst case — the candidate query filters on
    # is_summary=false and a partial progress is still correct.
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # Config resolution — app_settings wins over plugin-level config.
        enabled_raw = await _get_setting(
            pool, "embedding_collapse_enabled",
            "true" if config.get("enabled", False) else "false",
        )
        if not _parse_bool(enabled_raw):
            return JobResult(
                ok=True,
                detail="collapse disabled — set embedding_collapse_enabled=true to opt in",
                changes_made=0,
            )

        age_days = _parse_int(
            await _get_setting(pool, "embedding_collapse_age_days", "90"),
            90,
        )
        cluster_size = _parse_int(
            await _get_setting(pool, "embedding_collapse_cluster_size", "8"),
            8,
        )
        raw_sources = await _get_setting(
            pool, "embedding_collapse_source_tables",
            "claude_sessions,brain,audit",
        )
        source_tables = _parse_source_list(raw_sources)
        if not source_tables:
            return JobResult(
                ok=True,
                detail="no safe source tables configured",
                changes_made=0,
            )

        cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, age_days))

        total_collapsed = 0
        total_summaries = 0
        per_table: dict[str, dict[str, int]] = {}
        failures: list[str] = []

        for source_table in source_tables:
            try:
                result = await self._collapse_one_source(
                    pool,
                    source_table=source_table,
                    cutoff=cutoff,
                    cluster_size=cluster_size,
                    age_days=age_days,
                )
            except Exception as exc:  # noqa: BLE001 — never crash whole job
                logger.exception(
                    "[COLLAPSE] source=%s failed: %s", source_table, exc,
                )
                failures.append(f"{source_table}: {exc}")
                continue

            total_collapsed += result["collapsed"]
            total_summaries += result["summaries"]
            per_table[source_table] = result

        detail = (
            f"collapsed {total_collapsed} raw rows into "
            f"{total_summaries} summaries across {len(per_table)} table(s)"
        )
        if failures:
            detail += f"; {len(failures)} failure(s)"

        return JobResult(
            ok=not failures,
            detail=detail,
            changes_made=total_summaries,
            metrics={"per_table": per_table, "failures": failures},
        )

    # ------------------------------------------------------------------
    # per-source-table worker
    # ------------------------------------------------------------------

    async def _collapse_one_source(
        self,
        pool: Any,
        *,
        source_table: str,
        cutoff: datetime,
        cluster_size: int,
        age_days: int,
    ) -> dict[str, int]:
        result = {"candidates": 0, "collapsed": 0, "summaries": 0, "clusters": 0}

        if source_table in _NEVER_COLLAPSE:
            logger.warning(
                "[COLLAPSE] refusing to touch protected source_table=%s",
                source_table,
            )
            return result

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
                """,
                source_table,
                cutoff,
            )

        result["candidates"] = len(rows)
        # Need at least 2 rows for clustering to be meaningful.
        if len(rows) < 2:
            return result

        # Parse vectors up front. Skip rows with missing/malformed
        # vectors — they'll stay in place for the next run.
        parsed: list[dict[str, Any]] = []
        for r in rows:
            vec = _parse_vector(r["embedding"])
            if not vec:
                continue
            parsed.append({
                "id": r["id"],
                "source_id": r["source_id"],
                "text_preview": r["text_preview"] or "",
                "metadata": r["metadata"],
                "embedding": vec,
                "embedding_model": r["embedding_model"],
                "writer": r["writer"],
                "origin_path": r["origin_path"],
            })
        if len(parsed) < 2:
            return result

        # k = min(cluster_size, max(2, len(parsed) // 2)) — small groups
        # get fewer clusters so every cluster has multiple members.
        k = min(cluster_size, max(2, len(parsed) // 2))
        vectors = [p["embedding"] for p in parsed]
        assignments, centroids = kmeans_cluster(vectors, k)

        # Group rows by assignment.
        clusters: dict[int, list[dict[str, Any]]] = {}
        for i, a in enumerate(assignments):
            clusters.setdefault(a, []).append(parsed[i])

        # Dominant embedding_model in the group (handles mixed-model
        # groups conservatively — the summary keeps the most common
        # model tag so searches over that model still find it).
        model_counts: dict[str, int] = {}
        for p in parsed:
            m = p.get("embedding_model") or ""
            model_counts[m] = model_counts.get(m, 0) + 1
        dominant_model = max(model_counts.items(), key=lambda kv: kv[1])[0]
        if not dominant_model:
            dominant_model = "nomic-embed-text"

        for cluster_idx, members in clusters.items():
            if len(members) < 2:
                # A lone-member cluster isn't a compression — skip.
                continue

            if not centroids or cluster_idx >= len(centroids):
                continue
            centroid = centroids[cluster_idx]
            if not centroid:
                continue

            member_ids = [m["source_id"] for m in members]
            row_ids = [m["id"] for m in members]
            summary_id = _summary_source_id(source_table, member_ids)
            summary_text = build_summary_text(m["text_preview"] for m in members)
            metadata = build_summary_metadata(
                source_table,
                member_ids,
                cluster_index=int(cluster_idx),
                cluster_size=len(members),
                age_days=age_days,
            )

            try:
                summaries_written, deleted = await self._write_summary_and_delete(
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
                    "[COLLAPSE] cluster write failed source=%s cluster=%s: %s",
                    source_table, cluster_idx, exc,
                )
                continue

            if summaries_written:
                result["summaries"] += summaries_written
                result["clusters"] += 1
            result["collapsed"] += deleted

        return result

    async def _write_summary_and_delete(
        self,
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
        transaction rolls back and the raw rows stay in place.
        """
        now = datetime.now(timezone.utc)
        content_hash = _content_hash(summary_text or summary_id)
        vector_str = _vector_literal(centroid)
        text_preview = (summary_text or summary_id)[:500]
        metadata_json = json.dumps(metadata)

        async with pool.acquire() as conn:
            async with conn.transaction():
                # INSERT the summary first. If this fails the delete
                # doesn't happen and the raw rows survive.
                summary_row = await conn.fetchrow(
                    """
                    INSERT INTO embeddings (
                        source_table, source_id, content_hash, chunk_index,
                        text_preview, embedding_model, embedding, metadata,
                        writer, is_summary, created_at, updated_at
                    )
                    VALUES (
                        $1, $2, $3, 0,
                        $4, $5, $6::vector, $7::jsonb,
                        'collapse_job', TRUE, $8, $8
                    )
                    ON CONFLICT (source_table, source_id, chunk_index, embedding_model)
                    DO UPDATE SET content_hash = EXCLUDED.content_hash,
                                  embedding   = EXCLUDED.embedding,
                                  metadata    = EXCLUDED.metadata,
                                  text_preview = EXCLUDED.text_preview,
                                  is_summary  = TRUE,
                                  updated_at  = EXCLUDED.updated_at
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
                )
                if summary_row is None:
                    raise RuntimeError(
                        "summary insert returned no row — refusing to delete originals"
                    )

                # Verify the summary is actually in the table before
                # proceeding with the destructive delete.
                verify = await conn.fetchval(
                    "SELECT 1 FROM embeddings WHERE id = $1 AND is_summary = TRUE",
                    summary_row["id"],
                )
                if not verify:
                    raise RuntimeError(
                        "summary row verification failed — rolling back"
                    )

                # Delete the raw rows. Scoped by id list so we can't
                # accidentally drop siblings outside the cluster.
                deleted_count = 0
                for row_id in raw_row_ids:
                    result = await conn.execute(
                        "DELETE FROM embeddings WHERE id = $1 AND is_summary = FALSE",
                        row_id,
                    )
                    # asyncpg returns "DELETE N"
                    try:
                        deleted_count += int(result.split()[-1])
                    except (ValueError, IndexError):
                        pass

                if deleted_count != len(raw_row_ids):
                    logger.warning(
                        "[COLLAPSE] expected %d deletes, got %d (source=%s) — "
                        "tx will commit with the partial delete",
                        len(raw_row_ids), deleted_count, source_table,
                    )

        logger.info(
            "[COLLAPSE] source=%s wrote summary %s, deleted %d raw rows",
            source_table, summary_id, deleted_count,
        )
        return 1, deleted_count
