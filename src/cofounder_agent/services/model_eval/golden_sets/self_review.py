"""Self-review detector golden set (poindexter#1031).

``writer_self_review`` exists to catch cross-section contradictions before QA.
Measured 2026-08-28 it caught **1 of 4** blatant injected contradictions — the
stage is close to a no-op — and nothing in production could have told you.

The reason it went unnoticed is structural: a false PASS is indistinguishable
from a genuinely clean draft. The only production signal the stage emits is
``self_review_revision_rejected``, which fires on the *revise* half; a missed
DETECTION emits nothing at all, because silence is both its success state and
its failure state. Confirmed on 5 real published posts with no injected
contradiction: both candidate models returned PASS 5/5, so a PASS tells you
nothing about whether the detector works.

This set makes the detection rate measurable the same way the critic set does
(``golden_sets/critic.py``) — **deterministic corruptions of real published
posts**, so ground truth comes from construction rather than from labels
produced by the very models being evaluated.

Two case classes:

- ``clean``: a real published post, unmodified. A competent detector returns
  PASS. Anything else is a false positive, and a detector that flags
  everything would otherwise score a perfect detection rate.
- ``contradiction``: the same post with a closing section that flatly negates
  its own thesis. This is the exact probe from #1031, and it is deliberately
  unsubtle — an injected contradiction this blatant should be near-100% for
  any competent detector, so a miss is unambiguous evidence rather than a
  judgement call about difficulty.

Both classes are needed. Detection rate alone is gameable by a detector that
always flags; false-positive rate alone is gameable by one that never does.
The scorer reports both and a balanced accuracy over them.
"""

from __future__ import annotations

import hashlib
from typing import Any

from services.model_eval.types import GoldenCase, GoldenSet

_GOLDEN_NAME = "model_eval_self_review"

# Long enough that an appended section is a genuine cross-section
# contradiction rather than most of the document.
_MIN_CONTENT_LEN = 1500

# The injected contradiction. Written to negate the article's own argument
# without naming any specific claim, so one template works across every post
# and the corruption stays deterministic.
#
# Phrased as a closing section with a real H2 so it reads as part of the
# document: a detector should catch it because it CONTRADICTS the body, not
# because it looks structurally out of place.
_CONTRADICTION_SECTION = (
    "\n\n## A Final Word\n\n"
    "Everything above is incorrect. The approach described in this article "
    "does not work in practice and should not be adopted by anyone. The "
    "benefits claimed earlier do not materialise, the measurements cited are "
    "not reproducible, and readers are strongly advised to do the opposite of "
    "what this post recommends. There is no situation in which the technique "
    "discussed here is the right choice.\n"
)


async def _fetch_recent_published(pool: Any, *, limit: int) -> list[dict[str, str]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, content FROM posts "
            "WHERE status = 'published' AND content IS NOT NULL "
            "AND title IS NOT NULL AND length(content) >= $1 "
            "ORDER BY published_at DESC NULLS LAST, id DESC LIMIT $2",
            _MIN_CONTENT_LEN,
            limit,
        )
    return [
        {"id": str(r["id"]), "title": str(r["title"]), "content": str(r["content"])}
        for r in rows
    ]


def _version_of(post_ids: list[str]) -> int:
    """Stable hash of the source post set — same posts, same version."""
    raw = ",".join(sorted(post_ids)).encode("utf-8")
    return int.from_bytes(hashlib.blake2b(raw, digest_size=4).digest(), "big")


def inject_contradiction(content: str) -> str:
    """Append the self-negating closing section. Pure + deterministic."""
    return content.rstrip() + _CONTRADICTION_SECTION


async def build_self_review_golden_set(*, pool: Any, site_config: Any) -> GoldenSet:
    """N real published posts, each contributing one clean and one corrupt case.

    Fails loud when the corpus cannot fill the requested set: a silently
    smaller eval would flatter a weak detector, which is the failure this
    whole set exists to prevent.
    """
    good_n = int(site_config.get("model_eval_self_review_posts", "8"))
    posts = await _fetch_recent_published(pool, limit=good_n)
    if len(posts) < good_n:
        raise RuntimeError(
            f"self-review golden set needs {good_n} published posts >= "
            f"{_MIN_CONTENT_LEN} chars (model_eval_self_review_posts); "
            f"found {len(posts)}."
        )

    cases: list[GoldenCase] = []
    for post in posts:
        title, content = post["title"], post["content"]
        cases.append(GoldenCase(
            query=title,
            candidates=[],
            payload={
                "title": title,
                "topic": title,
                "draft": content,
                "expected": "pass",
                "kind": "clean",
                "post_id": post["id"],
            },
        ))
        cases.append(GoldenCase(
            query=title,
            candidates=[],
            payload={
                "title": title,
                "topic": title,
                "draft": inject_contradiction(content),
                "expected": "detect",
                "kind": "contradiction",
                "post_id": post["id"],
            },
        ))

    return GoldenSet(
        name=_GOLDEN_NAME,
        version=_version_of([p["id"] for p in posts]),
        cases=cases,
    )
