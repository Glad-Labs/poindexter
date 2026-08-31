"""Tests for ``scripts/lib_readme_stats.py``.

The README's marketing stats were hand-typed and every one of them rotted:
by 2026-08-31 the intro claimed 166 live posts against a real 198 and the
badge claimed 11,400+ tests against ~17,200. This module is what puts them
on the same anchored-regex mechanism CLAUDE.md's counts already ride, with
two deliberate differences the tests below pin:

* claims are floored to a round ``N+``, so they understate rather than
  overstate and only churn on a threshold crossing, and
* a pattern that matches nothing is reported, never treated as
  "already correct" (#2832).
"""

from __future__ import annotations

import pytest
import scripts.lib_readme_stats as lib  # type: ignore[import-not-found]  # repo-root namespace pkg via pytest pythonpath

pytestmark = pytest.mark.unit


class TestFloored:
    """The claim must never be able to overstate reality between syncs."""

    @pytest.mark.parametrize(
        ("value", "step", "expected"),
        [
            (198, 10, "190+"),
            (372, 10, "370+"),
            (2_074, 1_000, "2,000+"),
            (1_677, 100, "1,600+"),
            (17_238, 1_000, "17,000+"),
            # exact multiples stay put rather than jumping a step
            (200, 10, "200+"),
            (17_000, 1_000, "17,000+"),
        ],
    )
    def test_rounds_down_with_thousands_separators(self, value, step, expected):
        assert lib.floored(value, step) == expected

    @pytest.mark.parametrize("value", [198, 372, 2_074, 1_677, 17_238, 5, 0])
    @pytest.mark.parametrize("step", [10, 100, 1_000])
    def test_never_claims_more_than_reality(self, value, step):
        """The whole point of flooring: a stale claim stays TRUE."""
        claimed = int(lib.floored(value, step).rstrip("+").replace(",", ""))
        assert claimed <= value

    def test_below_the_step_floors_to_zero_rather_than_lying(self):
        assert lib.floored(7, 10) == "0+"

    def test_zero_step_is_rejected_not_silently_divided(self):
        with pytest.raises(ValueError):
            lib.floored(198, 0)


class TestShieldEscape:
    """shields.io reads `-` as a field separator and needs `,` and `+`
    percent-encoded, so the badge value cannot be passed through raw."""

    def test_encodes_comma_and_plus(self):
        assert lib.shield_escape("17,000+") == "17%2C000%2B"

    def test_encodes_a_literal_percent_first(self):
        """`%` must be escaped before the others, or `%2C` would re-encode."""
        assert lib.shield_escape("50%+") == "50%25%2B"

    def test_plain_value_is_untouched(self):
        assert lib.shield_escape("900") == "900"


class TestSubstituteAnchored:
    def test_rewrites_and_reports_each_match(self):
        text = "we have 5 live posts and 3 tests"
        new, changes = lib.substitute_anchored(text, [
            ("posts", r"\d+ live posts", "190+ live posts"),
            ("tests", r"\d+ tests", "17,000+ tests"),
        ])
        assert new == "we have 190+ live posts and 17,000+ tests"
        assert changes == ["posts ->190+ live posts", "tests ->17,000+ tests"]

    def test_only_the_first_occurrence_of_a_pattern_is_rewritten(self):
        new, _ = lib.substitute_anchored(
            "5 tests here, 5 tests there",
            [("t", r"\d+ tests", "9 tests")],
        )
        assert new == "9 tests here, 5 tests there"

    def test_already_current_text_is_a_no_op_with_no_change_entry(self):
        """Idempotence: the nightly must not open a PR on an unchanged file."""
        text = "190+ live posts"
        new, changes = lib.substitute_anchored(
            text, [("posts", r"[\d,]+\+? live posts", "190+ live posts")]
        )
        assert new == text
        assert changes == []

    def test_dead_anchor_yields_a_warning_not_silence(self):
        """#2832: zero matches means the prose was reworded and the claim
        stopped syncing. Reporting it as "already correct" is how a frozen
        number keeps reading as current."""
        text = "we published a number of posts"
        new, changes = lib.substitute_anchored(
            text, [("live_posts", r"\d+ live posts", "190+ live posts")]
        )
        assert new == text
        assert len(changes) == 1
        assert lib.is_warning(changes[0])
        assert "live_posts" in changes[0]

    def test_a_dead_anchor_does_not_stop_the_surviving_ones(self):
        new, changes = lib.substitute_anchored("5 tests", [
            ("dead", r"\d+ live posts", "190+ live posts"),
            ("tests", r"\d+ tests", "17,000+ tests"),
        ])
        assert new == "17,000+ tests"
        assert [lib.is_warning(c) for c in changes] == [True, False]

    def test_replacement_backslashes_are_literal_not_backreferences(self):
        """A value containing `\\1` must land as text, not expand a group."""
        new, _ = lib.substitute_anchored(
            "value: X", [("v", r"(X)", r"\1 and \\ raw")]
        )
        assert new == r"value: \1 and \\ raw"


class TestFloorSteps:
    """The step table is the churn/accuracy trade-off; a step of 1 would put
    the README back to opening a docs PR every single night."""

    def test_every_step_is_a_meaningful_round_number(self):
        assert set(lib.FLOOR_STEPS) == {
            "live_posts",
            "total_posts",
            "pipeline_tasks",
            "app_settings",
            "test_functions",
        }
        assert all(step >= 10 for step in lib.FLOOR_STEPS.values())


def test_readme_target_points_at_the_repo_root_readme():
    assert lib.README_MD.name == "README.md"
    assert (lib.README_MD.parent / "CLAUDE.md").exists() or not lib.README_MD.exists()
