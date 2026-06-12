"""clean_title hygiene for SEO titles (SEO Harvest Loop #763 follow-up).

Regression for the validation run that produced
`How Indie Hackers Actually Make Money" Beyond Micro-Saas &` — an embedded
quote survived (clean_oneline only strips *surrounding* quotes) and a 60-char
word-boundary clip left a dangling `&` (derive_seo_title, unlike clamp_words,
doesn't drop trailing punctuation). clean_title is the title twin of clamp_words.
"""

from modules.content.atoms import _seo_common as sc


def test_clean_title_strips_embedded_quote_and_trailing_ampersand():
    raw = 'How Indie Hackers Actually Make Money" Beyond Micro-SaaS & Acquisition Plays'
    out = sc.clean_title(raw, 60)
    assert '"' not in out, "embedded quote must be removed"
    assert not out.rstrip().endswith("&"), "must not end on a dangling &"
    assert len(out) <= 60
    # the trailing token must not be a dangling conjunction/preposition
    assert out.split()[-1].lower() not in {
        "and", "or", "&", "the", "a", "an", "to", "for", "with", "of", "in", "on", "vs",
    }
    assert out.startswith("How Indie Hackers Actually Make Money")


def test_clean_title_leaves_short_clean_title_untouched():
    # Not truncated → a legitimate short title is returned verbatim.
    raw = "How Indie Hackers Actually Make Money in 2026"
    assert sc.clean_title(raw, 60) == raw


def test_clean_title_does_not_strip_trailing_word_when_not_truncated():
    # "For" is a legitimate ending when the title fits; only a truncated clip
    # may drop a dangling conjunction.
    raw = "What to Look For"
    assert sc.clean_title(raw, 60) == "What to Look For"


def test_clean_title_preserves_apostrophes():
    # Only double-quote artifacts are stripped; contraction apostrophes stay.
    raw = "A Hacker's Guide to Revenue"
    out = sc.clean_title(raw, 60)
    assert "Hacker's" in out


def test_clean_title_drops_trailing_symbol_even_when_not_truncated():
    raw = "Indie Hacker Revenue -"
    out = sc.clean_title(raw, 60)
    assert out == "Indie Hacker Revenue"
