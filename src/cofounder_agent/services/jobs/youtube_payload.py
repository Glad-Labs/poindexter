"""Shared YouTube upload-payload builders (description + tags).

Extracted from services/jobs/backfill_videos.py (glad-labs-stack#1460 PR1) so
the surviving distributor (media_distribute) no longer imports them from a job
that PR2 deletes. Pure string helpers — no DB, no heavy deps.

YouTube Data API v3 hard caps (NOT operator-tunable): description ≤ 5000 chars
(we compose to ≤ 4800 for headroom); tags ≤ 30 and ≤ 500 joined chars. The
adapter (services/publish_adapters/youtube.py) enforces the hard caps; we build
values that stay comfortably under them so the upload never 400s mid-stream.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from services.distribution_ref import tag_for
from utils.exception_format import describe_exception

logger = logging.getLogger(__name__)

_YOUTUBE_DESCRIPTION_BUDGET = 4800
_YOUTUBE_MAX_TAGS = 30
_YOUTUBE_TAGS_JOINED_LIMIT = 500


def _strip_markup(text: str) -> str:
    """Strip HTML tags and collapse ALL whitespace to single spaces.

    The right tool for one-line fields (the excerpt header). It is the wrong
    tool for a markdown body — it flattens every paragraph break, which is how
    the pre-#3508-follow-up descriptions became one 4,800-char wall — so the
    body path uses :func:`_markdown_to_plain` instead.
    """
    if not text:
        return ""
    stripped = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", stripped).strip()


# Markdown constructs that must not reach a YouTube viewer as raw syntax. Each
# pattern keeps the human-readable text and drops the machinery. Dep-free on
# purpose: this is a teaser renderer, not a markdown engine — anything it
# misses degrades to slightly odd punctuation, never to a broken upload.
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MD_EMPHASIS_RE = re.compile(r"(\*{1,3}|_{1,3}|`{1,3})(?=\S)|(?<=\S)(\*{1,3}|_{1,3}|`{1,3})")
_MD_FENCE_RE = re.compile(r"^\s*```[^\n]*$", re.MULTILINE)


def _markdown_to_plain(text: str) -> str:
    """Render a markdown body to viewer-facing plain text.

    Keeps paragraph breaks (blank-line separated), collapses whitespace only
    WITHIN a paragraph, and strips the syntax the live videos were leaking
    verbatim: ``[text](url)`` becomes just the text (the URLs are relative
    ``/go/…`` affiliate and ``/posts/…`` internal paths — dead as description
    text, and the affiliate slugs are nobody's business), images vanish,
    ``## heading`` markers drop while the heading text survives as its own
    paragraph, and emphasis/fence markers go. HTML tags are stripped last.
    """
    if not text:
        return ""
    text = _MD_FENCE_RE.sub("", text)
    text = _MD_IMAGE_RE.sub("", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    # Replace the marker with a paragraph break rather than nothing: the writer
    # often emits "## Heading" hard against the previous paragraph (single
    # newline), and plain removal glued the heading text onto that paragraph's
    # last sentence.
    text = _MD_HEADING_RE.sub("\n\n", text)
    text = _MD_EMPHASIS_RE.sub("", text)
    text = re.sub(r"<[^>]+>", "", text)
    paragraphs = [
        re.sub(r"\s+", " ", para).strip()
        for para in re.split(r"\n\s*\n", text)
    ]
    return "\n\n".join(p for p in paragraphs if p)


def _trim_at_sentence(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` at a sentence end, else a word end.

    The live descriptions ended mid-word ("Every post gets a val") because the
    budget was applied as a bare slice. A teaser that stops at a full stop
    reads as chosen; one that stops mid-word reads as broken.
    """
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text
    head = text[:limit]
    for stop in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        cut = head.rfind(stop)
        if cut > limit // 2:
            return head[: cut + 1].rstrip()
    cut = head.rfind(" ")
    if cut > limit // 2:
        return head[:cut].rstrip()
    return head.rstrip()


def _parse_seo_keywords(seo_keywords: str) -> list[str]:
    """Parse the comma-separated ``posts.seo_keywords`` column into tags.

    Strips each keyword, drops empties, caps at 30 tags, and trims
    trailing tags until the comma-joined string fits YouTube's combined
    500-char tag limit. Returns ``[]`` when there are no usable keywords
    (caller converts that to ``tags=None``).
    """
    tags = [k.strip() for k in (seo_keywords or "").split(",") if k.strip()]
    tags = tags[:_YOUTUBE_MAX_TAGS]
    # Drop trailing tags until the joined string is under the limit.
    while tags and len(",".join(tags)) > _YOUTUBE_TAGS_JOINED_LIMIT:
        tags.pop()
    return tags


def _body_chars_budget(site_config: Any) -> int:
    """How many characters of article body the description may carry.

    ``youtube_description_body_chars`` — **0 (the default) means none**: the
    description is the excerpt hook plus the tagged back-link, and the article
    itself stays on the site (operator decision, 2026-08-31 — the previous
    behaviour dumped the whole stripped body to the 4,800-char cap, which
    produced a one-paragraph markdown-soup wall on every live video). A
    positive value opts a snippet back in, sentence-trimmed and rendered
    through :func:`_markdown_to_plain`, capped at the 4,800 composition budget.
    """
    if site_config is None:
        return 0
    try:
        raw = str(site_config.get("youtube_description_body_chars", "0") or "0")
        return max(0, min(int(raw), _YOUTUBE_DESCRIPTION_BUDGET))
    except (TypeError, ValueError):
        logger.warning(
            "[YOUTUBE_PAYLOAD] youtube_description_body_chars is not an "
            "integer — treating as 0 (no body snippet)"
        )
        return 0


def _build_youtube_description(
    *,
    seo_description: str,
    body: str,
    site_config: Any,
    slug: str,
) -> str:
    """Compose the YouTube video description.

    Default layout (``youtube_description_body_chars=0``)::

        {seo_description}

        Read the full post: {site_url}/posts/{slug}?utm_source=youtube&…

    With a positive body budget, a sentence-trimmed plain-text snippet of the
    article follows the link — skipping its first paragraph when that is the
    excerpt again (``posts.excerpt`` IS the post's opening paragraph, so
    without the skip the viewer read the same two sentences twice).

    ``seo_description`` comes from ``posts.excerpt`` (empty string when
    null). The "Read the full post" line is omitted gracefully (logged at
    info) when ``site_url`` can't be resolved or ``slug`` is missing — never
    raises. Total stays ≤ 4800 chars (YouTube hard cap 5000; the adapter
    re-clamps as a backstop).
    """
    # The excerpt occasionally carries inline <img> HTML or a stray markdown
    # link from the pipeline; render it to one clean line.
    seo_description = _strip_markup(_markdown_to_plain(seo_description or ""))

    # Resolve the canonical back-link. Missing site_url / slug → omit the
    # line (the only deliberate graceful fallback here, per the #275
    # design); log it so the operator knows why it's absent.
    backlink = ""
    site_url = ""
    if site_config is not None:
        try:
            site_url = str(site_config.require("site_url") or "").rstrip("/")
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[YOUTUBE_PAYLOAD] site_url unavailable — omitting "
                "YouTube back-link: %s", describe_exception(exc),
            )
            site_url = ""
    if site_url and slug:
        # Tagged so a click from the description is attributable to YouTube
        # rather than landing in the "(direct)" bucket — a video description is
        # exactly the kind of link a browser sends no referrer for.
        backlink = "Read the full post: " + tag_for(
            site_config, f"{site_url}/posts/{slug}", surface="youtube"
        )
    elif not slug:
        logger.info(
            "[YOUTUBE_PAYLOAD] slug missing — omitting YouTube back-link",
        )

    header_parts = [p for p in (seo_description, backlink) if p]
    header = "\n\n".join(header_parts)

    body_budget = _body_chars_budget(site_config)
    body_snippet = ""
    if body_budget > 0:
        rendered = _markdown_to_plain(body)
        if seo_description:
            # posts.excerpt is the post's opening paragraph; drop the body's
            # first paragraph when it repeats the excerpt so the snippet
            # continues the story instead of restarting it. Prefix-compare
            # (either direction) because the excerpt may itself be a trim.
            paragraphs = rendered.split("\n\n")
            if paragraphs:
                first = paragraphs[0]
                if first.startswith(seo_description) or seo_description.startswith(first):
                    rendered = "\n\n".join(paragraphs[1:])
        room = _YOUTUBE_DESCRIPTION_BUDGET - len(header) - 2 if header else _YOUTUBE_DESCRIPTION_BUDGET
        body_snippet = _trim_at_sentence(rendered, min(body_budget, max(room, 0)))

    if not header:
        composed = body_snippet[:_YOUTUBE_DESCRIPTION_BUDGET]
    elif not body_snippet:
        composed = header[:_YOUTUBE_DESCRIPTION_BUDGET]
    else:
        composed = f"{header}\n\n{body_snippet}"
    # YouTube rejects any bare < or > (e.g. SQL WHERE x > 0, markdown arrows).
    # Strip them so the upload never 400s on invalidDescription.
    return composed.replace("<", "").replace(">", "")


__all__ = [
    "_build_youtube_description",
    "_markdown_to_plain",
    "_parse_seo_keywords",
    "_strip_markup",
    "_trim_at_sentence",
]
