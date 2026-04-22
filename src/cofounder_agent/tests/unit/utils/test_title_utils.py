"""Unit tests for title_utils (issue GH-85).

Covers:
* emoji strip over the common Unicode blocks
* word-boundary truncation — never mid-word, ≤ max_len
* derive_seo_title combining both rules with the 60-char cap
* extract_body_h1 / replace_body_h1 handle missing/multi-heading bodies
* propagate_canonical_title produces the consistent triple (title, seo_title, body)
"""


from utils.title_utils import (
    DEFAULT_SEO_TITLE_MAX_LEN,
    derive_seo_title,
    extract_body_h1,
    propagate_canonical_title,
    replace_body_h1,
    strip_emoji,
    truncate_at_word_boundary,
)

# ---------------------------------------------------------------------------
# strip_emoji
# ---------------------------------------------------------------------------


class TestStripEmoji:
    def test_removes_magnifying_glass(self):
        # The exact bug from issue GH-85 — task f4965103
        assert (
            strip_emoji("Forem Architecture: Powering DEV & Beyond 🔍")
            == "Forem Architecture: Powering DEV & Beyond"
        )

    def test_removes_rocket(self):
        assert strip_emoji("🚀 Ship it fast") == "Ship it fast"

    def test_removes_multiple_emoji(self):
        assert strip_emoji("AI 🤖 + Code 💻 = ❤️") == "AI + Code ="

    def test_removes_composite_emoji(self):
        # ZWJ sequences (person + laptop, family, etc.)
        assert strip_emoji("Developer 👨‍💻 life") == "Developer life"

    def test_removes_flag(self):
        assert strip_emoji("US 🇺🇸 tech policy") == "US tech policy"

    def test_preserves_ampersand_and_punctuation(self):
        # GH-85 explicitly wants '&', ':', '-' preserved
        assert strip_emoji("AI & ML: A Deep-Dive") == "AI & ML: A Deep-Dive"

    def test_returns_empty_on_empty(self):
        assert strip_emoji("") == ""

    def test_returns_text_without_emoji_unchanged(self):
        assert (
            strip_emoji("Python asyncio event loop internals for developers")
            == "Python asyncio event loop internals for developers"
        )

    def test_none_passes_through(self):
        assert strip_emoji(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# truncate_at_word_boundary
# ---------------------------------------------------------------------------


class TestTruncateAtWordBoundary:
    def test_short_text_unchanged(self):
        assert truncate_at_word_boundary("Short title", 100) == "Short title"

    def test_exact_length_unchanged(self):
        text = "x" * 60
        assert truncate_at_word_boundary(text, 60) == text

    def test_truncation_never_mid_word(self):
        # The exact bug pattern — "About Custom AI Models" cut to "About C"
        long = "Everything You Need To Know About Custom AI Models Today"
        out = truncate_at_word_boundary(long, 40)
        assert len(out) <= 40
        # Every word in ``out`` must be a complete prefix-word from ``long``
        words_out = out.split()
        words_in = long.split()
        for i, w in enumerate(words_out):
            assert w == words_in[i], f"word {i} mid-cut: {w!r}"

    def test_truncation_strips_trailing_comma(self):
        # Window = first 15 chars = "One, two, three"; last space at idx 9
        # → "One, two," then trim trailing ',' → "One, two"
        assert truncate_at_word_boundary("One, two, three, four, five", 15) == "One, two"

    def test_truncation_strips_trailing_colon(self):
        # Window = first 10 chars = "Title: A S"; last space at idx 8
        # → "Title: A" (valid word boundary)
        assert truncate_at_word_boundary("Title: A Subtitle Extension", 10) == "Title: A"

    def test_truncation_strips_trailing_em_dash(self):
        # Window = 15 chars "One two — three"; last space before '—' at idx 7
        # → "One two —" then trim trailing '—' → "One two"
        assert truncate_at_word_boundary("One two — three four five", 15) == "One two"

    def test_zero_max_len(self):
        assert truncate_at_word_boundary("Anything", 0) == ""

    def test_none_passes_through(self):
        assert truncate_at_word_boundary(None, 10) is None  # type: ignore[arg-type]

    def test_single_huge_word_falls_back_to_slice(self):
        # Edge case: no whitespace at all — bounded slice is the safest fallback.
        out = truncate_at_word_boundary("supercalifragilisticexpialidocious", 10)
        assert len(out) <= 10

    def test_never_exceeds_max_len_fuzz(self):
        # Real-world SEO title cap — try a bunch of lengths/texts.
        samples = [
            "Everything You Need To Know About Custom AI Models Today",
            "Python asyncio event loop internals for developers explained",
            "The Synthetic Music Surge: What Deezer's 44% AI Upload Rate Means for the Industry",
            "AI Security: Kubernetes Pod Standards for Workloads Running in Production",
        ]
        for sample in samples:
            for cap in (30, 45, 60, 70):
                out = truncate_at_word_boundary(sample, cap)
                assert len(out) <= cap, f"{sample!r} @ {cap} → {out!r} ({len(out)})"
                if out and " " in sample:
                    # The returned text (possibly with trailing punctuation
                    # trimmed back) must be a *prefix* of ``sample`` ending
                    # on a word boundary — i.e. ``sample`` must either equal
                    # ``out`` exactly or continue with whitespace or the
                    # trimmed trailing punctuation.
                    if sample == out:
                        continue
                    # Allow ``out`` plus any trimmed trailing punctuation from
                    # the trim set to be a prefix of ``sample``, followed by
                    # whitespace (word boundary).
                    matched = False
                    for trim_suffix in ("", ",", ";", ":", "-", "—", "–"):
                        candidate = out + trim_suffix
                        if sample.startswith(candidate + " ") or sample == candidate:
                            matched = True
                            break
                    assert matched, (
                        f"truncation not at word boundary: {out!r} from {sample!r}"
                    )


# ---------------------------------------------------------------------------
# derive_seo_title
# ---------------------------------------------------------------------------


class TestDeriveSeoTitle:
    def test_strips_emoji_and_caps_at_60(self):
        # Canonical with emoji that's also over 60 chars
        canonical = (
            "The Synthetic Music Surge: What Deezer's 44% AI Upload Rate Means 🎵"
        )
        out = derive_seo_title(canonical)
        assert "🎵" not in out
        assert len(out) <= DEFAULT_SEO_TITLE_MAX_LEN

    def test_no_mid_word_truncation(self):
        canonical = "Everything You Need To Know About Custom AI Models"
        out = derive_seo_title(canonical, max_len=30)
        # Must not end with a half-word like "About C"
        assert not out.endswith("C")
        assert " C" not in out + " "  # sanity — ensure no lone 'C'
        # Every word fully present in canonical
        for w in out.split():
            assert w in canonical

    def test_short_title_passes_through(self):
        assert derive_seo_title("Short & Punchy") == "Short & Punchy"

    def test_empty_string_returns_empty(self):
        assert derive_seo_title("") == ""


# ---------------------------------------------------------------------------
# extract_body_h1
# ---------------------------------------------------------------------------


class TestExtractBodyH1:
    def test_basic_h1(self):
        assert extract_body_h1("# My Title\n\nBody") == "My Title"

    def test_h1_with_trailing_whitespace(self):
        assert extract_body_h1("#   Padded Title   \n\nBody") == "Padded Title"

    def test_no_h1_returns_none(self):
        assert extract_body_h1("No heading here, just prose.") is None

    def test_h2_is_not_h1(self):
        assert extract_body_h1("## Only an H2\n\nBody") is None

    def test_empty_returns_none(self):
        assert extract_body_h1("") is None
        assert extract_body_h1(None) is None  # type: ignore[arg-type]

    def test_skips_leading_blank_lines(self):
        assert extract_body_h1("\n\n# After Blanks\n\nBody") == "After Blanks"

    def test_only_first_h1_extracted(self):
        content = "# First Title\n\nBody.\n\n# Second H1 (weird)"
        assert extract_body_h1(content) == "First Title"

    def test_h1_with_emoji(self):
        assert extract_body_h1("# Ship It 🚀\n\nBody") == "Ship It 🚀"

    def test_hash_inside_code_block_not_picked(self):
        # Real GH-85 false-positive: '.github/workflows/cost-report.yml' got
        # picked as canonical because it was a header inside a ``` block.
        content = (
            "## Introduction\n\n"
            "Here's a file path:\n\n"
            "```yaml\n"
            "# .github/workflows/cost-report.yml\n"
            "name: cost-report\n"
            "```\n\n"
            "And the real H1 comes later:\n\n"
            "# The Real Title\n\n"
            "Body."
        )
        assert extract_body_h1(content) == "The Real Title"

    def test_no_h1_when_only_in_fence(self):
        content = (
            "Regular paragraph.\n\n"
            "```bash\n"
            "# Download quantized model\n"
            "curl ...\n"
            "```\n"
        )
        assert extract_body_h1(content) is None

    def test_tilde_fence_also_ignored(self):
        content = (
            "prose\n\n"
            "~~~\n"
            "# not-a-title\n"
            "~~~\n\n"
            "# Real One"
        )
        assert extract_body_h1(content) == "Real One"

    def test_h2_with_hash_not_confused_with_h1(self):
        # Make sure ``## H2`` doesn't match (strict H1 only).
        assert extract_body_h1("## H2 first\n\n# H1 second") == "H1 second"


# ---------------------------------------------------------------------------
# replace_body_h1
# ---------------------------------------------------------------------------


class TestReplaceBodyH1:
    def test_replaces_first_h1(self):
        content = "# Old Title\n\nBody text.\n\n## Section"
        out, replaced = replace_body_h1(content, "New Title")
        assert replaced is True
        assert "# New Title" in out
        assert "# Old Title" not in out
        assert "## Section" in out  # H2 untouched

    def test_returns_false_when_no_h1(self):
        content = "No heading\n\n## Only H2"
        out, replaced = replace_body_h1(content, "New Title")
        assert replaced is False
        assert out == content

    def test_only_first_h1_replaced(self):
        content = "# One\n\nBody.\n\n# Two"
        out, replaced = replace_body_h1(content, "New")
        assert replaced is True
        assert out.count("# New") == 1
        assert "# Two" in out

    def test_empty_content_returns_false(self):
        out, replaced = replace_body_h1("", "New")
        assert replaced is False


# ---------------------------------------------------------------------------
# propagate_canonical_title (the end-to-end contract)
# ---------------------------------------------------------------------------


class TestPropagateCanonicalTitle:
    def test_three_way_consistency(self):
        canonical = "Beyond Threads: A Deep Dive into Python's asyncio Event Loop"
        content = "# Old editorial title\n\nParagraph one.\n\n## Section\n\nMore."

        title, seo_title, new_content = propagate_canonical_title(canonical, content)

        # 1. title column = canonical (emoji stripped, but no emoji here)
        assert title == canonical
        # 2. seo_title = truncated at word boundary
        assert len(seo_title) <= DEFAULT_SEO_TITLE_MAX_LEN
        assert seo_title  # not empty
        for word in seo_title.split():
            assert word in canonical
        # 3. body H1 = canonical verbatim
        assert new_content.startswith(f"# {canonical}")
        assert "# Old editorial title" not in new_content

    def test_strips_emoji_from_title_and_seo_but_keeps_in_body(self):
        canonical = "Forem Architecture: Powering DEV & Beyond 🔍"
        content = "# Body keeps 🔍 emoji\n\nParagraph."

        title, seo_title, new_content = propagate_canonical_title(canonical, content)

        assert "🔍" not in title
        assert "🔍" not in seo_title
        # Body H1 is replaced with canonical (keeps emoji per contract).
        assert "🔍" in new_content
        assert new_content.startswith("# Forem Architecture: Powering DEV & Beyond 🔍")

    def test_prepends_h1_when_missing(self):
        canonical = "A Fresh Title"
        content = "No heading yet. Just prose.\n\n## H2 here"

        title, seo_title, new_content = propagate_canonical_title(canonical, content)

        assert title == canonical
        assert new_content.startswith("# A Fresh Title\n\n")
        assert "No heading yet" in new_content
        assert "## H2 here" in new_content

    def test_idempotent(self):
        canonical = "Stable Title"
        content = "# Other Title\n\nBody"

        first = propagate_canonical_title(canonical, content)
        second = propagate_canonical_title(canonical, first[2])
        # Running twice yields the same result.
        assert first == second

    def test_long_canonical_truncates_seo_safely(self):
        # Real examples from GH-85 — mid-word cuts were the bug.
        canonical = "Everything You Need To Know About Custom AI Models Today"
        title, seo_title, _new = propagate_canonical_title(
            canonical, "# x\n\nBody"
        )
        # seo_title capped, never mid-word.
        assert len(seo_title) <= DEFAULT_SEO_TITLE_MAX_LEN
        last_word = seo_title.rstrip(",;:-—–").rstrip().split()[-1]
        assert last_word in canonical.split(), f"mid-word cut: {last_word!r}"
        # title column: canonical verbatim (emoji-free, which it already is).
        assert title == canonical

    def test_none_content_returns_none_content(self):
        title, seo_title, out_content = propagate_canonical_title("X Title", None)  # type: ignore[arg-type]
        assert title == "X Title"
        assert seo_title == "X Title"
        assert out_content is None
