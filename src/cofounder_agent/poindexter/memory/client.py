"""MemoryClient — shared pgvector memory client for every poindexter tool.

See `poindexter.memory.__init__` for usage. This module is the ONLY place
that touches the embeddings table schema, the Ollama embedder, and the
writer namespacing — every other caller in the repo should migrate onto it
over time (Gitea #192 slice 3).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_EMBED_DIM = 768

# Known writers — free-form string, but keeping the set small makes the
# `/memory` dashboard stats easier to read. Callers can pass anything; this
# list exists so every tool reaches for the same labels first.
KNOWN_WRITERS = frozenset(
    {
        "claude-code",
        "openclaw",
        "worker",
        "user",
        "shared-context",
        "gitea",
    }
)

# Known source_table namespaces — writing to anything else is allowed but
# logs a warning because the dashboard groups by source_table.
KNOWN_SOURCE_TABLES = frozenset({"memory", "posts", "issues", "audit"})


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MemoryHit:
    """One search result row from `MemoryClient.search`."""

    source_table: str
    source_id: str
    similarity: float
    text_preview: str
    writer: str | None
    origin_path: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        writer_str = f" ({self.writer})" if self.writer else ""
        return (
            f"[{self.similarity:.3f}] {self.source_table}/{self.source_id}"
            f"{writer_str}: {self.text_preview[:80]}"
        )


# ---------------------------------------------------------------------------
# MemoryClient
# ---------------------------------------------------------------------------


class MemoryClient:
    """High-level shared-memory client.

    Owns its own asyncpg pool and httpx client. Use as a context manager OR
    call `connect()` / `close()` explicitly. Thread-safe within an asyncio
    event loop (asyncpg handles pooling).

    Connection config is resolved in this order (#198: no hardcoded defaults):
      1. explicit dsn / ollama_url args to __init__
      2. POINDEXTER_MEMORY_DSN env var (or fall back to DATABASE_URL)
      3. OLLAMA_URL env var, then OLLAMA_BASE_URL (Poindexter worker
         containers set OLLAMA_BASE_URL; the standalone CLI path still
         accepts OLLAMA_URL — GH#93 Phase 1 consolidation)
      4. ``app_settings.ollama_base_url`` (then ``ollama_url``) read via the
         pool during ``connect()`` — DB-first config (#368). Lets fresh CLI
         installs work without operator-managed env vars.
      5. RuntimeError — no silent localhost fallback
    """

    def __init__(
        self,
        dsn: str | None = None,
        ollama_url: str | None = None,
        embed_model: str = DEFAULT_EMBED_MODEL,
        embed_dim: int = DEFAULT_EMBED_DIM,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
    ) -> None:
        # Resolution order: explicit `dsn=` arg → env vars (legacy) →
        # ``~/.poindexter/bootstrap.toml::database_url`` (last-ditch
        # fallback so a fresh install works without operator-managed env
        # vars). Explicit + env var stay first for backward compat with
        # callers that already have those wired.
        try:
            from poindexter.cli._bootstrap import read_bootstrap_value
            _toml_dsn = read_bootstrap_value("database_url")
        except Exception:  # noqa: BLE001
            _toml_dsn = ""
        self.dsn = (
            dsn
            or os.getenv("POINDEXTER_MEMORY_DSN")
            or os.getenv("LOCAL_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or _toml_dsn
            or ""
        )
        if not self.dsn:
            raise RuntimeError(
                "MemoryClient requires a DSN. Pass dsn=, set any of "
                "POINDEXTER_MEMORY_DSN / LOCAL_DATABASE_URL / DATABASE_URL "
                "in env, OR populate ~/.poindexter/bootstrap.toml::database_url."
            )
        # OLLAMA_URL kept for the standalone CLI path; OLLAMA_BASE_URL is
        # the name the worker container + compose stack uses, so accept
        # both and prefer OLLAMA_URL when set (GH#93 Phase 1). When neither
        # the kwarg nor an env var is set, defer the failure to connect()
        # which can fall through to app_settings via the DB pool (#368).
        resolved_ollama = (
            ollama_url or os.getenv("OLLAMA_URL") or os.getenv("OLLAMA_BASE_URL")
        )
        self.ollama_url: str | None = (
            resolved_ollama.rstrip("/") if resolved_ollama else None
        )
        self.embed_model = embed_model
        self.embed_dim = embed_dim
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool: asyncpg.Pool | None = None
        self._http: httpx.AsyncClient | None = None

    # ----- lifecycle ------------------------------------------------------

    async def connect(self) -> None:
        """Create the pool + HTTP client. Idempotent."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self.dsn,
                min_size=self._pool_min_size,
                max_size=self._pool_max_size,
            )
        if self.ollama_url is None:
            self.ollama_url = await self._resolve_ollama_url_from_settings()
        if self._http is None:
            # 30s is generous for a single embed call; raise if you're
            # embedding very long documents in one shot.
            self._http = httpx.AsyncClient(timeout=30.0)

    async def _resolve_ollama_url_from_settings(self) -> str:
        """Look up ``ollama_base_url`` (then ``ollama_url``) from app_settings.

        Last step of the resolution chain — runs only when no kwarg or env
        var supplied a value. Uses the pool we just created in connect()
        so we don't open a second connection. Raises a descriptive
        RuntimeError when nothing is configured anywhere.
        """
        assert self._pool is not None  # connect() created it
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT value
                FROM app_settings
                WHERE key IN ('ollama_base_url', 'ollama_url')
                ORDER BY key = 'ollama_base_url' DESC
                LIMIT 1
                """
            )
        value = (row["value"] if row else None) or ""
        if not value:
            raise RuntimeError(
                "MemoryClient requires an Ollama URL. None resolved from "
                "explicit kwarg, OLLAMA_URL/OLLAMA_BASE_URL env vars, or "
                "app_settings.ollama_base_url. Set it via "
                "`poindexter settings set ollama_base_url <url>` "
                "(DB-first config — no hardcoded default, #198/#368)."
            )
        return value.rstrip("/")

    async def close(self) -> None:
        """Close the pool + HTTP client. Safe to call multiple times.

        Suppression is narrow to the teardown path — raising here would
        mask the real error that sent us into close() in the first place.
        """
        if self._http is not None:
            with suppress(Exception):
                await self._http.aclose()
            self._http = None
        if self._pool is not None:
            with suppress(Exception):
                await self._pool.close()
            self._pool = None

    async def __aenter__(self) -> MemoryClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # ----- embedding ------------------------------------------------------

    async def embed(self, text: str) -> list[float]:
        """Embed a single text blob via Ollama. Returns a 768-dim vector."""
        if self._http is None:
            await self.connect()
        assert self._http is not None  # for type checker

        resp = await self._http.post(
            f"{self.ollama_url}/api/embed",
            json={"model": self.embed_model, "input": text},
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings") or []
        if not embeddings:
            raise RuntimeError(
                f"Ollama embed returned empty result "
                f"(model={self.embed_model}, text_len={len(text)})"
            )
        vec = embeddings[0]
        if len(vec) != self.embed_dim:
            raise RuntimeError(
                f"Expected {self.embed_dim}-dim vector from {self.embed_model}, "
                f"got {len(vec)}"
            )
        return vec

    # ----- write ----------------------------------------------------------

    async def store(
        self,
        *,
        text: str,
        writer: str,
        source_id: str | None = None,
        source_table: str = "memory",
        chunk_index: int = 0,
        tags: Iterable[str] | None = None,
        metadata: dict[str, Any] | None = None,
        origin_path: str | None = None,
        embedding: list[float] | None = None,
        content_hash: str | None = None,
        embedding_model: str | None = None,
    ) -> str:
        """Embed `text` and UPSERT into the embeddings table.

        Args:
            text: The full text content. Required. Used for the
                  text_preview, content hash (unless overridden), and for
                  auto-embedding when no explicit `embedding` is passed.
            writer: Origin label. e.g. "claude-code", "openclaw", "worker".
            source_id: Stable id for dedup. If omitted, a timestamp+hash id
                       is generated under the writer namespace.
            source_table: Coarse namespace. Default "memory".
            chunk_index: Chunk position for multi-chunk documents. Default 0.
            tags: Optional string tags merged into metadata.
            metadata: Optional extra metadata dict. `origin`, `writer`,
                      `stored_at` are always set automatically.
            origin_path: Original filesystem path or URL, for traceability.
                         Defaults to `source_id`.
            embedding: Optional pre-computed 768-dim vector. When provided,
                       the Ollama embed call is skipped entirely — use this
                       to ingest vectors that were already computed by
                       another tool (e.g. OpenClaw's SQLite chunks).
            content_hash: Optional pre-computed SHA-256 hash. Defaults to
                          sha256(text). Override when your source tool
                          already has a stable chunk id (e.g. OpenClaw
                          stores the chunk id AS a SHA-256 hash).
            embedding_model: Override the stored `embedding_model` column.
                             Defaults to the client's configured model.
                             Only relevant when ingesting vectors from a
                             differently-named source (e.g. OpenClaw calls
                             the same model "nomic-embed-text:latest").

        Returns:
            The final `source_id` that was written (useful when it was generated).
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("text is required and cannot be empty")
        if not writer:
            raise ValueError("writer is required")

        if writer not in KNOWN_WRITERS:
            logger.debug("Writing memory with non-standard writer=%r", writer)
        if source_table not in KNOWN_SOURCE_TABLES:
            logger.debug("Writing memory with non-standard source_table=%r", source_table)

        if source_id is None:
            epoch = int(datetime.now(timezone.utc).timestamp())
            short = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
            source_id = f"{writer}/adhoc-{epoch}-{short}.md"

        final_hash = content_hash or hashlib.sha256(text.encode("utf-8")).hexdigest()
        if embedding is None:
            embedding = await self.embed(text)
        elif len(embedding) != self.embed_dim:
            raise ValueError(
                f"Pre-computed embedding has {len(embedding)} dims, "
                f"expected {self.embed_dim}"
            )
        final_model = embedding_model or self.embed_model

        merged_metadata: dict[str, Any] = {
            "origin": writer,
            "writer": writer,
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            merged_metadata.update(metadata)
        if tags:
            merged_metadata["tags"] = [t for t in tags if t]

        vector_str = "[" + ",".join(str(v) for v in embedding) + "]"
        preview = text[:500].replace("\n", " ").strip()
        now = datetime.now(timezone.utc)
        origin = origin_path or source_id

        pool = await self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO embeddings (source_table, source_id, chunk_index,
                                        content_hash, text_preview,
                                        embedding_model, embedding, metadata,
                                        writer, origin_path,
                                        created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7::vector, $8::jsonb, $9, $10, $11, $11)
                ON CONFLICT (source_table, source_id, chunk_index, embedding_model)
                DO UPDATE SET content_hash = EXCLUDED.content_hash,
                              text_preview = EXCLUDED.text_preview,
                              embedding    = EXCLUDED.embedding,
                              metadata     = EXCLUDED.metadata,
                              writer       = EXCLUDED.writer,
                              origin_path  = EXCLUDED.origin_path,
                              updated_at   = EXCLUDED.updated_at
                """,
                source_table,
                source_id,
                chunk_index,
                final_hash,
                preview,
                final_model,
                vector_str,
                json.dumps(merged_metadata),
                writer,
                origin,
                now,
            )
        return source_id

    async def store_file(
        self,
        path: Path | str,
        *,
        writer: str,
        source_table: str = "memory",
        source_id_prefix: str | None = None,
    ) -> str | None:
        """Read a file from disk and store it as a single embedding.

        Skips files whose content hasn't changed since the last store
        (checked via content_hash in the DB).

        Returns the source_id written, or None if the file was skipped.
        """
        path = Path(path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(path)

        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return None

        if source_id_prefix:
            source_id = f"{source_id_prefix.rstrip('/')}/{path.name}"
        else:
            source_id = f"{writer}/{path.name}"

        new_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if not await self._needs_reembedding(source_table, source_id, new_hash):
            return None

        await self.store(
            text=text,
            writer=writer,
            source_id=source_id,
            source_table=source_table,
            metadata={"filename": path.name, "chars": len(text)},
            origin_path=str(path),
        )
        return source_id

    # ----- read -----------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        writer: str | None = None,
        source_table: str | None = None,
        min_similarity: float = 0.0,
        limit: int = 10,
    ) -> list[MemoryHit]:
        """Semantic search across embeddings.

        Args:
            query: Natural language query. Embedded via nomic-embed-text.
            writer: Optional filter on the writer column (e.g. "claude-code").
            source_table: Optional filter on source_table (e.g. "memory", "posts").
                          Note the plural! Passing "post" silently returns zero.
            min_similarity: Cosine-similarity floor (0.0 = no floor).
            limit: Max rows to return.

        Returns a list of MemoryHit objects, sorted by similarity descending.

        When ``app_settings.rag_engine_enabled`` is true AND no ``writer``
        filter is passed, the query is routed through the LlamaIndex
        retriever (``services.rag_engine.get_rag_retriever``) instead of
        the inline pgvector path. That activates whatever extras the
        operator has enabled — ``rag_hybrid_enabled`` (BM25 + vector RRF
        fusion), ``rag_rerank_enabled`` (cross-encoder rerank), etc.
        Writer-filtered queries fall through to the legacy path because
        the rag_engine retriever doesn't expose a writer filter today.
        """
        if not query.strip():
            return []

        # Lane D #329 sub-issue 4: optional LlamaIndex routing.
        # Default-off; operator opts in via rag_engine_enabled. Writer
        # filter skips the routing because the retriever has no
        # writer-filter parameter yet.
        if writer is None and await self._rag_engine_enabled():
            try:
                hits = await self._search_via_rag_engine(
                    query,
                    source_table=source_table,
                    min_similarity=min_similarity,
                    limit=limit,
                )
                return hits
            except Exception as e:
                # Fail-soft: never let the LlamaIndex path break the
                # legacy semantic-search contract. Log + fall through.
                logger.warning(
                    "[memory] rag_engine path failed (%s) — falling back to legacy",
                    e,
                )

        embedding = await self.embed(query)
        vector_str = "[" + ",".join(str(v) for v in embedding) + "]"

        where_clauses = ["1 - (embedding <=> $1::vector) >= $2"]
        args: list[Any] = [vector_str, min_similarity]
        if source_table:
            where_clauses.append(f"source_table = ${len(args) + 1}")
            args.append(source_table)
        if writer:
            where_clauses.append(f"writer = ${len(args) + 1}")
            args.append(writer)

        args.append(limit)
        sql = f"""
            SELECT source_table, source_id, text_preview, metadata,
                   writer, origin_path,
                   1 - (embedding <=> $1::vector) as similarity
            FROM embeddings
            WHERE {' AND '.join(where_clauses)}
            ORDER BY embedding <=> $1::vector
            LIMIT ${len(args)}
        """

        pool = await self._require_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)

        hits: list[MemoryHit] = []
        for row in rows:
            meta = row["metadata"]
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except json.JSONDecodeError:
                    meta = {}
            hits.append(
                MemoryHit(
                    source_table=row["source_table"],
                    source_id=row["source_id"],
                    similarity=float(row["similarity"]),
                    text_preview=row["text_preview"] or "",
                    writer=row["writer"],
                    origin_path=row["origin_path"],
                    metadata=meta or {},
                )
            )
        return hits

    # ----- convenience helpers -------------------------------------------

    async def search_decisions(
        self, query: str, *, limit: int = 5
    ) -> list[MemoryHit]:
        """Search memory for decisions/feedback/project context only.

        Equivalent to the `recall_decision` MCP tool but without the
        HTTP hop. Filters to `source_table='memory'`.
        """
        return await self.search(
            query,
            source_table="memory",
            limit=limit,
            min_similarity=0.3,
        )

    async def find_similar_posts(
        self, topic: str, *, limit: int = 5, min_similarity: float = 0.75
    ) -> list[MemoryHit]:
        """Find published posts similar to a topic. Powers the pre-generation
        semantic dedup check in task_executor.

        Note: source_table is 'posts' (plural). Passing 'post' silently
        returns zero matches — that bug already burned us twice, so this
        helper hardcodes the correct value.
        """
        return await self.search(
            topic,
            source_table="posts",
            limit=limit,
            min_similarity=min_similarity,
        )

    async def stats(self) -> dict[str, dict[str, Any]]:
        """Return counts + latest update per source_table and writer.

        Intended as the data source for the /memory dashboard from #192.

        Returns a dict of the shape:
            {
                "by_source_table": {"memory": {"count": 172, "newest": datetime, ...}, ...},
                "by_writer":       {"claude-code": {"count": 70, "newest": datetime, ...}, ...},
            }
        """
        pool = await self._require_pool()
        async with pool.acquire() as conn:
            by_source = await conn.fetch(
                """
                SELECT source_table as key,
                       COUNT(*) as count,
                       MIN(created_at) as oldest,
                       MAX(updated_at) as newest
                FROM embeddings
                GROUP BY source_table
                ORDER BY COUNT(*) DESC
                """
            )
            by_writer = await conn.fetch(
                """
                SELECT COALESCE(writer, 'unknown') as key,
                       COUNT(*) as count,
                       MIN(created_at) as oldest,
                       MAX(updated_at) as newest
                FROM embeddings
                GROUP BY writer
                ORDER BY COUNT(*) DESC
                """
            )

        def _rows_to_dict(rows) -> dict[str, dict[str, Any]]:
            out: dict[str, dict[str, Any]] = {}
            for r in rows:
                out[r["key"]] = {
                    "count": int(r["count"]),
                    "oldest": r["oldest"],
                    "newest": r["newest"],
                }
            return out

        return {
            "by_source_table": _rows_to_dict(by_source),
            "by_writer": _rows_to_dict(by_writer),
        }

    # ----- internals ------------------------------------------------------

    async def _needs_reembedding(
        self, source_table: str, source_id: str, new_hash: str
    ) -> bool:
        """Return True when the row is absent OR its hash differs."""
        pool = await self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT content_hash FROM embeddings
                WHERE source_table = $1 AND source_id = $2
                  AND chunk_index = 0 AND embedding_model = $3
                """,
                source_table,
                source_id,
                self.embed_model,
            )
        if row is None:
            return True
        return row["content_hash"] != new_hash

    async def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            await self.connect()
        assert self._pool is not None
        return self._pool

    async def _rag_engine_enabled(self) -> bool:
        """Return True when ``app_settings.rag_engine_enabled = 'true'``.

        Per #329 sub-issue 4 — operator opts in to LlamaIndex retrieval.
        Reads through the pool MemoryClient already owns; the value is
        cheap enough that we don't bother caching (one row per call,
        and the call site is a single semantic search at content-pipeline
        cadence — not a hot loop).
        """
        try:
            pool = await self._require_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT value FROM app_settings "
                    "WHERE key = 'rag_engine_enabled' AND is_active = true "
                    "LIMIT 1"
                )
        except Exception:
            return False
        if not row:
            return False
        return str(row["value"] or "").strip().lower() in ("true", "1", "yes", "on")

    async def _search_via_rag_engine(
        self,
        query: str,
        *,
        source_table: str | None,
        min_similarity: float,
        limit: int,
    ) -> list[MemoryHit]:
        """Route the search through ``services.rag_engine.get_rag_retriever``.

        Converts the LlamaIndex ``NodeWithScore`` output back into
        ``MemoryHit`` so callers see the same shape regardless of which
        path serviced the query. ``writer`` and ``origin_path`` survive
        the round-trip via the retriever's metadata payload.
        """
        from services.rag_engine import get_rag_retriever

        pool = await self._require_pool()
        retriever = await get_rag_retriever(
            pool,
            top_k=limit,
            min_similarity=min_similarity,
            source_filter=[source_table] if source_table else None,
        )
        nodes = await retriever.aretrieve(query)

        hits: list[MemoryHit] = []
        for nws in nodes:
            md = dict(getattr(nws.node, "metadata", {}) or {})
            hits.append(
                MemoryHit(
                    source_table=str(md.get("source_table", "")),
                    source_id=str(md.get("source_id", "")),
                    similarity=float(getattr(nws, "score", 0.0) or 0.0),
                    text_preview=getattr(nws.node, "text", "") or "",
                    writer=md.get("writer"),
                    origin_path=md.get("origin_path"),
                    metadata={
                        k: v
                        for k, v in md.items()
                        if k not in ("source_table", "source_id", "writer", "origin_path")
                    },
                )
            )
        return hits
