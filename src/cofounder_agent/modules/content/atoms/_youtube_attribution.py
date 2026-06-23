"""Pure YouTube-attribution core for the citation-repair atom.

Underscore-prefixed so ``atom_registry._walk_package`` skips it — a helper, not
a discoverable atom. DB-free and network-free: it does the deterministic
detection + rewrite given an already-resolved ``{url: author_name}`` map. The
atom (``content.reconcile_citations``) resolves channel names via the YouTube
oEmbed endpoint and hands the map here.

Goal (Matt 2026-06-23): "proper attribution and not a raw youtube url." A bare
YouTube video URL becomes a ``[Channel Name](url)`` link; a markdown link whose
text is itself the URL / the video id is relabelled to the channel name. A link
the writer already attributed with human text is never overwritten, and a URL
whose channel couldn't be resolved is left untouched (fail-soft).
"""

from __future__ import annotations

import re

# A YouTube URL token: youtube.com or youtu.be, grabbed up to the first
# whitespace / closing delimiter. Trailing sentence punctuation is peeled by
# ``_clean_url`` so "…/watch?v=ID." doesn't swallow the period.
_YT_URL_PAT = r"https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/[^\s)\]<>\"'`]+"
_YT_URL_RE = re.compile(_YT_URL_PAT)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((" + _YT_URL_PAT + r")\)")
# 11-char video id from any watchable form; channel/playlist URLs have none.
_VIDEO_ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|shorts/|live/|embed/)([\w-]{11})")

_RAW_TEXT_TOKENS = frozenset(
    {"youtube", "youtube.com", "www.youtube.com", "youtu.be", "video", "watch"}
)


def _clean_url(raw: str) -> str:
    """Peel trailing sentence punctuation a greedy match may have absorbed."""
    return raw.rstrip(".,;:!?")


def _video_id(url: str) -> str:
    m = _VIDEO_ID_RE.search(url)
    return m.group(1) if m else ""


def find_youtube_urls(content: str | None) -> list[str]:
    """Distinct watchable YouTube URLs in ``content`` (bare or markdown-linked).

    Only URLs carrying an 11-char video id are returned — those the oEmbed
    endpoint can resolve to a channel. Channel/playlist URLs (no video id) are
    skipped. Deduped by cleaned URL, first-seen order preserved.
    """
    if not content:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _YT_URL_RE.finditer(content):
        url = _clean_url(m.group(0))
        if not _video_id(url) or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _is_raw_text(text: str, url: str) -> bool:
    """A link text that is NOT human attribution — the URL, the video id, or a
    bare-domain placeholder — so relabelling it to the channel is an improvement
    (never an overwrite of real editorial text)."""
    t = text.strip()
    if not t:
        return True
    if t == url.strip() or _YT_URL_RE.fullmatch(t):
        return True
    vid = _video_id(url)
    if vid and t == vid:
        return True
    return t.lower() in _RAW_TEXT_TOKENS


def apply_youtube_attribution(
    content: str | None, authors: dict[str, str],
) -> tuple[str, list[dict]]:
    """Rewrite YouTube references for proper attribution given ``{url: author}``.

    - bare URL → ``[author](url)``
    - markdown link with raw text (URL / video id / placeholder) → relabel to author
    - markdown link with human text → left untouched
    - URL with no resolved author → left untouched (fail-soft)

    Returns ``(new_content, changes)`` with ``changes`` a list of
    ``{"url", "author", "kind"}`` (``kind`` ∈ {"bare", "relabel"}) in document
    order. Idempotent: a second pass finds nothing left to rewrite.
    """
    if not content or not authors:
        return content or "", []

    # (start, end, replacement, change-record)
    edits: list[tuple[int, int, str, dict]] = []
    link_spans: list[tuple[int, int]] = []

    # Pass 1: markdown links — relabel only raw text, never human attribution.
    for m in _MD_LINK_RE.finditer(content):
        link_spans.append(m.span())
        text = m.group(1)
        url = _clean_url(m.group(2))
        author = authors.get(url)
        if author and _is_raw_text(text, url):
            edits.append(
                (m.start(), m.end(), f"[{author}]({url})",
                 {"url": url, "author": author, "kind": "relabel"}),
            )

    # Pass 2: bare URLs not sitting inside a markdown link.
    for m in _YT_URL_RE.finditer(content):
        s, e = m.span()
        if any(ls <= s and e <= le for ls, le in link_spans):
            continue
        raw = m.group(0)
        url = _clean_url(raw)
        e -= len(raw) - len(url)  # keep peeled punctuation in the residual
        if not _video_id(url):
            continue
        author = authors.get(url)
        if author:
            edits.append(
                (s, e, f"[{author}]({url})",
                 {"url": url, "author": author, "kind": "bare"}),
            )

    if not edits:
        return content, []

    # Resolve overlaps greedily left-to-right; apply right-to-left.
    edits.sort(key=lambda x: x[0])
    final: list[tuple[int, int, str, dict]] = []
    last_end = -1
    for s, e, r, c in edits:
        if s < last_end:
            continue
        final.append((s, e, r, c))
        last_end = e

    new = content
    for s, e, r, _c in sorted(final, key=lambda x: x[0], reverse=True):
        new = f"{new[:s]}{r}{new[e:]}"
    changes = [c for _s, _e, _r, c in sorted(final, key=lambda x: x[0])]
    return new, changes


__all__ = ["apply_youtube_attribution", "find_youtube_urls"]
