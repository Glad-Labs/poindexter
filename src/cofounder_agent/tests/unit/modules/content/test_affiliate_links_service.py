"""Unit tests for the affiliate-link matcher (pure, no DB)."""

from modules.content.affiliate_links import AffiliateLink, inject_affiliate_links

M = AffiliateLink(code="mercury", keyword="Mercury", url="https://x", display_text="Mercury")


def test_injects_first_mention_only():
    md = "We use Mercury for banking. Mercury is great."
    out, injected = inject_affiliate_links(md, [M], base="/go", cap=3)
    assert injected == ["mercury"]
    assert out.count("[Mercury](/go/mercury)") == 1
    assert out.endswith("Mercury is great.")  # 2nd mention untouched


def test_respects_cap():
    a = AffiliateLink(code="a", keyword="Alpha", url="u", display_text="Alpha")
    b = AffiliateLink(code="b", keyword="Bravo", url="u", display_text="Bravo")
    out, injected = inject_affiliate_links("Alpha and Bravo.", [a, b], cap=1)
    assert injected == ["a"]


def test_skips_fenced_code_block():
    md = "```\nMercury\n```\nUse Mercury here."
    out, injected = inject_affiliate_links(md, [M])
    assert "```\nMercury\n```" in out          # code block untouched
    assert out.count("[Mercury](/go/mercury)") == 1


def test_skips_inline_code():
    md = "Set `Mercury` env then use Mercury."
    out, _ = inject_affiliate_links(md, [M])
    assert "`Mercury`" in out
    assert "[Mercury](/go/mercury)" in out


def test_skips_headings():
    md = "## Mercury\nWe use Mercury daily."
    out, _ = inject_affiliate_links(md, [M])
    assert out.startswith("## Mercury\n")       # heading untouched
    assert "[Mercury](/go/mercury)" in out


def test_skips_existing_link_and_is_idempotent():
    md = "See [Mercury](/go/mercury) here."
    out, injected = inject_affiliate_links(md, [M])
    assert injected == []
    assert out == md


def test_display_text_defaults_to_keyword():
    link = AffiliateLink(code="c", keyword="Cloudflare", url="u")  # no display_text
    out, _ = inject_affiliate_links("We run Cloudflare here.", [link])
    assert "[Cloudflare](/go/c)" in out


def test_noop_on_empty_inputs():
    assert inject_affiliate_links("", [M]) == ("", [])
    assert inject_affiliate_links("text", []) == ("text", [])
