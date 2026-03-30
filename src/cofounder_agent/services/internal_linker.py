"""
Internal Linker — automatically adds links between related posts.

Scans post content for topic keywords that match other published posts,
and injects HTML links. Improves SEO and keeps readers on the site.

Usage:
    from services.internal_linker import add_internal_links
    content = await add_internal_links(pool, content, current_post_slug)
"""

import logging
import re
from typing import List, Tuple

logger = logging.getLogger(__name__)


async def get_published_posts(pool) -> List[dict]:
    """Get all published posts with title and slug."""
    try:
        rows = await pool.fetch(
            "SELECT title, slug FROM posts WHERE status = 'published' ORDER BY published_at DESC"
        )
        return [{"title": r["title"], "slug": r["slug"]} for r in rows]
    except Exception as e:
        logger.warning("[LINKER] Failed to fetch posts: %s", e)
        return []


def _extract_keywords(title: str) -> List[str]:
    """Extract meaningful keywords from a post title."""
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
        "and", "or", "but", "not", "no", "so", "if", "then", "than",
        "how", "why", "what", "when", "where", "who", "which",
        "your", "you", "we", "our", "its", "it", "this", "that",
        "into", "about", "between", "through", "during", "before", "after",
        "every", "all", "most", "some", "any", "few", "more", "less",
        "just", "only", "very", "really", "also", "still", "already",
        "new", "old", "big", "small", "long", "short", "good", "bad",
    }
    words = re.findall(r'\b[a-zA-Z]{3,}\b', title.lower())
    return [w for w in words if w not in stop_words]


def _find_link_opportunities(
    content: str, posts: List[dict], current_slug: str, max_links: int = 5
) -> List[Tuple[str, str, str]]:
    """Find places in content where we can link to other posts.

    Returns list of (matched_text, slug, title) tuples.
    """
    opportunities = []
    used_slugs = set()

    for post in posts:
        if post["slug"] == current_slug:
            continue
        if post["slug"] in used_slugs:
            continue

        keywords = _extract_keywords(post["title"])
        if len(keywords) < 2:
            continue

        # Look for 2+ keyword matches in the content
        for i in range(len(keywords)):
            for j in range(i + 1, min(i + 3, len(keywords))):
                # Build a pattern that matches both keywords near each other
                kw1, kw2 = keywords[i], keywords[j]
                # Look for a sentence containing both keywords
                pattern = rf'\b({kw1})\b[^.!?]{{0,100}}\b({kw2})\b'
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    # Get the full sentence
                    start = content.rfind('.', 0, match.start()) + 1
                    end = content.find('.', match.end())
                    if end == -1:
                        end = min(match.end() + 50, len(content))
                    sentence = content[start:end].strip()

                    if len(sentence) > 20 and post["slug"] not in used_slugs:
                        opportunities.append((sentence, post["slug"], post["title"]))
                        used_slugs.add(post["slug"])
                        break
            if post["slug"] in used_slugs:
                break

        if len(opportunities) >= max_links:
            break

    return opportunities


async def add_internal_links(
    pool, content: str, current_slug: str, max_links: int = 5
) -> str:
    """Add internal links to related posts within content.

    Finds sentences that match keywords from other published posts
    and appends a "Related Reading" section at the end.
    """
    posts = await get_published_posts(pool)
    if not posts:
        return content

    opportunities = _find_link_opportunities(content, posts, current_slug, max_links)
    if not opportunities:
        return content

    # Build a "Related Reading" section
    related_html = '\n<div class="related-posts" style="margin-top: 2em; padding: 1.5em; border-top: 1px solid #334155;">'
    related_html += '\n<h3 style="color: #94a3b8; margin-bottom: 1em;">Related Reading</h3>'
    related_html += '\n<ul style="list-style: none; padding: 0;">'

    for _, slug, title in opportunities[:max_links]:
        related_html += f'\n<li style="margin-bottom: 0.5em;">→ <a href="/posts/{slug}" style="color: #22d3ee; text-decoration: none;">{title}</a></li>'

    related_html += '\n</ul>\n</div>'

    logger.info("[LINKER] Added %d internal links to post %s", len(opportunities), current_slug[:30])
    return content + related_html


async def add_links_to_all_posts(pool) -> int:
    """Retroactively add internal links to all published posts that don't have them."""
    posts = await get_published_posts(pool)
    updated = 0

    for post in posts:
        try:
            row = await pool.fetchrow(
                "SELECT content FROM posts WHERE slug = $1", post["slug"]
            )
            if not row or not row["content"]:
                continue

            content = row["content"]
            if "related-posts" in content:
                continue  # Already has links

            new_content = await add_internal_links(pool, content, post["slug"])
            if new_content != content:
                await pool.execute(
                    "UPDATE posts SET content = $1 WHERE slug = $2",
                    new_content, post["slug"]
                )
                updated += 1
        except Exception as e:
            logger.warning("[LINKER] Failed to update %s: %s", post["slug"][:30], e)

    logger.info("[LINKER] Added internal links to %d/%d posts", updated, len(posts))
    return updated
