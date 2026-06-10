"""Unit tests for utils.text_utils.strip_title_label (#728)."""

import pytest

from utils.text_utils import strip_title_label


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Title: Best Eats in the Northeast", "Best Eats in the Northeast"),
        ("TITLE: Foo Bar", "Foo Bar"),
        ("  title:   Spaced  ", "Spaced"),
        ("Headline: Big News", "Big News"),
        ("Title: Headline: Nested", "Nested"),
        # No label -> unchanged.
        ("Best Eats", "Best Eats"),
        # Hyphen compound must not be mistaken for a label.
        ("Title-Case Guide", "Title-Case Guide"),
        # Interior colon preserved (not a leading label).
        ("My Take: A Practical Guide", "My Take: A Practical Guide"),
        # "Title" as a prefix of a real word is not a label.
        ("Titles: A Roundup", "Titles: A Roundup"),
        ("", ""),
        (None, ""),
    ],
)
def test_strip_title_label(raw, expected):
    assert strip_title_label(raw) == expected
