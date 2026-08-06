"""Tests for the truncated-output detector + validator rule (poindexter#984).

A completion that hits its output-token cap is guillotined mid-token — the
text ends mid-word ("### Wha", "the wrong shape for th") or inside a
markdown/HTML structure (a URL severed mid-path). In 2026-07, 38% of QA
critic vetoes were exactly this, each burning a full LLM rail pass to
conclude what a string check proves for free.

Positive fixtures below are verbatim tails from the critic-vetoed truncated
drafts of 2026-07/08; negative fixtures mirror how the published corpus
actually ends (the rule set was tuned to 165/168 clean on the live corpus —
the 3 flags were genuinely truncated live posts).
"""

import pytest

from modules.content.content_validator import (
    detect_truncated_content,
    validate_content,
)
from services.site_config import SiteConfig

_SC = SiteConfig()


def _truncation_issues(result):
    return [i for i in result.issues if i.category == "truncated_content"]


@pytest.mark.unit
class TestDetectTruncatedContent:
    # --- positives: verbatim failure shapes from prod ---

    def test_mid_word_prose_cutoff(self):
        content = (
            "## Read Scaling\n\n"
            "Replicas solve read scaling, but they don't solve write "
            "scaling, and the wrong shape for th"
        )
        reasons = detect_truncated_content(content)
        assert reasons and "terminal punctuation" in reasons[0]

    def test_dangling_heading(self):
        content = "Intro paragraph ends fine.\n\n### Wha"
        reasons = detect_truncated_content(content)
        assert reasons and "heading" in reasons[0]

    def test_severed_markdown_link_url(self):
        content = (
            "See [a Unity Discussions thread]"
            "(https://discussions.unity.com/t/sa"
        )
        reasons = detect_truncated_content(content)
        assert reasons and "link URL" in reasons[0]

    def test_severed_markdown_link_text(self):
        content = "More reading in [the official guide to conte"
        reasons = detect_truncated_content(content)
        assert reasons and "link text" in reasons[0]

    def test_unclosed_code_fence(self):
        content = "Complete sentence here.\n\n```python\nprint('hi')\n"
        reasons = detect_truncated_content(content)
        assert any("code fence" in r for r in reasons)

    def test_unclosed_html_tag(self):
        # Live post the-hidden-cost-of-rigid-databases ended exactly here.
        content = 'Body text.\n\n<h3 style="color: #94a3b8; margin-bottom: 1e'
        reasons = detect_truncated_content(content)
        assert reasons and "HTML tag" in reasons[0]

    def test_mid_quote_cutoff(self):
        content = (
            "we stopped asking... in the same conversation where someone "
            "used to just say 'ye"
        )
        assert detect_truncated_content(content)

    # --- negatives: how finished posts actually end ---

    def test_terminal_period(self):
        assert detect_truncated_content("All done. The end.") == []

    def test_autolink_source_list(self):
        # The most common published ending: a sources list of autolinks.
        content = (
            "## Sources\n\n"
            "- <https://github.com/Glad-Labs/poindexter>\n"
            "- <https://www.liquibase.com/blog/database-drift>"
        )
        assert detect_truncated_content(content) == []

    def test_closed_code_fence(self):
        content = "Ends with a fence:\n\n```python\nx = 1\n```"
        assert detect_truncated_content(content) == []

    def test_table_row_ending(self):
        content = "| model | score |\n| --- | --- |\n| gemma | 82 |"
        assert detect_truncated_content(content) == []

    def test_emphasis_and_quote_endings(self):
        assert detect_truncated_content("The end, *emphasized*.") == []
        assert detect_truncated_content('She said "done."') == []

    def test_horizontal_rule_ending(self):
        assert detect_truncated_content("Final line.\n\n---") == []

    def test_markdown_link_ending(self):
        content = "Read more in [the docs](https://example.com/docs)"
        assert detect_truncated_content(content) == []

    def test_question_and_exclamation(self):
        assert detect_truncated_content("Ready to start?") == []
        assert detect_truncated_content("Ship it!") == []

    def test_empty_content_returns_no_reasons(self):
        assert detect_truncated_content("") == []
        assert detect_truncated_content("   \n\n  ") == []


@pytest.mark.unit
class TestValidateContentTruncationRule:
    def test_truncated_content_flags_critical_and_fails(self):
        # Body must clear the rule's 200-char minimum for the gate to look
        # at the tail at all.
        content = (
            "# Postgres Read Replicas\n\n"
            "## The Problem\n\n"
            "Read replicas are the default first move when a Postgres "
            "primary starts to buckle under read traffic, and for good "
            "reason: they are cheap to stand up and well understood.\n\n"
            "Replicas solve read scaling, but they don't solve write "
            "scaling, and the wrong shape for th"
        )
        result = validate_content(
            "Postgres Read Replicas", content, "postgres", site_config=_SC
        )
        issues = _truncation_issues(result)
        assert issues, "expected a truncated_content issue"
        assert issues[0].severity == "critical"
        assert "output-token cap" in issues[0].description
        assert result.passed is False

    def test_complete_content_not_flagged(self):
        # Also >200 chars so the negative is meaningful, not gated away.
        content = (
            "# FastAPI Basics\n\n"
            "## Why It Is Fast\n\n"
            "It builds on Starlette and Pydantic for async performance, "
            "and the type-hint-driven request validation means endpoints "
            "document themselves through the generated OpenAPI schema "
            "without any extra annotation work from the author.\n\n"
            "## Sources\n\n"
            "- <https://fastapi.tiangolo.com>"
        )
        result = validate_content(
            "FastAPI Basics", content, "FastAPI", site_config=_SC
        )
        assert _truncation_issues(result) == []
