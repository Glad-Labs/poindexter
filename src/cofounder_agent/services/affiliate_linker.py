"""
Affiliate Linker — auto-inject affiliate links into published content.

Scans post content for mentions of specific tools/services and replaces
plain text mentions with affiliate links. Revenue on autopilot.

Links are configurable via app_settings (category: affiliates).

Usage:
    from services.affiliate_linker import add_affiliate_links
    content = add_affiliate_links(content)
"""

import logging
import re
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# Default affiliate links — override via app_settings
# Format: keyword → (url, display_text)
# These are placeholder URLs — replace with actual affiliate links
DEFAULT_AFFILIATES: Dict[str, Tuple[str, str]] = {
    # Amazon Associates (gladlabsllc-20)
    "Python": ("https://www.amazon.com/Learning-Python-5th-Mark-Lutz/dp/1449355730?tag=gladlabsllc-20", "Python"),
    "Raspberry Pi": ("https://www.amazon.com/s?k=raspberry+pi&tag=gladlabsllc-20", "Raspberry Pi"),
    "NAS": ("https://www.amazon.com/s?k=nas+storage&tag=gladlabsllc-20", "NAS"),
    "GPU": ("https://www.amazon.com/s?k=nvidia+gpu&tag=gladlabsllc-20", "GPU"),
    "SSD": ("https://www.amazon.com/s?k=nvme+ssd&tag=gladlabsllc-20", "SSD"),
    "mechanical keyboard": ("https://www.amazon.com/s?k=mechanical+keyboard&tag=gladlabsllc-20", "mechanical keyboard"),
    # Service links (no referral codes yet — add real ones when available)
    "Railway": ("https://railway.app", "Railway"),
    "Vercel": ("https://vercel.com", "Vercel"),
    "Ollama": ("https://ollama.com", "Ollama"),
    "Grafana": ("https://grafana.com/products/cloud/", "Grafana Cloud"),
    # Tools we write about
    "PostgreSQL": ("https://www.postgresql.org", "PostgreSQL"),
    "FastAPI": ("https://fastapi.tiangolo.com", "FastAPI"),
    "Next.js": ("https://nextjs.org", "Next.js"),
    "Docker": ("https://www.docker.com", "Docker"),
    "GitHub": ("https://github.com", "GitHub"),
    "Anthropic": ("https://www.anthropic.com", "Anthropic"),
    "Claude": ("https://claude.ai", "Claude"),
}


def add_affiliate_links(
    content: str,
    affiliates: Dict[str, Tuple[str, str]] | None = None,
    max_links_per_keyword: int = 1,
) -> str:
    """Add affiliate links to content.

    Only links the FIRST mention of each keyword to avoid over-linking.
    Skips keywords that are already inside <a> tags.

    Args:
        content: HTML content to process
        affiliates: Optional override for affiliate mappings
        max_links_per_keyword: Max times to link each keyword (default 1)

    Returns:
        Content with affiliate links injected
    """
    if not content:
        return content

    links = affiliates if affiliates is not None else DEFAULT_AFFILIATES
    linked_count = 0

    for keyword, (url, display) in links.items():
        # Skip if keyword is already linked (inside an <a> tag)
        if re.search(rf'<a[^>]*>[^<]*{re.escape(keyword)}[^<]*</a>', content, re.IGNORECASE):
            continue

        # Only replace first occurrence, and only if not inside an HTML tag
        pattern = rf'(?<![<"\'/a-zA-Z])({re.escape(keyword)})(?![a-zA-Z"\'>])'
        match = re.search(pattern, content)
        if match:
            replacement = (
                f'<a href="{url}" target="_blank" rel="noopener sponsored" '
                f'style="color: #22d3ee; text-decoration: underline;">{match.group(1)}</a>'
            )
            content = content[:match.start()] + replacement + content[match.end():]
            linked_count += 1

    if linked_count > 0:
        logger.info("[AFFILIATE] Injected %d affiliate links", linked_count)

    return content


async def load_affiliates_from_db(pool) -> Dict[str, Tuple[str, str]]:
    """Load active affiliate links from the database.

    Falls back to DEFAULT_AFFILIATES if DB is unavailable.
    Manage via: INSERT/UPDATE/DELETE on affiliate_links table,
    or via the settings API.
    """
    try:
        rows = await pool.fetch(
            "SELECT keyword, url, display_text FROM affiliate_links WHERE is_active = true"
        )
        if rows:
            affiliates = {
                row["keyword"]: (row["url"], row["display_text"] or row["keyword"])
                for row in rows
            }
            logger.info("[AFFILIATE] Loaded %d affiliates from DB", len(affiliates))
            return affiliates
    except Exception as e:
        logger.debug("[AFFILIATE] DB not available, using defaults: %s", e)
    return DEFAULT_AFFILIATES


async def add_affiliates_to_all_posts(pool) -> int:
    """Retroactively add affiliate links to all published posts.

    Loads affiliate mappings from the affiliate_links DB table.
    """
    affiliates = await load_affiliates_from_db(pool)

    try:
        rows = await pool.fetch(
            "SELECT id, content, slug FROM posts WHERE status = 'published'"
        )
    except Exception as e:
        logger.error("[AFFILIATE] Failed to fetch posts: %s", e)
        return 0

    updated = 0
    for row in rows:
        content = row["content"]
        if not content:
            continue

        # Skip if already has affiliate links
        if 'rel="noopener sponsored"' in content:
            continue

        new_content = add_affiliate_links(content, affiliates=affiliates)
        if new_content != content:
            try:
                await pool.execute(
                    "UPDATE posts SET content = $1 WHERE id = $2",
                    new_content, row["id"],
                )
                updated += 1
            except Exception as e:
                logger.warning("[AFFILIATE] Failed to update %s: %s", row["slug"][:20], e)

    logger.info("[AFFILIATE] Updated %d/%d posts with affiliate links", updated, len(rows))
    return updated
