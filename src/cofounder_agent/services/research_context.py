"""Build RAG (retrieval-augmented generation) context for a new draft.

Lifted from content_router_service.py during Phase E2. Searches the
pgvector embedding store for already-published posts similar to the
given topic, then formats them as a reference block the writer can
consult for internal linking + angle diversity.

Migrated 2026-04-11 from direct ``database_service.embeddings.search_similar``
calls to ``poindexter.memory.MemoryClient.find_similar_posts`` per Gitea #192
slice 3. The MemoryClient helper hardcodes ``source_table='posts'`` so the
singular/plural silent-zero-result bug can never recur here.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def build_rag_context(
    database_service: Any | None,
    topic: str,
    source_tags: list[str] | None = None,
    source_category: str | None = None,
    current_post_id: str | None = None,
) -> str | None:
    """Search pgvector for similar published posts. Returns None if unavailable.

    GH-88: when ``source_tags``/``source_category`` are provided, the
    candidates go through :class:`InternalLinkCoherenceFilter` before
    being rendered — rejects off-topic suggestions (e.g. CadQuery pinned
    as "related" to an asyncio post) and caps how many times any single
    target can be recommended across the corpus.

    poindexter#470: every candidate is re-checked against
    ``posts.status = 'published'`` during slug resolution. The pgvector
    embeddings table happily indexes drafts / rejected / archived posts,
    so a similarity hit alone is not proof the target post is live —
    without this filter, draft links could point at slugs that 404 on
    gladlabs.io once the draft itself ships.
    """
    if not topic or not topic.strip():
        return None

    try:
        from poindexter.memory import MemoryClient

        async with MemoryClient() as mem:
            # Over-fetch so the coherence filter has room to drop off-topic
            # candidates and still return enough relevant ones.
            fetch_limit = 15 if source_tags or source_category else 5
            similar_posts = await mem.find_similar_posts(
                topic, limit=fetch_limit, min_similarity=0.3,
            )

        if not similar_posts:
            return None

        pool = getattr(database_service, "pool", None) if database_service else None

        # Resolve slug + excerpt for every hit up front; we need slug for
        # the coherence filter's target-tag lookup anyway.
        #
        # poindexter#470: filter by ``status = 'published'`` here so a
        # rejected / awaiting_approval / archived target never reaches the
        # writer prompt. Self-link suppression via ``current_post_id`` is
        # the natural sibling — a post should not link to itself. If a hit
        # doesn't survive the filter the row simply isn't returned and the
        # candidate is dropped (we log the count below — no silent default).
        resolved: list[dict[str, Any]] = []
        dropped_non_published = 0
        dropped_self = 0
        normalized_current_post_id = (
            current_post_id.removeprefix("post/") if current_post_id else None
        )
        for hit in similar_posts:
            post_id = hit.source_id
            similarity = hit.similarity
            title = (hit.metadata or {}).get("title", "Untitled")
            lookup_id = post_id.removeprefix("post/")

            # Self-link suppression — short-circuit before the DB hit.
            if (
                normalized_current_post_id
                and lookup_id == normalized_current_post_id
            ):
                dropped_self += 1
                continue

            slug = ""
            excerpt = ""
            if pool:
                try:
                    row = await pool.fetchrow(
                        """
                        SELECT slug, excerpt
                        FROM posts
                        WHERE id::text = $1
                          AND status = 'published'
                        LIMIT 1
                        """,
                        lookup_id,
                    )
                    if row:
                        slug = row.get("slug") or ""
                        excerpt = row.get("excerpt") or ""
                    else:
                        # Either the post doesn't exist or it isn't
                        # published. Either way, it's not a valid link
                        # target.
                        dropped_non_published += 1
                        continue
                except Exception:
                    # DB error fetching the row — be defensive and skip
                    # this candidate rather than emitting an unverified
                    # link to the writer.
                    continue
            resolved.append({
                "post_id": post_id,
                "similarity": similarity,
                "title": title,
                "slug": slug,
                "excerpt": excerpt,
            })

        if dropped_non_published or dropped_self:
            # No silent defaults — surface the filter activity so we can
            # spot regressions in the embeddings table (e.g. drafts being
            # auto-embedded) and tune accordingly.
            logger.info(
                "[RAG_CONTEXT] dropped %d non-published / %d self-link "
                "candidate(s) from %d hits",
                dropped_non_published,
                dropped_self,
                len(similar_posts),
            )

        # GH-88: apply the coherence filter if we have source context + a DB.
        # source_category is accepted here for call-site symmetry with
        # upstream code, but the filter itself is tag-driven — it is not
        # forwarded to InternalLinkCoherenceFilter.
        if pool and source_tags:
            try:
                from services.internal_link_coherence import (
                    InternalLinkCoherenceFilter,
                    LinkCandidate,
                )
                candidates = [
                    LinkCandidate(
                        slug=r["slug"],
                        title=r["title"],
                        post_id=r["post_id"],
                        similarity=r["similarity"],
                    )
                    for r in resolved
                    if r["slug"]
                ]
                filt = InternalLinkCoherenceFilter(pool=pool)
                kept = await filt.filter_candidates(
                    source_tags=list(source_tags or []),
                    candidates=candidates,
                    current_post_id=current_post_id,
                )
                kept_slugs = {c.slug for c in kept}
                resolved = [r for r in resolved if r["slug"] in kept_slugs]
            except Exception:
                # Escalated from DEBUG — this path was silently failing on
                # every call due to signature mismatches; keep it loud.
                logger.exception("Internal-link coherence filter failed")

        # Trim to 5 after filtering (was the legacy cap).
        resolved = resolved[:5]
        if not resolved:
            return None

        lines: list[str] = [
            "RELATED POSTS WE'VE PUBLISHED "
            "(reference for internal linking, avoid repeating same angles):",
        ]
        for i, r in enumerate(resolved, 1):
            excerpt_short = (
                (r["excerpt"][:120] + "...")
                if len(r["excerpt"]) > 120
                else r["excerpt"]
            )
            url = f"/posts/{r['slug']}" if r["slug"] else f"(post id: {r['post_id']})"
            lines.append(
                f"{i}. [{r['title']}] -- {excerpt_short} ({url}) "
                f"[similarity: {r['similarity']:.2f}]"
            )

        return "\n".join(lines)

    except Exception as e:
        logger.debug("RAG context build failed (non-fatal): %s", e)
        return None
