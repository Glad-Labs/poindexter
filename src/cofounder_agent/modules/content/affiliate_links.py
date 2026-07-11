"""Affiliate-link service — the pure keyword matcher + DB CRUD + click rollup.

Rebuild of the removed services/affiliate_linker.py, adapted to markdown (the
pipeline content is markdown at injection time) and to /go/<code> redirect URLs
resolved by the affiliate-redirect Worker. Real referral rows live in the DB
only (never source) — see docs/operations/affiliate-links.md.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^\s*```")


@dataclass
class AffiliateLink:
    code: str
    keyword: str
    url: str
    display_text: str = ""


def _inside_inline_code(line: str, idx: int) -> bool:
    """True when position ``idx`` falls inside a `...` inline-code span."""
    return line.count("`", 0, idx) % 2 == 1


def _link_first_mention(line: str, link: AffiliateLink, base: str) -> tuple[str, int]:
    """Link the first eligible whole-word keyword occurrence in ``line``.

    Eligible = not preceded/followed by a word char, ``/``, ``[`` or ``]`` (so it
    won't fire inside an existing ``[kw](url)`` or a ``/go/kw`` path) and not
    inside an inline-code span. Returns (new_line, n_injected in {0, 1}).
    """
    pattern = re.compile(
        r"(?<![\w`/\[\]])(" + re.escape(link.keyword) + r")(?![\w`\]])"
    )
    for m in pattern.finditer(line):
        if _inside_inline_code(line, m.start()):
            continue
        text = link.display_text or link.keyword
        url = f"{base.rstrip('/')}/{link.code}"
        return line[: m.start()] + f"[{text}]({url})" + line[m.end() :], 1
    return line, 0


def inject_affiliate_links(
    markdown: str,
    links: list[AffiliateLink],
    *,
    base: str = "/go",
    cap: int = 3,
) -> tuple[str, list[str]]:
    """Inject up to ``cap`` affiliate links, one per keyword, first-mention only.

    Skips fenced code blocks, heading lines, inline code, and existing links.
    Deterministic and idempotent (a re-run finds the keyword already inside a
    link and skips it). Returns (new_markdown, injected_codes).
    """
    if not markdown or not links or cap <= 0:
        return markdown, []
    used: set[str] = set()
    injected: list[str] = []
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
        for link in links:
            if link.code in used or len(injected) >= cap:
                continue
            new_line, n = _link_first_mention(line, link, base)
            if n:
                line = new_line
                used.add(link.code)
                injected.append(link.code)
        out_lines.append(line)
    return "\n".join(out_lines), injected


async def list_active(pool: Any) -> list[AffiliateLink]:
    rows = await pool.fetch(
        "SELECT code, keyword, url, COALESCE(display_text, '') AS display_text "
        "FROM affiliate_links WHERE is_active = true ORDER BY id"
    )
    return [
        AffiliateLink(
            code=r["code"], keyword=r["keyword"], url=r["url"],
            display_text=r["display_text"],
        )
        for r in rows
    ]


async def add_link(
    pool: Any, *, code: str, keyword: str, url: str,
    display_text: str = "", program: str = "",
) -> None:
    await pool.execute(
        "INSERT INTO affiliate_links (code, keyword, url, display_text, program) "
        "VALUES ($1, $2, $3, $4, $5) "
        "ON CONFLICT (code) DO UPDATE SET keyword = EXCLUDED.keyword, "
        "url = EXCLUDED.url, display_text = EXCLUDED.display_text, "
        "program = EXCLUDED.program, updated_at = now()",
        code, keyword, url, display_text or None, program or None,
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
    "AffiliateLink", "inject_affiliate_links", "list_active",
    "add_link", "set_active", "remove_link",
]
