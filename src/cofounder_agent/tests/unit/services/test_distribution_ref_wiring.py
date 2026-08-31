"""Every outbound link composer actually applies the attribution tag.

``test_distribution_ref`` proves the helper shapes a URL correctly. This file
proves the helper is *reached* — which is the failure that actually happened:
the links were composed in four unrelated places and every one of them shipped
a bare ``{site_url}/posts/{slug}``, so 197 placements over 90 days produced 5
identifiable referrals.

A new outbound surface that forgets to tag will look exactly like a surface
nobody clicks. These tests are the guard against that, so they assert on the
composed OUTPUT rather than on the helper being imported.
"""

from __future__ import annotations

import pytest

from services.devto_service import DevToCrossPostService, _append_origin_backlink
from services.jobs.youtube_payload import _build_youtube_description
from services.site_config import SiteConfig
from services.social_drafts import _ensure_post_url

SITE_URL = "https://www.gladlabs.io"
SLUG = "a-morse-code-headline-28431849"
POST_URL = f"{SITE_URL}/posts/{SLUG}"

_SC = SiteConfig(
    initial_config={
        "site_url": SITE_URL,
        "devto_origin_backlink_template": "---\n\n*Originally published at [{site_host}]({url}).*",
    }
)


# ---------------------------------------------------------------------------
# YouTube — the description back-link
# ---------------------------------------------------------------------------


def test_youtube_backlink_is_tagged():
    out = _build_youtube_description(
        seo_description="A great post",
        body="Body text.",
        site_config=_SC,
        slug=SLUG,
    )
    assert f"Read the full post: {POST_URL}?utm_source=youtube&utm_medium=video" in out


def test_youtube_backlink_still_omitted_without_a_site_url():
    """Tagging must not turn a graceful omission into a broken link."""
    out = _build_youtube_description(
        seo_description="A great post", body="Body.", site_config=None, slug=SLUG
    )
    assert "Read the full post" not in out


# ---------------------------------------------------------------------------
# Dev.to — internal links, images, and the origin footer
# ---------------------------------------------------------------------------


def test_devto_internal_links_are_tagged():
    md = "See [the earlier post](/posts/why-vram-matters) for context."
    out = DevToCrossPostService._clean_markdown(md, _SC)
    assert f"{SITE_URL}/posts/why-vram-matters?utm_source=devto&utm_medium=syndication" in out


def test_devto_images_are_absolutised_but_not_tagged():
    """An ``<img>`` src is fetched by the platform, never clicked. Tagging it
    would manufacture phantom traffic against our own asset URLs.

    This is why the link branch carries a ``(?<!!)`` lookbehind: ``![alt](…)``
    also matches a bare ``[…](…)``, so without it the image would be claimed
    by the link branch and tagged.
    """
    md = "![a diagram](/images/diagram.png)"
    out = DevToCrossPostService._clean_markdown(md, _SC)
    assert out == f"![a diagram]({SITE_URL}/images/diagram.png)"
    assert "utm_source" not in out


def test_devto_mixed_link_and_image_are_treated_differently():
    md = "Read [the post](/posts/x) and see ![chart](/images/c.png)."
    out = DevToCrossPostService._clean_markdown(md, _SC)
    assert "/posts/x?utm_source=devto" in out
    assert f"![chart]({SITE_URL}/images/c.png)" in out


def test_devto_origin_footer_is_appended_and_tagged():
    out = _append_origin_backlink("Body text.", POST_URL, _SC)
    assert out.startswith("Body text.")
    assert f"{POST_URL}?utm_source=devto&utm_medium=syndication" in out
    assert "www.gladlabs.io" in out


def test_devto_origin_footer_is_idempotent():
    """A retried cross-post must not stack two footers."""
    once = _append_origin_backlink("Body.", POST_URL, _SC)
    assert _append_origin_backlink(once, POST_URL, _SC) == once


def test_devto_origin_footer_is_off_when_the_template_is_blank():
    sc = SiteConfig(
        initial_config={"site_url": SITE_URL, "devto_origin_backlink_template": ""}
    )
    assert _append_origin_backlink("Body.", POST_URL, sc) == "Body."


def test_devto_origin_footer_survives_a_typod_placeholder():
    """An operator config error must cost the back-link, not the crosspost."""
    sc = SiteConfig(
        initial_config={
            "site_url": SITE_URL,
            "devto_origin_backlink_template": "See {nonexistent}",
        }
    )
    assert _append_origin_backlink("Body.", POST_URL, sc) == "Body."


@pytest.mark.asyncio
async def test_devto_canonical_url_is_never_tagged():
    """The canonical is the canonicalisation SIGNAL — a query string there is a
    different URL, and search engines would consolidate on the tagged variant.
    The attributable path is the footer link beside it, not this field.
    """
    captured: dict = {}

    class _Svc(DevToCrossPostService):
        async def _get_api_key(self):  # type: ignore[override]
            return "key"

    svc = _Svc(pool=None, site_config=_SC)

    import httpx

    class _Resp:
        status_code = 201
        text = "{}"

        def json(self):
            return {"url": "https://dev.to/x/y"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, _url, **kwargs):
            captured.update(kwargs.get("json") or {})
            return _Resp()

    orig = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **k: _Client()  # type: ignore[assignment]
    try:
        await svc.cross_post(
            title="T", content_markdown="Body.", canonical_url=POST_URL, tags=[]
        )
    finally:
        httpx.AsyncClient = orig  # type: ignore[assignment]

    assert captured["article"]["canonical_url"] == POST_URL
    assert "utm_source" not in captured["article"]["canonical_url"]
    # ...while the body DOES carry the tagged path back.
    assert "utm_source=devto" in captured["article"]["body_markdown"]


# ---------------------------------------------------------------------------
# Social — the approve-time repair
# ---------------------------------------------------------------------------


def test_ensure_post_url_replaces_an_untagged_url_with_the_tagged_one():
    """Drafts written before tagging existed are repaired on approve — the
    back-fill path, not a special case."""
    tagged = f"{POST_URL}?utm_source=bluesky&utm_medium=social"
    out = _ensure_post_url(f"Great read! {POST_URL}", tagged, SITE_URL)
    assert out == f"Great read! {tagged}"


def test_ensure_post_url_swaps_one_surface_tag_for_another():
    """Short-form copy is shared across X/Bluesky/Mastodon, so the stored draft
    can carry a sibling's tag; approve settles it to this draft's platform."""
    out = _ensure_post_url(
        f"Great read! {POST_URL}?utm_source=twitter&utm_medium=social",
        f"{POST_URL}?utm_source=mastodon&utm_medium=social",
        SITE_URL,
    )
    assert "utm_source=mastodon" in out
    assert "utm_source=twitter" not in out


def test_ensure_post_url_treats_the_replacement_as_literal_text():
    """The canonical is DATA in an ``re.sub`` replacement slot. Backreference
    syntax in it must not be expanded — harmless for a bare slug, but the tag
    makes this URL operator-configurable."""
    tagged = f"{POST_URL}?utm_source=x&utm_medium=social"
    out = _ensure_post_url(f"read {POST_URL}", tagged, SITE_URL)
    assert out.endswith(tagged)
