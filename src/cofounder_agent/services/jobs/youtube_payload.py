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

logger = logging.getLogger(__name__)

_YOUTUBE_DESCRIPTION_BUDGET = 4800
_YOUTUBE_MAX_TAGS = 30
_YOUTUBE_TAGS_JOINED_LIMIT = 500


def _strip_markup(text: str) -> str:
    """Strip HTML/markdown tags and collapse whitespace.

    Mirrors the ``re.sub(r"<[^>]+>", "", ...)`` + whitespace-collapse
    approach used for the SEO excerpt in
    ``publish_service`` (~line 546) so the video description body reads
    as plain text rather than leaking ``<img>`` / ``<a>`` markup.
    """
    if not text:
        return ""
    stripped = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", stripped).strip()


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


def _build_youtube_description(
    *,
    seo_description: str,
    body: str,
    site_config: Any,
    slug: str,
) -> str:
    """Compose the YouTube video description from SEO metadata + body.

    Layout::

        {seo_description}

        Read the full post: {site_url}/posts/{slug}

        {body_excerpt}

    ``seo_description`` comes from ``posts.excerpt`` (empty string when
    null). ``body_excerpt`` is the stripped content body, trimmed so the
    TOTAL composed description stays ≤ 4800 chars. The "Read the full
    post" line is omitted gracefully (logged at info) when ``site_url``
    can't be resolved or ``slug`` is missing — never raises.
    """
    # Strip HTML from the excerpt — posts.excerpt occasionally contains
    # inline <img> tags from the pipeline. YouTube rejects any angle bracket
    # in the description body (HTTP 400 invalidDescription).
    seo_description = _strip_markup(seo_description or "")

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
                "YouTube back-link: %s", exc,
            )
            site_url = ""
    if site_url and slug:
        backlink = f"Read the full post: {site_url}/posts/{slug}"
    elif not slug:
        logger.info(
            "[YOUTUBE_PAYLOAD] slug missing — omitting YouTube back-link",
        )

    body_excerpt = _strip_markup(body)

    # Compose with blank-line separators, skipping empty segments so a
    # null seo_description doesn't leave a leading blank line.
    header_parts = [p for p in (seo_description, backlink) if p]
    header = "\n\n".join(header_parts)

    if not header:
        # No SEO desc and no back-link — description is just the body.
        return body_excerpt[:_YOUTUBE_DESCRIPTION_BUDGET].replace("<", "").replace(">", "")

    if not body_excerpt:
        return header[:_YOUTUBE_DESCRIPTION_BUDGET].replace("<", "").replace(">", "")

    # Reserve room for the header + the "\n\n" joining it to the body,
    # then trim the body to fit the remaining budget.
    remaining = _YOUTUBE_DESCRIPTION_BUDGET - len(header) - 2
    if remaining <= 0:
        composed = header[:_YOUTUBE_DESCRIPTION_BUDGET]
    else:
        composed = f"{header}\n\n{body_excerpt[:remaining]}"
    # YouTube rejects any bare < or > (e.g. SQL WHERE x > 0, markdown arrows).
    # Strip them so the upload never 400s on invalidDescription.
    return composed.replace("<", "").replace(">", "")


__all__ = ["_build_youtube_description", "_parse_seo_keywords", "_strip_markup"]
