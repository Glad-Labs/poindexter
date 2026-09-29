"""The rendered-preview page: one renderer for the operator's link and the QA screenshot.

``GET /preview/{token}`` (``routes/cms_routes.py``) serves this page to the
operator. The rendered-preview leg of ``qa.vision`` renders the same page from
the in-flight draft and screenshots it. Because both use one renderer, the
vision verdict is about the page the operator will actually open.

The QA leg renders in-process instead of fetching the URL. It runs in the
``qa.*`` block, before ``content.persist_task`` writes the draft or its
``preview_token`` to the database, so at that point the route answers
"Post not found". That was measured: from 2026-07-08 to 07-18, the only
stretch when the URL was reachable from the prefect-worker, 33 of the 34
``rendered_preview`` reviews scored that 404 page at 25-45/100. Rendering from
graph state screenshots the draft under review, and leaves no URL to rot. See
``docs/architecture/preview-links.md``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from html import escape
from typing import Any

from poindexter.services.image_markers import strip_unresolved_image_markers
from poindexter.utils.content_formatting import convert_markdown_to_html

# 2026-05-12 security audit P0 #6: the page needs no JavaScript, so the policy
# is aggressively narrow: no scripts at all (no 'unsafe-inline' or
# 'unsafe-eval'), no fonts, no XHR. Style stays inline-allowed because the page
# CSS lives in a <style> block. The LLM writer reads web research and could echo
# attacker-controlled markup, which is why this policy exists. A page rendered
# without an HTTP response (the QA screenshot) has no header to carry it, so
# that capture disables JavaScript in the browser context instead
# (``services.preview_screenshot.capture_html_tiles``).
PREVIEW_PAGE_CSP = (
    "default-src 'none'; "
    "style-src 'unsafe-inline'; "
    "img-src https: data:; "
    "media-src https: blob:; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


def preview_content_html(content: str) -> str:
    """Stored draft markdown -> the HTML the preview page renders.

    Unwraps any leaked writer JSON envelope (a ```json-fenced
    ``{"title": ..., "post_body": "<markdown>"}`` that slipped past the
    generation-time unwrap) first, so the preview shows the article rather than
    a raw JSON code block. Then markdown -> HTML, so headings, emphasis and
    inline images (both ``![](...)`` markdown and embedded ``<img>`` HTML)
    render the way the published page does (#540).
    """
    if not content:
        return ""
    # Lazy: llm_text pulls in the LLM dispatch stack, which the route and the
    # QA atom already have loaded by the time they get here.
    from poindexter.services.llm_text import maybe_unwrap_json

    return convert_markdown_to_html(maybe_unwrap_json(content))


def draft_preview_post(
    *,
    title: str,
    content_markdown: str,
    excerpt: str = "",
    featured_image_url: str = "",
    quality_score: Any = None,
    status: str = "in_progress",
) -> dict[str, Any]:
    """The mapping :func:`render_preview_page` takes, built from an in-flight draft.

    Mirrors what ``GET /api/posts/preview/{token}`` returns for a task, minus
    the media fields: podcast and video render after QA, so a draft never
    has them.
    """
    return {
        "title": title,
        "content": preview_content_html(content_markdown),
        "excerpt": excerpt,
        "featured_image_url": featured_image_url,
        "quality_score": quality_score,
        "status": status,
    }


def render_preview_page(post: Mapping[str, Any]) -> str:
    """Render the full preview HTML document for a post or task mapping.

    ``post["content"]`` is already HTML (see :func:`preview_content_html`).
    Every other field an LLM or a web page could have influenced (title,
    excerpt, status, media URLs) is escaped before interpolation.
    """
    # dict.get(key, default) returns None when the key exists with a None value,
    # so coerce to fallback strings explicitly with `or` before any string ops.
    title = post.get("title") or "Untitled"
    content = post.get("content") or ""
    status = post.get("status") or "unknown"
    quality = post.get("quality_score") if post.get("quality_score") is not None else "?"
    excerpt = post.get("excerpt") or ""
    featured_img = escape(post.get("featured_image_url") or "")
    has_podcast = post.get("has_podcast", False)
    has_video = post.get("has_video", False)
    podcast_url = escape(post.get("podcast_url") or "")
    video_url = escape(post.get("video_url") or "")
    safe_title = escape(title)
    # 2026-05-12 security audit P0 #6: title/excerpt/status are operator-
    # facing strings derived from LLM output (via research_service) which
    # an attacker-controlled web page could poison through a prompt-
    # injection vector. They flowed into the HTML body raw pre-fix.
    # Escape them explicitly before any string interpolation below.
    safe_excerpt = escape(excerpt)
    safe_status = escape(status)
    safe_quality = escape(str(quality))

    # Build podcast/video players
    media_html = ""
    if podcast_url:
        media_html += f'<div style="margin:16px 0;padding:12px;background:#1a2332;border:1px solid #22c55e44;border-radius:8px"><h3 style="color:#22c55e;font-size:12px;text-transform:uppercase;margin:0 0 8px">Podcast</h3><audio controls style="width:100%" preload="metadata"><source src="{podcast_url}" type="audio/mpeg"></audio></div>'
    if video_url:
        media_html += f'<div style="margin:16px 0;padding:12px;background:#1a2332;border:1px solid #3b82f644;border-radius:8px"><h3 style="color:#3b82f6;font-size:12px;text-transform:uppercase;margin:0 0 8px">Video</h3><video controls style="width:100%;border-radius:6px" preload="metadata" playsinline><source src="{video_url}" type="video/mp4"></video></div>'

    img_html = ""
    if featured_img:
        img_html = f'<img src="{featured_img}" style="width:100%;border-radius:12px;margin:16px 0" alt="{safe_title}">'

    # Clean up preview content — strip the same junk the publish pipeline removes
    # Remove "External Resources" / "Further Reading" sections with empty links
    content = re.sub(
        r'(?:^|\n)#{1,4}\s*(?:External\s+Resources|Further\s+Reading|References|Suggested\s+Resources)[^\n]*\n(?:\s*[-*]\s+[^\n]*\n)*',
        '\n', content, flags=re.IGNORECASE,
    )
    # Remove bullet items that are just labels with colons but no URLs
    content = re.sub(r'^\s*[-*]\s+[^(\[]*:\s*$', '', content, flags=re.MULTILINE)
    # Remove leaked image-gen prompts after images
    content = re.sub(r'(!\[[^\]]*\]\([^\)]+\))\s*\n\s*:\s+[^\n]+', r'\1', content)
    # Remove unresolved placeholders
    # Every writer marker form, not just [IMAGE-N] — a dev_diary draft
    # (no plan_image_markers in its graph) reaches here with raw
    # [IMAGE:] / [SCREENSHOT:] markers the operator would otherwise
    # read as literal text in the preview.
    content = strip_unresolved_image_markers(content)
    # Remove dead link references (title with colon but no URL following)
    content = re.sub(r'^\s*[-*]\s+\[[^\]]+\]\s*$', '', content, flags=re.MULTILINE)
    # Strip photo attribution lines
    content = re.sub(r'\n\s*\*?Photo by [^\n]+(?:Pexels|Unsplash|Pixabay)\*?\s*\n', '\n', content, flags=re.IGNORECASE)
    # Strip empty "External Resources" / "Suggested Resources" sections with no URLs
    content = re.sub(
        r'(?:^|\n)#{1,4}\s*(?:Suggested\s+)?(?:External\s+)?(?:Resources?|References?|Further\s+Reading)[^\n]*\n(?:\s*[-*]\s+[^\n]*\n)*',
        '\n', content, flags=re.IGNORECASE,
    )

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>[PREVIEW] {safe_title}</title>
<style>
body{{font-family:-apple-system,system-ui,sans-serif;background:#0f172a;color:#cbd5e1;margin:0;padding:0}}
.banner{{background:#f59e0b;color:#000;text-align:center;padding:8px;font-weight:bold;font-size:13px;position:sticky;top:0;z-index:50}}
.banner small{{font-weight:normal;opacity:.7;margin-left:8px}}
.container{{max-width:720px;margin:0 auto;padding:16px}}
h1{{color:#fff;font-size:28px;line-height:1.3;margin:16px 0 8px}}
.excerpt{{color:#94a3b8;font-size:16px;line-height:1.6;margin-bottom:16px}}
.badges span{{display:inline-block;padding:4px 10px;border-radius:20px;font-size:12px;margin:0 4px 8px 0}}
.badge-status{{background:#f59e0b33;color:#fbbf24;border:1px solid #f59e0b44}}
.badge-quality{{background:#22c55e33;color:#4ade80;border:1px solid #22c55e44}}
.badge-podcast{{background:#22c55e22;color:#22c55e;border:1px solid #22c55e33}}
.badge-video{{background:#3b82f622;color:#3b82f6;border:1px solid #3b82f633}}
article{{color:#e2e8f0;line-height:1.8;font-size:16px}}
article h1,article h2,article h3{{color:#fff}}
article h2{{font-size:22px;margin:24px 0 12px;border-bottom:1px solid #334155;padding-bottom:8px}}
article h3{{font-size:18px;margin:20px 0 8px}}
article a{{color:#22d3ee;overflow-wrap:anywhere}}
article code{{background:#1e293b;padding:2px 6px;border-radius:4px;font-size:14px;color:#67e8f9}}
article pre{{background:#1e293b;padding:16px;border-radius:8px;overflow-x:auto;border:1px solid #334155}}
article blockquote{{border-left:3px solid #22d3ee55;background:#1e293b44;padding:8px 16px;margin:16px 0;border-radius:0 8px 8px 0}}
article ul,article ol{{padding-left:24px}}
article li{{margin:4px 0}}
article img{{max-width:100%;height:auto;aspect-ratio:auto;border-radius:8px;margin:12px 0}}
.approve{{margin:24px 0;padding:16px;background:#1e293b;border-radius:12px;text-align:center}}
.approve a{{display:inline-block;padding:12px 32px;background:#22c55e;color:#000;text-decoration:none;border-radius:8px;font-weight:bold;font-size:16px}}
</style></head><body>
<div class="banner">PREVIEW MODE<small>{safe_status.upper()} | Q: {safe_quality}</small></div>
<div class="container">
{img_html}
<h1>{safe_title}</h1>
{"<p class='excerpt'>" + safe_excerpt + "</p>" if excerpt else ""}
<div class="badges">
<span class="badge-status">{safe_status.upper()}</span>
<span class="badge-quality">Quality: {safe_quality}</span>
{"<span class='badge-podcast'>Podcast Ready</span>" if has_podcast else ""}
{"<span class='badge-video'>Video Ready</span>" if has_video else ""}
</div>
{media_html}
<article>{content}</article>
</div></body></html>"""


__all__ = [
    "PREVIEW_PAGE_CSP",
    "draft_preview_post",
    "preview_content_html",
    "render_preview_page",
]
