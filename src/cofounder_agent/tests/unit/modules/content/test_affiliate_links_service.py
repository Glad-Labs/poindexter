"""Unit tests for the affiliate-link matcher (pure, no DB)."""

from modules.content.affiliate_links import AffiliateLink, inject_affiliate_links

M = AffiliateLink(code="mercury", keywords=["Mercury"], url="https://x", display_text="Mercury")


def test_injects_first_mention_only():
    md = "We use Mercury for banking. Mercury is great."
    out, injected = inject_affiliate_links(md, [M], base="/go", cap=3)
    assert injected == ["mercury"]
    assert out.count("[Mercury](/go/mercury)") == 1
    assert out.endswith("Mercury is great.")  # 2nd mention untouched


def test_respects_cap():
    a = AffiliateLink(code="a", keywords=["Alpha"], url="u", display_text="Alpha")
    b = AffiliateLink(code="b", keywords=["Bravo"], url="u", display_text="Bravo")
    out, injected = inject_affiliate_links("Alpha and Bravo.", [a, b], cap=1)
    assert injected == ["a"]


def test_skips_fenced_code_block():
    md = "```\nMercury\n```\nUse Mercury here."
    out, injected = inject_affiliate_links(md, [M])
    assert "```\nMercury\n```" in out
    assert out.count("[Mercury](/go/mercury)") == 1


def test_skips_inline_code():
    md = "Set `Mercury` env then use Mercury."
    out, _ = inject_affiliate_links(md, [M])
    assert "`Mercury`" in out
    assert "[Mercury](/go/mercury)" in out


def test_skips_headings():
    md = "## Mercury\nWe use Mercury daily."
    out, _ = inject_affiliate_links(md, [M])
    assert out.startswith("## Mercury\n")
    assert "[Mercury](/go/mercury)" in out


def test_skips_existing_link_and_is_idempotent():
    md = "See [Mercury](/go/mercury) here."
    out, injected = inject_affiliate_links(md, [M])
    assert injected == []
    assert out == md


def test_display_text_defaults_to_matched_keyword():
    link = AffiliateLink(code="c", keywords=["Cloudflare"], url="u")  # no display_text
    out, _ = inject_affiliate_links("We run Cloudflare here.", [link])
    assert "[Cloudflare](/go/c)" in out


def test_noop_on_empty_inputs():
    assert inject_affiliate_links("", [M]) == ("", [])
    assert inject_affiliate_links("text", []) == ("text", [])


def test_longer_keyword_phrase_preferred_over_shorter_substring():
    """"RTX 5090" and "5090" are DIFFERENT links' keywords (not shared) — the
    longer phrase must be tried first so it isn't half-consumed by the
    shorter one first."""
    generic = AffiliateLink(code="generic", keywords=["5090"], url="u1", display_text="Generic")
    gpu = AffiliateLink(code="gpu", keywords=["RTX 5090"], url="u2", display_text="GPU")
    out, injected = inject_affiliate_links("The RTX 5090 is fast.", [gpu, generic], cap=3)
    assert injected == ["gpu"]
    assert "[GPU](/go/gpu)" in out
    assert "generic" not in injected


def test_shared_keyword_resolves_via_cooccurrence():
    psu = AffiliateLink(code="psu", keywords=["Corsair", "HX1500i"], url="u1", display_text="PSU")
    headset = AffiliateLink(code="headset", keywords=["Corsair", "Virtuoso MAX"], url="u2", display_text="Headset")
    md = "I love my Corsair gear, especially the HX1500i."
    out, injected = inject_affiliate_links(md, [psu, headset], cap=3)
    assert injected == ["psu"]
    assert "/go/psu" in out
    assert "/go/headset" not in out


def test_shared_keyword_resolves_via_lru_when_no_cooccurrence():
    a = AffiliateLink(code="a", keywords=["Corsair"], url="u1", display_text="A")
    b = AffiliateLink(code="b", keywords=["Corsair"], url="u2", display_text="B")
    # "a" was used more recently than "b" -> "b" is least-recently-used -> wins
    last_used = {"a": "2026-07-10T00:00:00", "b": "2026-01-01T00:00:00"}
    out, injected = inject_affiliate_links("I love my Corsair gear.", [a, b], last_used=last_used)
    assert injected == ["b"]
    assert "[B](/go/b)" in out


def test_shared_keyword_lru_tie_breaks_via_injected_rng_when_both_unused():
    a = AffiliateLink(code="a", keywords=["Corsair"], url="u1")
    b = AffiliateLink(code="b", keywords=["Corsair"], url="u2")

    class _FixedRng:
        def choice(self, seq):
            return seq[-1]

    out, injected = inject_affiliate_links(
        "Corsair gear.", [a, b], last_used={}, rng=_FixedRng(),
    )
    assert injected == ["b"]


def test_shared_keyword_winner_injects_once_across_its_recurring_keywords():
    """A multi-keyword link that WINS a shared keyword still injects exactly
    once, even when the shared keyword and the link's own other keyword recur
    later in the post. The per-link ``used`` set plus per-keyword
    ``consumed_keywords`` guarantee one link => one injection across the whole
    catalog of its keywords; the losing link is never injected."""
    psu = AffiliateLink(code="psu", keywords=["Corsair", "HX1500i"], url="u1", display_text="PSU")
    headset = AffiliateLink(code="headset", keywords=["Corsair", "Virtuoso MAX"], url="u2", display_text="Headset")
    # "Corsair" is shared; psu is corroborated by its own "HX1500i" -> psu wins
    # the shared keyword. Both "Corsair" and "HX1500i" recur -> still one psu link.
    md = "My Corsair HX1500i rig. Another Corsair build reuses the HX1500i."
    out, injected = inject_affiliate_links(md, [psu, headset], cap=3)
    assert injected == ["psu"]
    assert out.count("/go/psu") == 1  # first mention only, across all its keywords
    assert "/go/headset" not in out  # the losing link is never injected
    assert out.count("[PSU](/go/psu)") == 1
