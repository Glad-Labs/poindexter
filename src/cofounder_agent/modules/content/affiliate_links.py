"""Affiliate-link service — the pure keyword matcher + DB CRUD + click rollup.

Rebuild of the removed services/affiliate_linker.py, adapted to markdown (the
pipeline content is markdown at injection time) and to /go/<code> redirect URLs
resolved by the affiliate-redirect Worker. Real referral rows live in the DB
only (never source) — see docs/operations/affiliate-links.md.
"""
from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^\s*```")


@dataclass
class AffiliateLink:
    code: str
    keywords: list[str] = field(default_factory=list)
    url: str = ""
    display_text: str = ""
    platform: str = ""


def _inside_inline_code(line: str, idx: int) -> bool:
    """True when position ``idx`` falls inside a `...` inline-code span."""
    return line.count("`", 0, idx) % 2 == 1


def _find_eligible_span(line: str, keyword: str) -> tuple[int, int] | None:
    """First eligible whole-word occurrence of ``keyword`` in ``line``.

    Eligible = not preceded/followed by a word char, ``/``, ``[`` or ``]``
    (so it won't fire inside an existing ``[kw](url)`` or a ``/go/kw`` path)
    and not inside an inline-code span.
    """
    pattern = re.compile(r"(?<![\w`/\[\]])(" + re.escape(keyword) + r")(?![\w`\]])")
    for m in pattern.finditer(line):
        if _inside_inline_code(line, m.start()):
            continue
        return m.start(), m.end()
    return None


def _resolve_link(
    candidates: list[AffiliateLink], keyword: str, markdown: str,
    last_used: dict[str, str], rng: Any,
) -> AffiliateLink:
    """Pick which link wins a keyword shared by 2+ active links.

    Tier 1 (co-occurrence): does exactly one candidate have another of ITS
    OWN keywords also present elsewhere in the post? Tier 2 (LRU fallback):
    least-recently-injected into a published post, ties broken randomly.
    Mirrors modules/content/stages/source_featured_image.py's
    _select_style_lru.
    """
    if len(candidates) == 1:
        return candidates[0]
    corroborated = [
        c for c in candidates
        if any(kw in markdown for kw in c.keywords if kw != keyword)
    ]
    if len(corroborated) == 1:
        return corroborated[0]
    oldest = min(last_used.get(c.code, "") for c in candidates)
    tied = [c for c in candidates if last_used.get(c.code, "") == oldest]
    return rng.choice(tied)


def inject_affiliate_links(
    markdown: str,
    links: list[AffiliateLink],
    *,
    base: str = "/go",
    cap: int = 3,
    last_used: dict[str, str] | None = None,
    rng: Any = random,
) -> tuple[str, list[str]]:
    """Inject up to ``cap`` affiliate links, first-mention only per link.

    Keywords are matched across the WHOLE active-link catalog (not per-link)
    and tried longest-first, so a more specific phrase wins over a shorter
    one that would otherwise match the same text. When a matched keyword is
    shared by 2+ links, ``_resolve_link`` picks the winner. Skips fenced code
    blocks, heading lines, inline code, and existing links. Deterministic
    given a fixed ``last_used``/``rng`` (idempotent: a re-run finds the
    keyword already inside a link and skips it).
    """
    if not markdown or not links or cap <= 0:
        return markdown, []
    last_used = last_used or {}

    keyword_to_links: dict[str, list[AffiliateLink]] = {}
    for link in links:
        for kw in link.keywords:
            keyword_to_links.setdefault(kw, []).append(link)
    ordered_keywords = sorted(keyword_to_links, key=len, reverse=True)

    used: set[str] = set()
    injected: list[str] = []
    consumed_keywords: set[str] = set()
    out_lines: list[str] = []
    in_fence = False
    for line in markdown.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence or line.lstrip().startswith("#") or len(injected) >= cap:
            out_lines.append(line)
            continue
        for kw in ordered_keywords:
            if kw in consumed_keywords or len(injected) >= cap:
                continue
            candidates = [l for l in keyword_to_links[kw] if l.code not in used]
            if not candidates:
                continue
            span = _find_eligible_span(line, kw)
            if span is None:
                continue
            link = _resolve_link(candidates, kw, markdown, last_used, rng)
            start, end = span
            text = link.display_text or kw
            url = f"{base.rstrip('/')}/{link.code}"
            line = line[:start] + f"[{text}]({url})" + line[end:]
            used.add(link.code)
            injected.append(link.code)
            consumed_keywords.add(kw)
        out_lines.append(line)
    return "\n".join(out_lines), injected


async def load_link_last_used(pool: Any) -> dict[str, str]:
    """Map affiliate code -> ISO timestamp of its most recent PUBLISHED-post
    mention, for LRU tie-breaking when multiple links share a keyword.
    Mirrors _load_style_last_used in
    modules/content/stages/source_featured_image.py (scans existing posts,
    no new tracking column)."""
    rows = await pool.fetch(
        "SELECT al.code, MAX(p.published_at) AS last_used "
        "FROM affiliate_links al "
        "LEFT JOIN posts p "
        "  ON p.content LIKE '%/go/' || al.code || '%' AND p.status = 'published' "
        "GROUP BY al.code"
    )
    return {r["code"]: (r["last_used"].isoformat() if r["last_used"] else "") for r in rows}


async def _fetch_links(pool: Any, *, active_only: bool) -> list[AffiliateLink]:
    where = "WHERE al.is_active = true " if active_only else ""
    rows = await pool.fetch(
        "SELECT al.code, al.url, COALESCE(al.display_text, '') AS display_text, "
        "al.platform, "
        "COALESCE(array_agg(alk.keyword ORDER BY alk.id) "
        "  FILTER (WHERE alk.keyword IS NOT NULL), '{}') AS keywords "
        "FROM affiliate_links al "
        "LEFT JOIN affiliate_link_keywords alk ON alk.link_id = al.id "
        + where + "GROUP BY al.id ORDER BY al.id"
    )
    return [
        AffiliateLink(code=r["code"], url=r["url"], display_text=r["display_text"],
                      platform=r["platform"], keywords=list(r["keywords"]))
        for r in rows
    ]


async def list_active(pool: Any) -> list[AffiliateLink]:
    return await _fetch_links(pool, active_only=True)


async def list_all(pool: Any) -> list[AffiliateLink]:
    return await _fetch_links(pool, active_only=False)


async def add_link(
    pool: Any, *, code: str, keywords: list[str], url: str,
    display_text: str = "", program: str = "", description: str = "",
    category: str = "product", platform: str = "",
) -> None:
    if not keywords:
        raise ValueError("add_link requires at least one keyword")
    deduped = list(dict.fromkeys(keywords))
    async with pool.acquire() as conn, conn.transaction():
        link_id = await conn.fetchval(
            "INSERT INTO affiliate_links "
            "(code, url, display_text, program, description, category, platform) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "ON CONFLICT (code) DO UPDATE SET url = EXCLUDED.url, "
            "display_text = EXCLUDED.display_text, program = EXCLUDED.program, "
            "description = EXCLUDED.description, category = EXCLUDED.category, "
            "platform = EXCLUDED.platform, updated_at = now() RETURNING id",
            code, url, display_text or None, program or None,
            description, category, platform,
        )
        await conn.execute("DELETE FROM affiliate_link_keywords WHERE link_id = $1", link_id)
        for kw in deduped:
            await conn.execute(
                "INSERT INTO affiliate_link_keywords (link_id, keyword) VALUES ($1, $2)",
                link_id, kw,
            )


async def set_active(pool: Any, code: str, active: bool) -> bool:
    tag = await pool.execute(
        "UPDATE affiliate_links SET is_active = $2, updated_at = now() WHERE code = $1",
        code, active,
    )
    return tag.endswith("1")


async def remove_link(pool: Any, code: str) -> bool:
    tag = await pool.execute("DELETE FROM affiliate_links WHERE code = $1", code)
    return tag.endswith("1")


__all__ = [
    "AffiliateLink", "inject_affiliate_links", "list_active", "list_all",
    "add_link", "set_active", "remove_link", "load_link_last_used",
]
