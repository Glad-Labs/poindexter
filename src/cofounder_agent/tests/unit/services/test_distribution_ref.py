"""Tests for ``services.distribution_ref`` — outbound link attribution.

The failure this module exists to prevent is not a crash, it is a URL that
looks fine and carries no information: over the 90 days to 2026-08-31, 197
outbound placements (77 social promos, 107 Dev.to crossposts, 12 YouTube
uploads) produced 5 identifiable referrals, because nothing in the link said
where it came from. So the tests below are mostly about the tag being present,
correct, and impossible to lose quietly.
"""

from __future__ import annotations

import pytest

from services.distribution_ref import (
    SURFACE_MEDIUM,
    RefConfig,
    resolve_ref_config,
    tag_for,
    tag_url,
)

POST = "https://www.gladlabs.io/posts/a-morse-code-headline-28431849"


class _FakeSiteConfig:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = values or {}

    def get(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)


# ---------------------------------------------------------------------------
# tag_url
# ---------------------------------------------------------------------------


def test_tags_source_and_medium_by_default():
    tagged = tag_url(POST, surface="devto")
    assert tagged.startswith(POST + "?")
    assert "utm_source=devto" in tagged
    assert "utm_medium=syndication" in tagged


def test_medium_comes_from_the_surface_map():
    assert "utm_medium=social" in tag_url(POST, surface="bluesky")
    assert "utm_medium=video" in tag_url(POST, surface="youtube")


def test_unmapped_surface_still_gets_a_source():
    """A platform nobody has classified must still be attributable.

    Blocking the tag on a missing medium would mean the newest surface — the
    one whose value is genuinely unknown — is the only one we cannot measure.
    """
    tagged = tag_url(POST, surface="lobsters")
    assert "utm_source=lobsters" in tagged
    assert "utm_medium" not in tagged


def test_explicit_medium_overrides_the_map():
    tagged = tag_url(POST, surface="youtube", medium="social")
    assert "utm_medium=social" in tagged
    assert "utm_medium=video" not in tagged


def test_preserves_existing_query_and_fragment():
    url = f"{POST}?page=2#section"
    tagged = tag_url(url, surface="x")
    assert "page=2" in tagged
    assert tagged.endswith("#section")
    assert "utm_source=x" in tagged


def test_is_idempotent():
    once = tag_url(POST, surface="devto")
    assert tag_url(once, surface="devto") == once


def test_does_not_clobber_a_hand_placed_tag():
    """An operator's own tag wins — a link that already says where it came
    from is already doing this module's job, and silently rewriting it would
    break whatever campaign they set it up for."""
    manual = f"{POST}?utm_source=newsletter-2026-08"
    assert tag_url(manual, surface="devto") == manual


def test_disabled_config_returns_the_url_untouched():
    assert tag_url(POST, surface="devto", config=RefConfig(enabled=False)) == POST


def test_custom_param_names_are_honoured():
    cfg = RefConfig(source_param="ref", medium_param="")
    tagged = tag_url(POST, surface="devto", config=cfg)
    assert tagged.endswith("?ref=devto")


def test_empty_medium_param_means_source_only():
    cfg = RefConfig(medium_param="")
    assert "utm_medium" not in tag_url(POST, surface="devto", config=cfg)


@pytest.mark.parametrize(
    "url", ["", "/posts/relative", "mailto:matt@example.com", "ftp://x/y"]
)
def test_non_http_urls_are_left_alone(url):
    """A relative or non-web target must not grow a query string — the caller
    still has to resolve it, and a tag would corrupt that."""
    assert tag_url(url, surface="devto") == url


@pytest.mark.parametrize("surface", ["", "Devto", "dev.to", "a" * 33, "-bad", "x y"])
def test_malformed_surface_raises(surface):
    """Loud, not lenient. A typo'd token that silently produced an untagged
    link would reproduce the exact blind spot this module removes — and it
    would look like "the surface delivers nothing" forever."""
    with pytest.raises(ValueError):
        tag_url(POST, surface=surface)


# ---------------------------------------------------------------------------
# config resolution
# ---------------------------------------------------------------------------


def test_none_site_config_uses_the_seeded_defaults():
    cfg = resolve_ref_config(None)
    assert (cfg.enabled, cfg.source_param, cfg.medium_param) == (
        True,
        "utm_source",
        "utm_medium",
    )


def test_reads_settings_off_site_config():
    cfg = resolve_ref_config(
        _FakeSiteConfig(
            {
                "distribution_ref_enabled": "false",
                "distribution_ref_source_param": "ref",
                "distribution_ref_medium_param": "",
            }
        )
    )
    assert cfg == RefConfig(enabled=False, source_param="ref", medium_param="")


def test_blank_source_param_falls_back_rather_than_emitting_a_nameless_tag():
    cfg = resolve_ref_config(_FakeSiteConfig({"distribution_ref_source_param": "  "}))
    assert cfg.source_param == "utm_source"


def test_settings_read_failure_does_not_break_the_link():
    class _Exploding:
        def get(self, *_a, **_k):
            raise RuntimeError("settings unavailable")

    # A config blip must not take the publish path down with it — the link is
    # still correct, it just goes out unattributed.
    assert resolve_ref_config(_Exploding()) == RefConfig()


def test_tag_for_threads_config_through():
    sc = _FakeSiteConfig({"distribution_ref_source_param": "ref", "distribution_ref_medium_param": ""})
    assert tag_for(sc, POST, surface="youtube").endswith("?ref=youtube")


# ---------------------------------------------------------------------------
# the surface map itself
# ---------------------------------------------------------------------------


def test_every_mapped_surface_is_a_legal_token():
    """The map's own keys have to satisfy the validator, or a surface would be
    classified in code and rejected at call time."""
    for surface in SURFACE_MEDIUM:
        assert tag_url(POST, surface=surface)


def test_social_platforms_share_one_medium():
    """`what did social deliver in total` has to be one query, not a list of
    every platform we have ever posted to."""
    for platform in ("x", "twitter", "bluesky", "mastodon", "linkedin", "reddit"):
        assert SURFACE_MEDIUM[platform] == "social"
