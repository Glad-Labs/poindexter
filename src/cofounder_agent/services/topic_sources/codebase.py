"""CodebaseSource — semantic topic ideation across pgvector embeddings.

Poindexter's differentiator: it knows YOUR data. Every tap (memory,
posts, audit, issues, brain) writes embeddings into the ``embeddings``
table; this source runs a set of seed queries against those embeddings
via pgvector cosine similarity and turns high-scoring hits into
DiscoveredTopic candidates.

Despite the legacy name "codebase", this is source-agnostic — it
ranks embeddings regardless of where they originated. Currently the
topic-extraction heuristic is conservative (only the ``posts``
source_table yields blog-topic candidates; the others generate
noise like internal audit events or memory-file preferences), but
new taps automatically flow through once they land in
``embeddings``.

Config (``plugin.topic_source.codebase`` in app_settings):

- ``enabled`` (default true)
- ``config.seed_queries`` — list of free-text queries to embed + search.
  Default: 8 broad technical themes.
- ``config.ollama_url`` — Ollama host for the embed call. Default
  resolves via site_config (``ollama_base_url``).
- ``config.embed_model`` — default resolves via site_config
  (``embed_model``, fallback ``nomic-embed-text``).
- ``config.lookback_days`` (default 30) — how far back in
  ``embeddings.created_at`` to search.
- ``config.similarity_threshold`` (default 0.4) — reject rows below
  this cosine similarity.
- ``config.per_query_limit`` (default 5) — top N rows per seed query.
- ``config.topic_max_chars`` (default 80) — truncate extracted topic
  titles to this length.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from plugins.topic_source import DiscoveredTopic

logger = logging.getLogger(__name__)


_DEFAULT_SEED_QUERIES: tuple[str, ...] = (
    "interesting architecture decisions and technical tradeoffs",
    "performance optimization and scaling challenges",
    "infrastructure automation and self-healing systems",
    "developer tools and workflow improvements",
    "database design and data pipeline engineering",
    "AI and machine learning in production systems",
    "monitoring alerting and observability patterns",
    "content generation and quality assurance automation",
)


# Source tables that don't yield blog-quality topics when extracted —
# internal system state, private prefs. `issues` and `audit` moved out
# of the skip set (2026-04-22) since the GiteaIssuesTap + AuditTap write
# text_previews in predictable shapes we can parse. `memory` stays: those
# are operator preferences + meta-notes, not blog-post material.
_SKIP_SOURCE_TABLES: frozenset[str] = frozenset({"memory"})


# Matches the first-line shape GiteaIssuesTap writes:
#   "Issue #229: feat: Event-driven topic discovery (replace fixed 8-hour cron)"
# Keeps the conventional-commit prefix (feat:/fix:/etc) dropped in the caller
# when framing the blog topic so the extracted title reads naturally.
import re as _re

_ISSUE_TITLE_RE = _re.compile(r"^Issue\s+#\d+:\s*(.+?)\s*$")
_CC_PREFIX_RE = _re.compile(r"^([a-z]+)(?:\([^)]+\))?!?:\s*(.+)$")


def _extract_topic_from_row(text: str, source_table: str, max_chars: int) -> str | None:
    """Extract a blog-worthy topic title from a row's text_preview.

    Source-agnostic derivation — the topic comes from the content,
    not the source metadata.

    - ``posts``: first long line of the preview (titles already publication-
      quality).
    - ``issues``: strip the "Issue #N: " prefix and the conventional-commit
      prefix, reframe as a first-person engineering topic. Matt's ask
      2026-04-22: internal-work fallback for when external sources dedup
      to zero. The issue bodies are already embedded via GiteaIssuesTap, so
      this rides the existing pgvector path — no new HTTP scrape needed.
    - Unknown source_tables: skip by default to avoid garbage from a
      new tap that hasn't been whitelisted here.
    """
    if not text or len(text) < 30:
        return None
    if source_table in _SKIP_SOURCE_TABLES:
        return None
    if source_table == "posts":
        for line in text.split("\n"):
            candidate = line.strip().lstrip("-# ")
            if len(candidate) > 40:
                return candidate[:max_chars]
        return None
    if source_table == "issues":
        first_line = text.split("\n", 1)[0].strip()
        m = _ISSUE_TITLE_RE.match(first_line)
        if not m:
            return None
        raw_title = m.group(1).strip()
        # Peel the conventional-commit prefix if present.
        prefix_match = _CC_PREFIX_RE.match(raw_title)
        if prefix_match:
            prefix, rest = prefix_match.group(1).lower(), prefix_match.group(2).strip()
            frames = {
                "feat": f"How we shipped: {rest}",
                "fix": f"Debugging: {rest}",
                "refactor": f"Refactoring: {rest}",
                "perf": f"Performance win: {rest}",
                "chore": f"Cleanup: {rest}",
                "docs": f"Documenting: {rest}",
                "security": f"Security writeup: {rest}",
            }
            framed = frames.get(prefix, rest)
        else:
            framed = raw_title
        framed = framed.rstrip(".").strip()
        # Minimum length guard: a 2-word issue title won't make a good post.
        if len(framed) < 20:
            return None
        return framed[:max_chars]
    return None


class CodebaseSource:
    """Semantic similarity search across pgvector embeddings."""

    name = "codebase"

    async def extract(
        self,
        pool: Any,  # asyncpg.Pool — required
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        if pool is None:
            logger.warning("CodebaseSource: no pool, returning empty")
            return []

        # Resolve config with site_config fallbacks.
        import services.site_config as _scm
        site_config = _scm.site_config
        seed_queries_cfg = config.get("seed_queries")
        seed_queries = (
            list(seed_queries_cfg) if isinstance(seed_queries_cfg, list) and seed_queries_cfg
            else list(_DEFAULT_SEED_QUERIES)
        )
        from services.bootstrap_defaults import DEFAULT_OLLAMA_URL
        ollama_url = (
            config.get("ollama_url")
            or site_config.get("ollama_base_url", DEFAULT_OLLAMA_URL)
        )
        embed_model = (
            config.get("embed_model")
            or site_config.get("embed_model", "nomic-embed-text")
            or "nomic-embed-text"
        )
        lookback_days = int(config.get("lookback_days", 30) or 30)
        similarity_threshold = float(config.get("similarity_threshold", 0.4) or 0.4)
        per_query_limit = int(config.get("per_query_limit", 5) or 5)
        topic_max_chars = int(config.get("topic_max_chars", 80) or 80)

        topics: list[DiscoveredTopic] = []
        seen_sources: set[str] = set()

        async with httpx.AsyncClient(timeout=30) as client:
            for query in seed_queries:
                # Per-query isolation: one seed failing shouldn't abort the
                # rest. Narrow except on network/HTTP layer failures only.
                try:
                    resp = await client.post(
                        f"{ollama_url}/api/embeddings",
                        json={"model": embed_model, "prompt": query},
                        timeout=15,
                    )
                except (httpx.HTTPError, httpx.TimeoutException) as e:
                    logger.debug("CodebaseSource: embed request failed for %r: %s", query[:30], e)
                    continue

                if resp.status_code != 200:
                    continue

                embedding = resp.json().get("embedding", [])
                if not embedding:
                    continue

                vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
                rows = await pool.fetch(
                    f"""
                    SELECT source_table, source_id, text_preview,
                           1 - (embedding <=> $1::vector) as similarity
                    FROM embeddings
                    WHERE source_table != 'posts_authored'
                      AND created_at > NOW() - INTERVAL '{int(lookback_days)} days'
                    ORDER BY embedding <=> $1::vector
                    LIMIT $2
                    """,  # nosec B608  # lookback_days int-cast inline; values use $N params
                    vec_str, per_query_limit,
                )

                for row in rows:
                    sim = float(row["similarity"])
                    source_id = row["source_id"]
                    if sim < similarity_threshold or source_id in seen_sources:
                        continue
                    seen_sources.add(source_id)

                    preview = (row["text_preview"] or "")[:300].strip()
                    if len(preview) < 30:
                        continue

                    topic_title = _extract_topic_from_row(
                        preview, row["source_table"], topic_max_chars,
                    )
                    if not topic_title:
                        continue

                    topics.append(
                        DiscoveredTopic(
                            title=topic_title,
                            category="technology",
                            source=f"embeddings:{row['source_table']}",
                            source_url="",
                            # Shift [threshold, 1.0] → [threshold+0.3, 1.3]
                            # then cap at 0.9 for mid-range-tier scoring.
                            relevance_score=min(0.9, sim + 0.3),
                        )
                    )

        logger.info(
            "CodebaseSource: %d topics from %d seed queries (threshold=%.2f)",
            len(topics), len(seed_queries), similarity_threshold,
        )
        return topics
