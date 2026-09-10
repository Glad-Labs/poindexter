"""Critic-judge golden-set bootstrap (judge calibration, poindexter#985).

Known-good cases are recent PUBLISHED posts — content that cleared QA and
operator sign-off, so a competent judge must approve it. Known-bad cases are
**deterministic corruptions of those same posts**, one per rubric reject
class the judge is contractually required to catch:

- ``truncated``: the body cut mid-word at ~55% — the token-limit failure
  shape (poindexter#984).
- ``scaffold``: a planning-dump preamble prepended — the leaked-outline
  failure shape (the 2026-07-01 incident dialect).
- ``deliberation``: a reviewer-role brief plus first-person reasoning
  prepended, with the article fused onto the last bullet — the
  writer_self_review dump shape (poindexter#1000). Added because the
  2026-08-06 calibration scored a judge 0.91 that then approved exactly
  this shape at 95/100: a golden set only measures the failure modes it
  actually contains, so every escaped shape earns a case here.

Corrupting real posts (instead of trusting historical reject labels) gives
trustworthy ground truth: historical labels were produced by the very judges
being calibrated. The production corpus IS the test set — no hand-labeling,
no dummy data (mirrors the reranker golden set's design).

The set is versioned by a stable hash of the source post-id set, so the same
posts always yield the same cases while newly published posts roll the
version forward.
"""

from __future__ import annotations

import hashlib
from typing import Any

from services.model_eval.types import GoldenCase, GoldenSet

_GOLDEN_NAME = "model_eval_critic"
# A post must be long enough that a 55% cut is unambiguously mid-article.
_MIN_CONTENT_LEN = 1500

# Verbatim planning-dump dialect from the 2026-07-01 incident family — the
# shape the qa.review rubric's unfinished-content rule names explicitly.
_SCAFFOLD_PREAMBLE = (
    "*   Topic: {title}\n"
    "*   Source Material provided: none\n"
    "*   Length: Approximately 800 words.\n"
    "*   I should provide a clear intro and vary sentence length.\n"
    "*   Check word count before finishing.\n\n"
)

# Verbatim reviewer-role deliberation dialect from prod task 1bdf0360
# (2026-08-07, poindexter#1000). Note the final line deliberately fuses the
# narration onto the article with no blank line — that is how the real leak
# arrived, and a judge that tolerates it is the judge that let it publish.
_DELIBERATION_PREAMBLE = (
    "*   Role: Reviewer checking for internal contradictions.\n"
    '    *   Input: A draft titled "{title}".\n'
    "    *   Task: Fix specific contradictions and *nothing else*.\n"
    "    *   Output Format: Revised draft only. Keep structure, length, "
    "tone identical. Preserve image markers.\n"
    "    *   Constraint 1: Fix *only* the identified contradictions.\n"
    "    *   Constraint 2: Output only the revised draft.\n\n"
    "    Wait, let me re-read carefully. The prompt says to fix "
    "contradictions, but the analysis concludes with PASS.\n"
    "    Let's double-check the sections one more time.\n"
    "    If the user's own analysis says PASS, then there is nothing to "
    "fix here.\n"
    "    Is there any other possible interpretation? I don't think so.\n"
    "    One last look at the text before I answer.\n"
    "    I'll provide the original text."
)

# Characters that read as a finished ending — stripped from the truncation
# corruption's tail so the cut is guaranteed mid-thought. Mirrors (inverted)
# the terminal set in ``content_validator.detect_truncated_content``.
_TERMINALISH = set(".!?…\"'”’»)]}`|~>*_\n \t-#")


def _truncate_mid_word(content: str) -> str:
    """Deterministically cut the body at ~55%, landing mid-word."""
    cut = content[: max(int(len(content) * 0.55), _MIN_CONTENT_LEN // 2)]
    cut = cut.rstrip()
    while cut and cut[-1] in _TERMINALISH:
        cut = cut[:-1].rstrip()
    return cut


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
    raw = ",".join(sorted(post_ids)).encode("utf-8")
    return int.from_bytes(hashlib.blake2b(raw, digest_size=4).digest(), "big")


async def build_critic_golden_set(*, pool: Any, site_config: Any) -> GoldenSet:
    """Build the critic golden set: N good posts + 2 corruptions each.

    Fails loud when the corpus can't fill the requested set — a silently
    smaller eval would flatter a bad judge.
    """
    good_n = int(site_config.get("model_eval_critic_good_posts", "8"))
    posts = await _fetch_recent_published(pool, limit=good_n)
    if len(posts) < good_n:
        raise RuntimeError(
            f"critic golden set needs {good_n} published posts >= "
            f"{_MIN_CONTENT_LEN} chars (model_eval_critic_good_posts); "
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
                "content": content,
                "expected": "approve",
                "kind": "good",
                "post_id": post["id"],
            },
        ))
        cases.append(GoldenCase(
            query=title,
            candidates=[],
            payload={
                "title": title,
                "topic": title,
                "content": _truncate_mid_word(content),
                "expected": "veto",
                "kind": "truncated",
                "post_id": post["id"],
            },
        ))
        cases.append(GoldenCase(
            query=title,
            candidates=[],
            payload={
                "title": title,
                "topic": title,
                "content": _SCAFFOLD_PREAMBLE.format(title=title) + content,
                "expected": "veto",
                "kind": "scaffold",
                "post_id": post["id"],
            },
        ))
        cases.append(GoldenCase(
            query=title,
            candidates=[],
            payload={
                "title": title,
                "topic": title,
                # Fused with no separator — the 1bdf0360 shape exactly.
                "content": _DELIBERATION_PREAMBLE.format(title=title) + content,
                "expected": "veto",
                "kind": "deliberation",
                "post_id": post["id"],
            },
        ))

    return GoldenSet(
        name=_GOLDEN_NAME,
        version=_version_of([p["id"] for p in posts]),
        cases=cases,
    )
