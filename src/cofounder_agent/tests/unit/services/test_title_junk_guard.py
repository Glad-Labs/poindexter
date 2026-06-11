"""Unit tests for the junk-title guard introduced in poindexter#1280.

Covers:
- Both real failure captures from 2026-06-09 are detected as junk.
- A normal, well-formed title passes through unchanged.
- The truncated-ending check fires on a comma-truncated heading.
- The length-cap check fires when the title exceeds title_max_length.
- The prefix-pattern checks fire for each known instructional prefix.
- The substring-pattern checks fire for each known substring.
- choose_canonical_title falls back to H1 / topic when junk is detected.
- choose_canonical_title respects the site_config title_max_length key.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services.title_generation import (
    _DEFAULT_TITLE_MAX_LENGTH,
    _is_junk_title,
    choose_canonical_title,
)


# ---------------------------------------------------------------------------
# Real failure captures (poindexter#1280 root-cause evidence)
# ---------------------------------------------------------------------------


def test_real_capture_b8b227c6_is_junk():
    """Task b8b227c6 (2026-06-09): LLM emitted instructional meta-text starting
    with 'Intent-Based:' — this must be rejected as junk."""
    junk = (
        "Intent-Based: They signal to the reader exactly what they will get "
        "(an introduction, a benefit, o..."
    )
    assert _is_junk_title(junk) is True


def test_real_capture_a94229fa_is_junk():
    """Task a94229fa (2026-06-09): LLM emitted 'SEO Keywords:' meta-text with
    a mid-sentence trailing ellipsis — must be rejected."""
    junk = (
        "SEO Keywords: They lead with high-volume terms like "
        "*\"Mastering Modern HTML5\"*…"
    )
    assert _is_junk_title(junk) is True


# ---------------------------------------------------------------------------
# Normal title passes unchanged
# ---------------------------------------------------------------------------


def test_normal_title_passes():
    """A clean, well-formed title must not be flagged as junk."""
    assert _is_junk_title("Why Rust Is the Future of Systems Programming") is False


def test_normal_title_with_colon_passes():
    """Interior colons (e.g. 'My Take: A Guide') are legitimate and must pass."""
    assert _is_junk_title("My Take: A Guide to Embeddings") is False


def test_short_normal_title_passes():
    """Even a short, complete title passes if it ends cleanly."""
    assert _is_junk_title("Rust vs Go") is False


# ---------------------------------------------------------------------------
# Truncated-ending check
# ---------------------------------------------------------------------------


def test_truncated_ending_comma_fires():
    """Title ending with ',' is a truncated tail and must be flagged."""
    assert _is_junk_title("Heading: Something that tails off,") is True


def test_truncated_ending_space_fires():
    """Title ending with a trailing space (after strip there's nothing) is OK
    only if the last non-whitespace char is clean — test a genuine truncation."""
    assert _is_junk_title("An incomplete title that ends with a hyphen-") is True


def test_truncated_ending_ellipsis_ascii_fires():
    """ASCII triple-dot ending is NOT in the clean-end set."""
    assert _is_junk_title("Intent-Based: They signal...") is True


def test_clean_ending_period_passes():
    assert _is_junk_title("The Complete Guide to Rust.") is False


def test_clean_ending_question_mark_passes():
    assert _is_junk_title("Is Rust the Future?") is False


def test_clean_ending_exclamation_passes():
    assert _is_junk_title("Rust Is Here!") is False


def test_clean_ending_quote_passes():
    assert _is_junk_title('Rust: "The Future"') is False


# ---------------------------------------------------------------------------
# Length cap
# ---------------------------------------------------------------------------


def test_length_cap_default_fires_at_91_chars():
    """Titles over the default 90-char cap must be flagged."""
    long_title = "A" * 91
    assert _is_junk_title(long_title) is True


def test_length_cap_default_passes_at_90_chars():
    """Exactly at the cap is fine."""
    title = "A" * 90
    assert _is_junk_title(title) is False


def test_length_cap_custom_respected():
    """A caller-supplied max_length is honoured over the module default."""
    title = "A" * 60  # Under default 90 but over custom cap of 50
    assert _is_junk_title(title, max_length=50) is True
    assert _is_junk_title(title, max_length=60) is False  # exactly at cap


# ---------------------------------------------------------------------------
# Prefix patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prefix",
    [
        "intent-based:",
        "Intent-Based:",
        "INTENT-BASED:",
        "seo keywords:",
        "SEO Keywords:",
        "heading:",
        "Heading:",
        "they signal",
        "They Signal",
        "they lead with",
        "They Lead With",
    ],
)
def test_known_prefixes_are_flagged(prefix: str):
    """Each known instructional prefix must trigger the guard."""
    title = prefix + " something else here"
    assert _is_junk_title(title) is True, f"Expected junk for prefix {prefix!r}"


# ---------------------------------------------------------------------------
# Substring patterns
# ---------------------------------------------------------------------------


def test_substring_they_signal_to_the_reader():
    """Substring ' they signal to the reader' must trigger the guard."""
    title = "Titles they signal to the reader what they get"
    assert _is_junk_title(title) is True


def test_substring_they_lead_with_high_volume():
    """Substring ' they lead with high-volume' must trigger the guard."""
    title = "Power words they lead with high-volume terms"
    assert _is_junk_title(title) is True


# ---------------------------------------------------------------------------
# choose_canonical_title falls back when junk is detected
# ---------------------------------------------------------------------------


def test_choose_falls_back_to_h1_when_llm_title_is_junk():
    """When the LLM title fires the junk guard, the H1 from the body is used."""
    junk_llm = (
        "Intent-Based: They signal to the reader exactly what they will get "
        "(an introduction, a benefit, o..."
    )
    content = "# Why Rust Is the Future of Systems Programming\n\nBody text."
    topic = "Rust programming"

    out = choose_canonical_title(topic, content, llm_title=junk_llm)
    assert out == "Why Rust Is the Future of Systems Programming"


def test_choose_falls_back_to_topic_when_llm_title_is_junk_and_no_h1():
    """When the LLM title is junk and there's no H1 in the body, the topic wins."""
    junk_llm = "SEO Keywords: They lead with high-volume terms like *\"Mastering Modern HTML5\"*…"
    content = "Body paragraph with no markdown heading."
    topic = "Modern HTML5 techniques"

    out = choose_canonical_title(topic, content, llm_title=junk_llm)
    # Falls back to the topic (with strip_qa_batch_suffix + strip_title_label applied)
    assert "html5" in out.lower() or "modern" in out.lower()


def test_choose_emits_warning_when_junk_guard_fires(caplog):
    """A WARNING must be emitted when the junk guard rejects an LLM title."""
    import logging

    junk_llm = (
        "Intent-Based: They signal to the reader exactly what they will get "
        "(an introduction, a benefit, o..."
    )
    topic = "Some Topic"
    content = "# Fallback H1 Title\n\nBody."

    with caplog.at_level(logging.WARNING, logger="services.title_generation"):
        choose_canonical_title(topic, content, llm_title=junk_llm)

    assert any(
        "junk" in rec.message.lower() for rec in caplog.records
    ), "Expected a WARNING about junk-guard rejection"


def test_choose_good_llm_title_passes_through_unchanged():
    """A clean LLM title must not be intercepted by the junk guard."""
    good_llm = "Why Rust Is the Future of Systems Programming"
    topic = "Rust programming"
    content = "# Different H1 That Should Lose\n\nBody."

    out = choose_canonical_title(topic, content, llm_title=good_llm)
    assert out == good_llm


# ---------------------------------------------------------------------------
# site_config title_max_length integration
# ---------------------------------------------------------------------------


def test_choose_reads_title_max_length_from_site_config():
    """When site_config provides a custom title_max_length, the junk guard
    uses that cap.  A title that would pass the default 90-char cap must be
    flagged when the DB cap is lower."""
    # 70 chars — below default 90, but above a custom cap of 60.
    borderline_title = "A" * 70
    topic = "Programming"
    content = "Body paragraph."

    fake_sc_strict = MagicMock()
    fake_sc_strict.get_int.return_value = 60  # tighter cap

    out_strict = choose_canonical_title(
        topic, content, llm_title=borderline_title, site_config=fake_sc_strict
    )
    # Junk guard should have fired and fallen back to topic fallback
    assert out_strict != borderline_title

    fake_sc_loose = MagicMock()
    fake_sc_loose.get_int.return_value = 90  # same as default — title should pass

    out_loose = choose_canonical_title(
        topic, content, llm_title=borderline_title, site_config=fake_sc_loose
    )
    assert out_loose == borderline_title


def test_choose_works_without_site_config():
    """choose_canonical_title must still work when site_config=None (the default).
    Existing callers that don't pass site_config must not break."""
    good_llm = "A Clean Title For Testing"
    out = choose_canonical_title("topic", "Body.", llm_title=good_llm)
    assert out == good_llm


def test_choose_handles_site_config_get_int_error_gracefully():
    """If site_config.get_int raises, the default cap is used rather than
    propagating the exception."""
    good_llm = "A Clean Title For Testing"
    topic = "topic"
    content = "Body."

    fake_sc = MagicMock()
    fake_sc.get_int.side_effect = RuntimeError("DB unavailable")

    # Should not raise; falls back to default 90-char cap.
    out = choose_canonical_title(topic, content, llm_title=good_llm, site_config=fake_sc)
    assert out == good_llm


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_is_junk_title_empty_string():
    """Empty string is not junk (handled elsewhere)."""
    assert _is_junk_title("") is False


def test_is_junk_title_whitespace_only():
    """Whitespace-only is not junk (choose_canonical_title filters it earlier)."""
    assert _is_junk_title("   ") is False


def test_default_title_max_length_constant():
    """The module default must be 90 to match the app_settings seed."""
    assert _DEFAULT_TITLE_MAX_LENGTH == 90
