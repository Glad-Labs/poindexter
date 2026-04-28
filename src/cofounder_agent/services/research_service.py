"""
Research Service — builds factual context for content generation.

Provides real links, documentation references, and verified facts that get
injected into the generation prompt. Content generated with research context
produces posts with real citations instead of fabricated ones.

Sources (in priority order):
1. Known reference database (official docs, always valid)
2. Serper web search (when API key available)
3. Existing published posts (internal linking)

Usage:
    from services.research_service import ResearchService
    research = ResearchService(pool, site_config=site_config)
    context = await research.build_context("FastAPI and PostgreSQL")
    # context is a formatted string ready for the generation prompt
"""

import re
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)

# Verified reference links — official documentation that won't go stale.
# These are real URLs to real documentation. NO fabricated links.
#
# The shipped defaults are a tech/developer-audience set. Customers in
# other niches (cooking, legal, medical, gardening) override via
# app_settings.known_references_json — a JSON blob of the same shape
# as `_DEFAULT_KNOWN_REFERENCES` below. See `get_known_references()`. (#198)
_DEFAULT_KNOWN_REFERENCES: dict[str, list[dict[str, str]]] = {
    "fastapi": [
        {"title": "FastAPI Official Documentation", "url": "https://fastapi.tiangolo.com"},
        {"title": "FastAPI Tutorial - First Steps", "url": "https://fastapi.tiangolo.com/tutorial/first-steps/"},
        {"title": "FastAPI on GitHub", "url": "https://github.com/tiangolo/fastapi"},
    ],
    "postgresql": [
        {"title": "PostgreSQL Official Documentation", "url": "https://www.postgresql.org/docs/current/"},
        {"title": "PostgreSQL Tutorial", "url": "https://www.postgresql.org/docs/current/tutorial.html"},
    ],
    "docker": [
        {"title": "Docker Official Documentation", "url": "https://docs.docker.com"},
        {"title": "Docker Compose Overview", "url": "https://docs.docker.com/compose/"},
        {"title": "Dockerfile Reference", "url": "https://docs.docker.com/reference/dockerfile/"},
    ],
    "kubernetes": [
        {"title": "Kubernetes Documentation", "url": "https://kubernetes.io/docs/home/"},
        {"title": "Kubernetes Concepts", "url": "https://kubernetes.io/docs/concepts/"},
    ],
    "grafana": [
        {"title": "Grafana Documentation", "url": "https://grafana.com/docs/grafana/latest/"},
        {"title": "Grafana Dashboard Best Practices", "url": "https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/best-practices/"},
    ],
    "prometheus": [
        {"title": "Prometheus Documentation", "url": "https://prometheus.io/docs/introduction/overview/"},
        {"title": "Prometheus Querying Basics", "url": "https://prometheus.io/docs/prometheus/latest/querying/basics/"},
    ],
    "next.js": [
        {"title": "Next.js Documentation", "url": "https://nextjs.org/docs"},
        {"title": "Next.js App Router", "url": "https://nextjs.org/docs/app"},
    ],
    "nextjs": [
        {"title": "Next.js Documentation", "url": "https://nextjs.org/docs"},
    ],
    "python": [
        {"title": "Python Official Documentation", "url": "https://docs.python.org/3/"},
        {"title": "Python Package Index (PyPI)", "url": "https://pypi.org"},
    ],
    "ollama": [
        {"title": "Ollama Official Site", "url": "https://ollama.com"},
        {"title": "Ollama GitHub", "url": "https://github.com/ollama/ollama"},
        {"title": "Ollama Model Library", "url": "https://ollama.com/library"},
    ],
    "llm": [
        {"title": "Hugging Face Model Hub", "url": "https://huggingface.co/models"},
        {"title": "LLM Leaderboard", "url": "https://huggingface.co/spaces/open-llm-leaderboard/open_llm_leaderboard"},
    ],
    "rag": [
        {"title": "LangChain RAG Tutorial", "url": "https://python.langchain.com/docs/tutorials/rag/"},
        {"title": "Hugging Face RAG Documentation", "url": "https://huggingface.co/docs/transformers/model_doc/rag"},
    ],
    "redis": [
        {"title": "Redis Documentation", "url": "https://redis.io/docs/latest/"},
        {"title": "Redis Commands Reference", "url": "https://redis.io/docs/latest/commands/"},
    ],
    "graphql": [
        {"title": "GraphQL Official Documentation", "url": "https://graphql.org/learn/"},
        {"title": "GraphQL Best Practices", "url": "https://graphql.org/learn/best-practices/"},
    ],
    "terraform": [
        {"title": "Terraform Documentation", "url": "https://developer.hashicorp.com/terraform/docs"},
        {"title": "Terraform Registry", "url": "https://registry.terraform.io"},
    ],
    "ci/cd": [
        {"title": "GitHub Actions Documentation", "url": "https://docs.github.com/en/actions"},
        {"title": "GitLab CI/CD Documentation", "url": "https://docs.gitlab.com/ee/ci/"},
    ],
    "ai agent": [
        {"title": "Anthropic Agent SDK", "url": "https://docs.anthropic.com/en/docs/agents-and-tools/"},
        {"title": "LangChain Agents", "url": "https://python.langchain.com/docs/how_to/#agents"},
    ],
    "edge computing": [
        {"title": "Cloudflare Workers Documentation", "url": "https://developers.cloudflare.com/workers/"},
        {"title": "AWS Lambda@Edge", "url": "https://docs.aws.amazon.com/lambda/latest/dg/lambda-edge.html"},
    ],
    "monitoring": [
        {"title": "Grafana Documentation", "url": "https://grafana.com/docs/grafana/latest/"},
        {"title": "Prometheus Documentation", "url": "https://prometheus.io/docs/introduction/overview/"},
        {"title": "OpenTelemetry Documentation", "url": "https://opentelemetry.io/docs/"},
    ],
}


def get_known_references(site_config: Any = None) -> dict[str, list[dict[str, str]]]:
    """Return the reference-link database, preferring app_settings if set.

    Looks up `known_references_json` in app_settings; if present and
    valid, replaces the default tech-oriented list entirely. Malformed
    JSON logs a warning and falls back to defaults. (#198)

    Args:
        site_config: SiteConfig instance (DI — Phase H, GH#95). ``None``
            returns the hardcoded defaults so legacy callers that haven't
            been threaded through yet still work.
    """
    import json as _json
    try:
        if site_config is None:
            return _DEFAULT_KNOWN_REFERENCES
        raw = site_config.get("known_references_json", "")
        if not raw:
            return _DEFAULT_KNOWN_REFERENCES
        parsed = _json.loads(raw)
        if not isinstance(parsed, dict):
            logger.warning(
                "[RESEARCH] known_references_json must be a JSON object — using defaults"
            )
            return _DEFAULT_KNOWN_REFERENCES
        # Shape validation: each value must be a list of {title, url} dicts.
        clean: dict[str, list[dict[str, str]]] = {}
        for key, entries in parsed.items():
            if not isinstance(entries, list):
                continue
            ok_entries = [
                {"title": str(e.get("title", "")), "url": str(e.get("url", ""))}
                for e in entries
                if isinstance(e, dict) and e.get("url")
            ]
            if ok_entries:
                clean[str(key).lower()] = ok_entries
        return clean or _DEFAULT_KNOWN_REFERENCES
    except (ValueError, TypeError) as e:
        logger.warning(
            "[RESEARCH] known_references_json is not valid JSON: %s — using defaults", e
        )
        return _DEFAULT_KNOWN_REFERENCES
    except Exception as e:
        logger.warning(
            "[RESEARCH] known_references lookup failed: %s — using defaults", e
        )
        return _DEFAULT_KNOWN_REFERENCES


# Backward-compat alias: modules that imported KNOWN_REFERENCES keep working
# but get the DEFAULTS. Migrate callers to get_known_references() for the
# settings-aware version.
KNOWN_REFERENCES = _DEFAULT_KNOWN_REFERENCES


class ResearchService:
    """Builds research context for content generation."""

    def __init__(self, pool=None, settings_service=None, *, site_config: Any = None):
        """
        Args:
            pool: asyncpg connection pool (optional — without it, internal
                link lookup returns empty).
            settings_service: Legacy settings service (reserved; unused
                today).
            site_config: SiteConfig instance (DI — Phase H, GH#95). Used
                to resolve ``known_references_json`` override. ``None``
                means the hardcoded defaults will always apply — keeps
                legacy callers working during Phase H rollout.
        """
        self.pool = pool
        self.settings = settings_service
        self._site_config = site_config

    async def build_context(
        self,
        topic: str,
        category: str = "technology",  # noqa: ARG002 — reserved for future per-category research sources (e.g., HN for tech, Dribbble for design)
    ) -> str:
        """Build research context string for the generation prompt.

        Returns a formatted string with:
        - Relevant documentation links
        - Existing published posts for internal linking
        - Web search results (if Serper key available)
        """
        sections = []

        # 1. Find matching known references
        refs = self._find_references(topic)
        if refs:
            ref_lines = ["VERIFIED REFERENCE LINKS (use these as citations):"]
            for ref in refs:
                ref_lines.append(f"- [{ref['title']}]({ref['url']})")
            sections.append("\n".join(ref_lines))

        # 2. Find existing published posts for internal linking
        internal = await self._find_internal_links(topic)
        if internal:
            int_lines = ["EXISTING POSTS ON OUR SITE (link to these where relevant):"]
            for post in internal:
                int_lines.append(f"- [{post['title']}](/posts/{post['slug']})")
            sections.append("\n".join(int_lines))

        # 3. Free web search via DuckDuckGo (replaces Serper)
        web_results = await self._web_search(topic)
        if web_results:
            web_lines = ["RECENT WEB SOURCES (cite if relevant):"]
            for result in web_results:
                web_lines.append(f"- [{result['title']}]({result['url']}): {result.get('snippet', '')[:100]}")
            sections.append("\n".join(web_lines))

        # 4. Add writing guidance based on available sources
        if sections:
            sections.append(
                "CITATION GUIDANCE:\n"
                "- Link to the reference URLs above when discussing those tools/concepts\n"
                "- Use format: [Tool Name](url) for inline links\n"
                "- Attribute facts to their source: 'According to the [official docs](url)...'\n"
                "- Link to our existing posts where the topic overlaps"
            )

        context = "\n\n".join(sections)
        logger.info("[RESEARCH] Built context for '%s': %d refs, %d internal, %d web",
                     topic[:40], len(refs), len(internal), len(web_results))
        return context

    def _find_references(self, topic: str) -> list[dict[str, str]]:
        """Match topic keywords against known reference database."""
        topic_lower = topic.lower()
        matched = []
        seen_urls = set()

        _refs = get_known_references(self._site_config)
        for keyword, refs in _refs.items():
            if keyword in topic_lower:
                for ref in refs:
                    if ref["url"] not in seen_urls:
                        matched.append(ref)
                        seen_urls.add(ref["url"])

        # Also match individual words for broader coverage
        topic_words = set(re.findall(r"\b\w{4,}\b", topic_lower))
        for keyword, refs in _refs.items():
            kw_words = set(keyword.lower().split())
            if kw_words & topic_words and keyword not in topic_lower:
                for ref in refs[:1]:  # Only first ref for partial matches
                    if ref["url"] not in seen_urls:
                        matched.append(ref)
                        seen_urls.add(ref["url"])

        return matched[:8]  # Cap at 8 references

    async def _find_internal_links(self, topic: str) -> list[dict[str, str]]:
        """Find existing published posts related to the topic.

        Two paths controlled by ``app_settings.rag_enabled_for_research``:

        - **off (default):** legacy ILIKE word-overlap match against
          posts.title / posts.slug. Cheap, deterministic, low recall on
          paraphrased topics.
        - **on:** LlamaIndex retriever (#210) over the embeddings table,
          filtered to ``source_table='posts'``. Picks up semantic
          neighbors that ILIKE misses — e.g. a post on "Bootstrapping
          a SaaS Startup" surfaces for a topic like "Indie Hacker
          Founder Strategy". Honors the same hybrid + rerank flags
          (``rag_hybrid_enabled``, ``rag_rerank_enabled``) the rest of
          the RAG layer respects, so flipping those toggles cascades
          through the research stage without code changes here.

        The RAG path falls through to the legacy ILIKE on any error
        (Ollama down, pgvector miss, etc) so research never blocks.
        """
        if not self.pool:
            return []

        if self._rag_research_enabled():
            try:
                rag_results = await self._rag_internal_links(topic)
                if rag_results:
                    return rag_results
            except Exception as e:
                logger.debug(
                    "[RESEARCH] RAG retrieval failed, falling back to ILIKE: %s", e,
                )

        try:
            # Search by topic word overlap
            topic_words = [w for w in re.findall(r"\b\w{4,}\b", topic.lower()) if len(w) > 3]
            if not topic_words:
                return []

            # Use the first few significant words for search (ILIKE against
            # each word — an earlier version built a tsquery string here,
            # which this query never used).
            rows = await self.pool.fetch("""
                SELECT title, slug FROM posts
                WHERE status = 'published'
                AND (
                    title ILIKE ANY($1)
                    OR slug ILIKE ANY($1)
                )
                LIMIT 5
            """, [f"%{w}%" for w in topic_words[:3]])

            return [{"title": r["title"], "slug": r["slug"]} for r in rows]
        except Exception as e:
            logger.debug("[RESEARCH] Internal link search failed: %s", e)
            return []

    def _rag_research_enabled(self) -> bool:
        if self._site_config is None:
            return False
        try:
            return bool(self._site_config.get_bool("rag_enabled_for_research", False))
        except Exception:
            try:
                v = self._site_config.get("rag_enabled_for_research", "")
                return str(v).strip().lower() in ("true", "1", "yes", "on")
            except Exception:
                return False

    async def _rag_internal_links(self, topic: str) -> list[dict[str, str]]:
        """LlamaIndex-backed retrieval — query the RAG layer scoped to
        published posts, hydrate the matches into the same
        ``[{title, slug}]`` shape the legacy ILIKE path returns.

        Embeddings store the post body chunks; we look up post slug +
        title from the ``posts`` table using the source_id metadata.
        """
        from llama_index.core.schema import QueryBundle

        from services.rag_engine import get_rag_retriever

        retriever = await get_rag_retriever(
            self.pool,
            site_config=self._site_config,
            top_k=5,
            source_filter=["posts"],
        )
        nodes = await retriever._aretrieve(QueryBundle(query_str=topic))
        if not nodes:
            return []

        post_ids = [
            n.node.metadata.get("source_id") for n in nodes
            if n.node.metadata.get("source_id")
        ]
        if not post_ids:
            return []

        # Hydrate slug + title from the posts table. Filter to
        # ``status='published'`` so the research context never points
        # the writer at draft / dry_run / archived URLs.
        rows = await self.pool.fetch(
            "SELECT id, title, slug FROM posts "
            "WHERE id::text = ANY($1) AND status = 'published'",
            post_ids,
        )
        # Preserve the retriever's ranking order — posts table fetch
        # comes back unordered.
        by_id = {str(r["id"]): r for r in rows}
        ordered: list[dict[str, str]] = []
        for n in nodes:
            sid = n.node.metadata.get("source_id")
            if sid and str(sid) in by_id:
                r = by_id[str(sid)]
                ordered.append({"title": r["title"], "slug": r["slug"]})
        return ordered

    async def _web_search(self, topic: str) -> list[dict[str, str]]:
        """Search the web for fresh sources (free — DuckDuckGo, no API key)."""
        try:
            from services.web_research import WebResearcher
            researcher = WebResearcher(site_config=self._site_config)
            results = await researcher.search_simple(topic, num_results=5)
            return results
        except Exception as e:
            logger.debug("[RESEARCH] Web search failed: %s", e)
            return []
