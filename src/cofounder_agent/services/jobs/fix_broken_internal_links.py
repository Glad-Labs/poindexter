"""FixBrokenInternalLinksJob — strip ``/posts/<slug>`` links to non-published posts.

Replaces ``IdleWorker._fix_broken_internal_links``. Runs every 24h by
default. Pulls the set of currently-published slugs, scans every
published post's content for ``/posts/<slug>`` references, and removes
anchors / markdown links / sidebar list items pointing at slugs that
are no longer in the published set (drafted, rejected, or deleted).

Keeps the link *text* in place for markdown/anchor forms so the reader
doesn't get a broken dangling reference; the list-item form is stripped
wholesale because it's usually a "Related Posts" bullet that stops
making sense once the target is gone.

Writes back to ``posts.content`` + updates ``updated_at`` — this is a
content-modifying job (one of the few), so ``idempotent`` stays True
(re-running is safe: the same stale links will no-op on the second
pass).

Config (``plugin.job.fix_broken_internal_links``):
- ``config.file_gitea_issue`` (default true) — file a dedup'd issue
  when any posts were rewritten.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from plugins.job import JobResult
from utils.gitea_issues import create_gitea_issue

logger = logging.getLogger(__name__)

_LINK_SLUG_RE = re.compile(r"/posts/([a-z0-9-]+)")


def _strip_slug_references(content: str, slug: str) -> str:
    """Remove all three link forms pointing at ``/posts/<slug>``.

    Order matters — sidebar ``<li>`` removal runs FIRST, because the
    ``<a>`` anchor inside it must still be present for the sidebar
    regex to match. Running anchor-rewrite first (as the legacy
    idle_worker code did) leaves orphaned empty ``<li>`` wrappers in
    the rendered page. That's a latent bug the port fixes on the way
    through.
    """
    slug_esc = re.escape(slug)
    # Sidebar <li> entry: whole item drops out (must run before anchor).
    content = re.sub(
        r"<li[^>]*>.*?/posts/" + slug_esc + r"[^<]*</a></li>",
        "",
        content,
    )
    # Markdown link: "[anchor text](/posts/slug)" → "anchor text"
    content = re.sub(
        r"\[([^\]]+)\]\(/posts/" + slug_esc + r"\)",
        r"\1",
        content,
    )
    # HTML anchor: <a href="/posts/slug">label</a> → label
    content = re.sub(
        r"<a[^>]*href=\"/posts/" + slug_esc + r"\"[^>]*>([^<]*)</a>",
        r"\1",
        content,
    )
    return content


class FixBrokenInternalLinksJob:
    name = "fix_broken_internal_links"
    description = "Strip internal links pointing at unpublished/deleted posts"
    schedule = "every 24 hours"
    idempotent = True  # Re-running is a no-op once stale links are gone

    async def run(
        self, pool: Any, config: dict[str, Any], *, site_config: Any = None,
    ) -> JobResult:
        file_issue = bool(config.get("file_gitea_issue", True))

        try:
            async with pool.acquire() as conn:
                pub_rows = await conn.fetch(
                    "SELECT slug FROM posts WHERE status = 'published'"
                )
                published_slugs = {r["slug"] for r in pub_rows if r["slug"]}

                candidates = await conn.fetch(
                    "SELECT id, title, content FROM posts "
                    "WHERE status = 'published' AND content LIKE '%/posts/%'"
                )
        except Exception as e:
            logger.exception("FixBrokenInternalLinksJob: fetch failed: %s", e)
            return JobResult(ok=False, detail=f"fetch failed: {e}", changes_made=0)

        if not candidates:
            return JobResult(
                ok=True,
                detail="no posts contain /posts/ links",
                changes_made=0,
            )

        fixed = 0
        for row in candidates:
            content = row["content"] or ""
            new_content = content
            linked_slugs = set(_LINK_SLUG_RE.findall(content))
            stale = linked_slugs - published_slugs
            if not stale:
                continue
            for slug in stale:
                new_content = _strip_slug_references(new_content, slug)
            if new_content != content:
                try:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE posts SET content = $1, updated_at = NOW() "
                            "WHERE id = $2",
                            new_content, row["id"],
                        )
                    fixed += 1
                except Exception as e:
                    logger.warning(
                        "FixBrokenInternalLinksJob: update failed for %s: %s",
                        row.get("id"), e,
                    )

        if fixed and file_issue:
            # Phase H (GH#95): transitional singleton import — this Job's
            # run() doesn't thread site_config yet.
            await create_gitea_issue(
                f"links: removed broken internal links from {fixed} posts",
                "Auto-cleaned links to unpublished/deleted posts. "
                "Anchor text preserved; sidebar list items removed wholesale.",
                site_config=site_config,
            )

        detail = f"scanned {len(candidates)} post(s), rewrote {fixed}"
        logger.info("FixBrokenInternalLinksJob: %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=fixed,
            metrics={
                "posts_scanned": len(candidates),
                "posts_rewritten": fixed,
            },
        )
