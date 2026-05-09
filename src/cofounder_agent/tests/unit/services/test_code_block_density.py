"""Unit tests for the code-block density quality gate (GH-234).

Verifies that ``services.content_validator.validate_content`` emits a
``code_block_density`` warning when:

* The post is tagged with one of the configured tech tags, AND
* The fenced-code-block density is below the configured threshold.

Also verifies the negative cases — non-tech posts, posts with enough
code, and the global kill-switch — all skip the gate cleanly.

Tests mutate ``services.content_validator.site_config._config`` directly. The
unit-test conftest snapshots + restores that dict between tests
(layer 3 of ``tests/unit/conftest.py``), so per-test seeds don't leak.
"""

from __future__ import annotations

import pytest

from services.content_validator import (
    _check_code_block_density,
    _count_code_blocks_and_lines,
    _is_tech_post,
    validate_content,
)
import services.site_config as _site_config_mod
site_config = _site_config_mod.site_config


# ---------------------------------------------------------------------------
# Test-local helpers
# ---------------------------------------------------------------------------


_TECH_TAGS_DEFAULT = "technical,ai,programming,ml,python,javascript,rust,go"


def _seed_density_settings(
    *,
    enabled: bool = True,
    tag_filter: str = _TECH_TAGS_DEFAULT,
    min_blocks_per_700w: int = 1,
    min_line_ratio_pct: int = 20,
    long_post_floor_words: int = 300,
) -> None:
    """Push the GH-234 settings into the site_config singleton.

    The autouse fixture in ``tests/unit/conftest.py`` resets these
    between tests, so each test starts from a clean baseline.
    """
    site_config._config["code_density_check_enabled"] = "true" if enabled else "false"
    site_config._config["code_density_tag_filter"] = tag_filter
    site_config._config["code_density_min_blocks_per_700w"] = str(min_blocks_per_700w)
    site_config._config["code_density_min_line_ratio_pct"] = str(min_line_ratio_pct)
    site_config._config["code_density_long_post_floor_words"] = str(long_post_floor_words)


def _prose_post(word_count: int) -> str:
    """Generate a prose body of ~word_count words with no code blocks."""
    sentence = (
        "Docker provides isolated environments for applications and lets "
        "you ship reproducible builds with predictable dependencies. "
    )
    # ~17 words per sentence — repeat enough to clear the requested target.
    repeats = max(1, (word_count // 17) + 1)
    return (sentence * repeats).strip()


def _post_with_blocks(prose_words: int, num_blocks: int, lines_per_block: int = 4) -> str:
    """Build a post with ``num_blocks`` fenced code blocks of given size."""
    body = _prose_post(prose_words)
    snippets = []
    for i in range(num_blocks):
        snippet_lines = "\n".join(f"print({i!r}, {j})" for j in range(lines_per_block))
        snippets.append(f"```python\n{snippet_lines}\n```")
    return body + "\n\n" + "\n\n".join(snippets) + "\n"


# ---------------------------------------------------------------------------
# Helper-function-level tests
# ---------------------------------------------------------------------------


class TestCountCodeBlocks:
    def test_no_blocks(self):
        blocks, code_lines, total = _count_code_blocks_and_lines("Just prose here.\nMore prose.")
        assert blocks == 0
        assert code_lines == 0
        assert total == 2

    def test_single_well_formed_block(self):
        content = "Intro line.\n```python\nprint('hi')\nprint('bye')\n```\nOutro."
        blocks, code_lines, total = _count_code_blocks_and_lines(content)
        assert blocks == 1
        assert code_lines == 2
        assert total == 4  # 'Intro line.', 2 code lines, 'Outro.' — fence lines excluded

    def test_unterminated_fence_still_counts(self):
        # A writer-intent block at EOF without a closer should still tally.
        content = "Intro.\n```python\nprint('hi')\n"
        blocks, _, _ = _count_code_blocks_and_lines(content)
        assert blocks == 1

    def test_tilde_fence_supported(self):
        content = "Intro.\n~~~\nls -la\n~~~\nOutro."
        blocks, code_lines, _ = _count_code_blocks_and_lines(content)
        assert blocks == 1
        assert code_lines == 1

    def test_empty_content(self):
        assert _count_code_blocks_and_lines("") == (0, 0, 0)
        assert _count_code_blocks_and_lines(None) == (0, 0, 0)  # type: ignore[arg-type]


class TestIsTechPost:
    def test_matches_explicit_tag(self):
        assert _is_tech_post(["python"], "", {"python", "go"}) is True

    def test_matches_tag_after_split(self):
        # "ai/ml" should split into "ai" and "ml" and match either token
        assert _is_tech_post(["AI/ML"], "", {"ai", "go"}) is True

    def test_matches_via_topic_when_no_tags(self):
        assert _is_tech_post([], "Python performance tips", {"python"}) is True

    def test_no_match_returns_false(self):
        assert _is_tech_post(["cooking"], "sourdough", {"python", "go"}) is False

    def test_empty_tech_tags_returns_false(self):
        # Empty tag list disables the gate entirely
        assert _is_tech_post(["python"], "python", set()) is False


# ---------------------------------------------------------------------------
# Acceptance-criteria tests (issue spec)
# ---------------------------------------------------------------------------


class TestCodeBlockDensityGate:
    """The four scenarios called out explicitly in the implementation plan."""

    def test_tech_post_with_too_few_blocks_warns(self):
        _seed_density_settings()
        # 700-word post with zero code blocks — should emit at least one
        # code_block_density warning.
        body = _prose_post(700)
        result = validate_content(
            "Why FastAPI Beats Flask for Async Workloads",
            body,
            topic="python",
            tags=["python", "programming"],
        )
        density_issues = [i for i in result.issues if i.category == "code_block_density"]
        assert density_issues, "Expected a code_block_density warning for prose-only tech post"
        assert all(i.severity == "warning" for i in density_issues), (
            "code_block_density must be warning-level only — never critical"
        )

    def test_tech_post_with_enough_blocks_passes_density_gate(self):
        _seed_density_settings()
        # 700 words + 2 code blocks of 12 lines each = enough blocks AND
        # enough code-line ratio to clear both sub-checks.
        body = _post_with_blocks(prose_words=700, num_blocks=2, lines_per_block=12)
        result = validate_content(
            "Hands-on Async Python with FastAPI",
            body,
            topic="python",
            tags=["python", "programming"],
        )
        density_issues = [i for i in result.issues if i.category == "code_block_density"]
        assert not density_issues, (
            "Tech post with sufficient code blocks should not trigger the "
            f"density gate; got: {[i.description for i in density_issues]}"
        )

    def test_non_tech_post_skips_gate_regardless_of_code(self):
        _seed_density_settings()
        # Long prose-only post tagged with a non-tech topic — gate must
        # not fire even though density is technically zero.
        body = _prose_post(800)
        result = validate_content(
            "How to Brew the Perfect Cup of Pour-Over Coffee",
            body,
            topic="cooking",
            tags=["coffee", "lifestyle"],
        )
        density_issues = [i for i in result.issues if i.category == "code_block_density"]
        assert not density_issues, (
            "Non-tech post must not trigger code_block_density warning"
        )

    def test_kill_switch_disables_gate(self):
        _seed_density_settings(enabled=False)
        body = _prose_post(900)
        result = validate_content(
            "Why FastAPI Beats Flask for Async Workloads",
            body,
            topic="python",
            tags=["python", "programming"],
        )
        density_issues = [i for i in result.issues if i.category == "code_block_density"]
        assert not density_issues, (
            "code_density_check_enabled=false must disable the gate"
        )


class TestDensityGateEdgeCases:
    """Boundary conditions that aren't strictly in the four-bullet plan
    but are easy to break and would erode trust in the gate."""

    def test_short_tech_post_skips_blocks_subcheck(self):
        # Posts under 200 prose words shouldn't trip the per-700w floor —
        # a 50-word note doesn't need a snippet.
        _seed_density_settings()
        body = _prose_post(50)
        result = validate_content(
            "Quick Python tip",
            body,
            topic="python",
            tags=["python"],
        )
        density_issues = [i for i in result.issues if i.category == "code_block_density"]
        assert not density_issues

    def test_short_post_skips_line_ratio_subcheck(self):
        # The line-ratio sub-check is gated on
        # ``code_density_long_post_floor_words`` (default 300). A 250-word
        # post with no code should NOT trigger the ratio warning, only
        # potentially the blocks-per-700w warning (which kicks in at 200).
        _seed_density_settings()
        body = _prose_post(250)
        result = validate_content(
            "FastAPI quickstart",
            body,
            topic="python",
            tags=["python"],
        )
        ratio_issues = [
            i for i in result.issues
            if i.category == "code_block_density"
            and "ratio" in i.description.lower()
        ]
        assert not ratio_issues, (
            "Line-ratio sub-check must skip posts under "
            "code_density_long_post_floor_words"
        )

    def test_empty_tag_filter_disables_gate(self):
        # Operator can soft-disable by emptying the tag list without
        # touching the master flag.
        _seed_density_settings(tag_filter="")
        body = _prose_post(800)
        result = validate_content(
            "Distributed Systems Patterns",
            body,
            topic="python",
            tags=["python"],
        )
        density_issues = [i for i in result.issues if i.category == "code_block_density"]
        assert not density_issues

    def test_helper_returns_empty_list_when_disabled(self):
        # Belt-and-suspenders: the internal helper itself must short-circuit
        # so callers that bypass validate_content() still get correct behavior.
        _seed_density_settings(enabled=False)
        issues = _check_code_block_density(
            _prose_post(800), "python", ["python"]
        )
        assert issues == []

    def test_density_warning_never_blocks_post(self):
        # Re-asserts the spec's "warning, not hard veto" guarantee: a
        # density warning alone must not cause result.passed to flip to
        # False. (Other rules can still fail the post for other reasons.)
        _seed_density_settings()
        body = _prose_post(800)
        result = validate_content(
            "Distributed Systems Patterns",
            body,
            topic="python",
            tags=["python"],
        )
        density_issues = [i for i in result.issues if i.category == "code_block_density"]
        assert density_issues, "Sanity: should have triggered the gate"
        # passed depends on whether OTHER rules fired — but density alone
        # is warning-level, so density warnings shouldn't drive critical_count.
        density_critical = [
            i for i in density_issues if i.severity == "critical"
        ]
        assert not density_critical
