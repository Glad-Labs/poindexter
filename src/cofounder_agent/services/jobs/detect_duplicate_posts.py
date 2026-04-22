"""DetectDuplicatePostsJob — flag post pairs with near-identical titles.

Replaces ``IdleWorker._detect_duplicate_posts``. Runs every 24 hours
by default — this is an audit job, not a real-time check.

Uses a simple word-overlap heuristic:
  overlap_ratio = |A ∩ B| / max(|A|, |B|)

Titles with fewer than 4 content words are skipped (too short for the
ratio to be meaningful — "AI News" vs "AI Update" would false-positive
under any threshold). Pairs exceeding ``overlap_threshold`` (default
0.7) land in a deduplicated Gitea issue for manual triage.

O(n²) over all published posts, but published count is small enough
(~hundreds) that the cost is negligible at the 24h cadence.

Config (``plugin.job.detect_duplicate_posts``):
- ``config.overlap_threshold`` (default 0.7)
- ``config.min_words`` (default 4)
- ``config.max_pairs_reported`` (default 10) — cap for the Gitea body
- ``config.file_gitea_issue`` (default true)
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.gitea_issues import create_gitea_issue

logger = logging.getLogger(__name__)


class DetectDuplicatePostsJob:
    name = "detect_duplicate_posts"
    description = "Flag pairs of published posts with near-identical titles (word-overlap heuristic)"
    schedule = "every 24 hours"
    idempotent = True  # Read-only — no writes

    async def run(
        self, pool: Any, config: dict[str, Any], *, site_config: Any = None,
    ) -> JobResult:
        overlap_threshold = float(config.get("overlap_threshold", 0.7))
        min_words = int(config.get("min_words", 4))
        max_pairs = int(config.get("max_pairs_reported", 10))
        file_issue = bool(config.get("file_gitea_issue", True))

        if not 0.0 < overlap_threshold <= 1.0:
            return JobResult(
                ok=False,
                detail=f"overlap_threshold must be in (0, 1], got {overlap_threshold}",
                changes_made=0,
            )

        try:
            async with pool.acquire() as conn:
                posts = await conn.fetch(
                    "SELECT id, title FROM posts "
                    "WHERE status = 'published' ORDER BY title",
                )
        except Exception as e:
            logger.exception("DetectDuplicatePostsJob: query failed: %s", e)
            return JobResult(ok=False, detail=f"query failed: {e}", changes_made=0)

        if len(posts) < 2:
            return JobResult(
                ok=True,
                detail=f"need ≥2 published posts, have {len(posts)}",
                changes_made=0,
            )

        duplicates: list[tuple[str, str]] = []
        # Precompute lowercased word sets once per post — avoids O(n²) re-parsing.
        title_sets = [
            {"id": p["id"], "title": p["title"], "words": set((p["title"] or "").lower().split())}
            for p in posts
        ]

        for i in range(len(title_sets)):
            a = title_sets[i]
            if len(a["words"]) < min_words:
                continue
            for j in range(i + 1, len(title_sets)):
                b = title_sets[j]
                if len(b["words"]) < min_words:
                    continue
                overlap = len(a["words"] & b["words"]) / max(
                    len(a["words"]), len(b["words"])
                )
                if overlap > overlap_threshold:
                    duplicates.append(
                        ((a["title"] or "")[:50], (b["title"] or "")[:50]),
                    )

        if duplicates and file_issue:
            body = "## Potential Duplicate Posts\n\n" + "\n".join(
                f'- "{a}" vs "{b}"' for a, b in duplicates[:max_pairs]
            )
            # Phase H (GH#95): transitional singleton import — this Job's
            # run() doesn't thread site_config yet.
            await create_gitea_issue(
                f"content: {len(duplicates)} potential duplicate post pairs",
                body,
                site_config=site_config,
            )

        detail = (
            f"scanned {len(posts)} posts, found {len(duplicates)} pair(s) "
            f"over {overlap_threshold} overlap"
        )
        logger.info("DetectDuplicatePostsJob: %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=len(duplicates),
            metrics={
                "posts_scanned": len(posts),
                "duplicate_pairs": len(duplicates),
            },
        )
