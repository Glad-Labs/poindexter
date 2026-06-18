"""Unit tests for ``services/text_utils.py``."""

from __future__ import annotations

from types import SimpleNamespace

from services.text_utils import (
    DEFAULT_TRUSTED_DOMAINS,
    normalize_text,
    scrub_fabricated_links,
)


class TestNormalizeText:
    def test_empty_returns_unchanged(self):
        assert normalize_text("") == ""
        assert normalize_text(None) is None  # type: ignore[arg-type]

    def test_replaces_smart_punctuation(self):
        raw = "\u201chello\u201d \u2019 \u2014 \u2013 \u2026"
        assert normalize_text(raw) == '"hello" \' -- - ...'

    def test_replaces_special_whitespace(self):
        assert normalize_text("a\u00a0b") == "a b"
        assert normalize_text("a\u2011b") == "a-b"

    def test_leaves_ascii_alone(self):
        assert normalize_text("plain ascii text") == "plain ascii text"


class TestScrubFabricatedLinks:
    def _site_cfg(self, **kwargs):
        defaults = {
            "trusted_source_domains": "",
            "site_domain": "",
        }
        defaults.update(kwargs)
        return SimpleNamespace(get=lambda k, d="": defaults.get(k, d))

    def test_keeps_trusted_markdown_link(self):
        body = "See [docs](https://github.com/python/cpython) for more."
        out = scrub_fabricated_links(body, site_config=self._site_cfg())
        assert "https://github.com/python/cpython" in out

    def test_removes_untrusted_markdown_link_keeps_text(self):
        body = "A [fake source](https://totally-made-up.example) claim."
        out = scrub_fabricated_links(body, site_config=self._site_cfg())
        assert "made-up.example" not in out
        assert "fake source" in out  # text preserved

    def test_strips_untrusted_bare_url(self):
        body = "Check https://totally-made-up.example here"
        out = scrub_fabricated_links(body, site_config=self._site_cfg())
        assert "made-up.example" not in out
        assert "here" in out

    def test_respects_csv_override(self):
        body = "See [x](https://niche-reference.example/a)"
        out = scrub_fabricated_links(body, site_config=self._site_cfg(trusted_source_domains="niche-reference.example"),)
        assert "niche-reference.example" in out  # now trusted

    def test_trusts_own_domain_plus_www_variant(self):
        body = "See [x](https://example.com/about) and [y](https://www.example.com/blog)"
        out = scrub_fabricated_links(body, site_config=self._site_cfg(site_domain="example.com"),)
        assert "example.com/about" in out
        assert "www.example.com/blog" in out

    def test_internal_posts_slug_allowlist_filters_fakes(self):
        body = "See [real](https://example.com/posts/real-slug) vs [fake](https://example.com/posts/made-up)."
        out = scrub_fabricated_links(body, known_slugs={"real-slug"}, site_config=self._site_cfg(site_domain="example.com"),)
        assert "posts/real-slug" in out
        assert "posts/made-up" not in out  # dropped; text "fake" preserved
        assert "fake" in out

    def test_no_slug_allowlist_accepts_internal_links(self):
        body = "See [x](https://example.com/posts/any-slug)."
        out = scrub_fabricated_links(
            body, site_config=self._site_cfg(site_domain="example.com")
        )  # known_slugs defaults to None
        assert "posts/any-slug" in out

    # --- orphaned-anchor repair (regression) -------------------------------
    # When the scrubbed link's anchor is an *appended Title-Case citation*
    # (not inline prose), dropping only the href strands the anchor mid-
    # sentence as broken English. These assert the whole construct is
    # removed and the sentence reads cleanly. Shapes match what local
    # writer models fabricate when inventing cross-links.

    def test_removes_appended_titlecase_citation_without_orphan(self):
        body = (
            "We've seen the trend elsewhere too "
            "[Local LLM Hardware Requirements](https://made-up.example/x). "
            "The hardware press is full of benchmarks."
        )
        out = scrub_fabricated_links(body, site_config=self._site_cfg())
        assert "made-up.example" not in out  # href dropped
        assert "Local LLM Hardware Requirements" not in out  # no orphan anchor
        assert "elsewhere too. The hardware press" in out  # sentence repaired

    def test_removes_ellipsis_truncated_title_without_orphan(self):
        body = (
            "the idea that more FLOPS equals faster LLMs "
            "[Optimizing Local...](https://made-up.example/y). It isn't."
        )
        out = scrub_fabricated_links(body, site_config=self._site_cfg())
        assert "made-up.example" not in out
        assert "Optimizing Local" not in out  # no orphan anchor
        assert "faster LLMs. It isn't." in out  # sentence repaired

    def test_removes_fabricated_internal_titlecase_without_orphan(self):
        # Own-domain /posts/<fake-slug> link whose slug fails the allowlist:
        # own_domain is trusted, so it survives _is_trusted but is dropped by
        # the slug check — must not orphan the Title-Case anchor either.
        body = (
            "We covered this before too "
            "[The Decode Phase Trap](https://example.com/posts/made-up-slug). "
            "Memory dominates."
        )
        out = scrub_fabricated_links(
            body,
            known_slugs={"real-slug"},
            site_config=self._site_cfg(site_domain="example.com"),
        )
        assert "posts/made-up-slug" not in out
        assert "The Decode Phase Trap" not in out  # no orphan anchor
        assert "this before too. Memory dominates." in out

    def test_keeps_titlecase_anchor_after_connector_to_avoid_dangling(self):
        # Guard: when a connector (preposition/article) precedes the link,
        # removing the whole phrase would dangle the connector ("...live in.").
        # Keep the anchor text instead — the safe, grammatical direction.
        body = "Details live in [The CUDA Programming Guide](https://made-up.example/z)."
        out = scrub_fabricated_links(body, site_config=self._site_cfg())
        assert "made-up.example" not in out  # href still dropped
        assert "live in The CUDA Programming Guide." in out  # anchor kept

    def test_default_trusted_domains_freezeset(self):
        # Sanity — the frozenset should include canonical references.
        assert "github.com" in DEFAULT_TRUSTED_DOMAINS
        assert "docs.python.org" in DEFAULT_TRUSTED_DOMAINS
