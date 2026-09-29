"""Unit tests for services/preview_page.py: one renderer for the preview page.

``GET /preview/{token}`` serves this page to the operator, and the qa.vision
rendered-preview leg screenshots the same page rendered from the in-flight
draft. These tests pin that the route and the QA leg cannot drift apart, and
that the escaping the 2026-05-12 security audit added survived the move.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from poindexter.services.preview_page import (
    PREVIEW_PAGE_CSP,
    draft_preview_post,
    preview_content_html,
    render_preview_page,
)


@pytest.mark.unit
class TestRenderPreviewPage:
    def test_escapes_llm_influenced_fields(self):
        html = render_preview_page({
            "title": "<script>alert(1)</script>T",
            "excerpt": "<img src=x onerror=alert(1)>",
            "status": "<b>draft</b>",
            "quality_score": "<i>9</i>",
            "content": "<p>body</p>",
        })
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;T" in html
        assert "<img src=x onerror" not in html
        # Status is upper-cased AFTER escaping, so the entities come out as
        # &LT;/&GT; — HTML5 legacy named references, still inert text.
        assert "<b>" not in html.lower()
        assert "&LT;B&GT;DRAFT&LT;/B&GT;" in html
        assert "<p>body</p>" in html  # content is already-rendered HTML

    def test_missing_fields_render_placeholders(self):
        html = render_preview_page({"title": None, "content": None, "status": None})
        assert "[PREVIEW] Untitled" in html
        assert "UNKNOWN" in html
        assert "Q: ?" in html

    def test_unresolved_writer_markers_are_stripped(self):
        html = render_preview_page({
            "title": "T",
            "content": "<p>before</p>\n[IMAGE-2: a diagram]\n<p>after</p>",
        })
        assert "[IMAGE-2" not in html
        assert "<p>after</p>" in html

    def test_media_players_only_when_published_copies_exist(self):
        html = render_preview_page({
            "title": "T", "content": "", "has_podcast": True,
            "podcast_url": "https://r2.example/p.mp3",
        })
        assert 'src="https://r2.example/p.mp3"' in html
        assert "Podcast Ready" in html
        assert "<video" not in html

    def test_a_long_bare_url_link_wraps_instead_of_scrolling_the_page_sideways(self):
        """A link whose text is a bare URL has no break opportunity, so on a phone it
        pushed the whole page sideways: on the evidence draft one Wikipedia link was
        578 px wide in a 358 px column (page scrollWidth 618 at a 390 px viewport).
        Links wrap at any character. The rule is scoped to links on purpose:
        ``article {overflow-wrap:anywhere}`` would also let table columns shrink and
        break words mid-cell, which hides the wide-table overflow the qa.vision leg
        exists to report."""
        html = render_preview_page({"title": "T", "content": "<p>x</p>"})
        assert "article a{color:#22d3ee;overflow-wrap:anywhere}" in html
        article_rule = next(line for line in html.splitlines() if line.startswith("article{"))
        assert "overflow-wrap" not in article_rule

    def test_csp_forbids_scripts(self):
        directives = dict(
            d.strip().split(" ", 1) for d in PREVIEW_PAGE_CSP.split(";") if d.strip()
        )
        assert directives["default-src"] == "'none'"
        assert "script-src" not in directives


@pytest.mark.unit
class TestDraftPreviewPost:
    def test_renders_markdown_like_the_task_preview(self):
        post = draft_preview_post(
            title="Tuning FastAPI",
            content_markdown="## Intro\n\nSome **bold** text.",
            featured_image_url="https://r2.example/hero.webp",
            quality_score=72,
        )
        assert post["title"] == "Tuning FastAPI"
        assert "<h2>Intro</h2>" in post["content"]
        assert "<strong>bold</strong>" in post["content"]
        assert post["featured_image_url"] == "https://r2.example/hero.webp"
        assert post["status"] == "in_progress"
        assert post["quality_score"] == 72

    def test_unwraps_a_leaked_json_envelope(self):
        leaked = '```json\n{"title": "T", "post_body": "## Heading\\n\\nBody text."}\n```'
        html = preview_content_html(leaked)
        assert "<h2>Heading</h2>" in html
        assert "post_body" not in html

    def test_empty_content(self):
        assert preview_content_html("") == ""


@pytest.mark.unit
@pytest.mark.asyncio
class TestRouteServesTheSharedRenderer:
    async def test_route_body_is_the_renderer_output(self):
        """One renderer, two consumers: what the route serves is exactly what
        the QA leg screenshots for the same fields."""
        import poindexter.routes.cms_routes as cms

        post = {
            "title": "Tuning FastAPI",
            "content": "<h2>Intro</h2><p>Body</p>",
            "status": "awaiting_approval",
            "quality_score": 86,
            "excerpt": "An excerpt",
            "featured_image_url": "https://r2.example/hero.webp",
        }
        with patch.object(cms, "preview_post", new=AsyncMock(return_value=dict(post))):
            resp = await cms.preview_post_html("a" * 32, None)
        assert resp.body.decode() == render_preview_page(post)
        assert resp.headers["content-security-policy"] == PREVIEW_PAGE_CSP
