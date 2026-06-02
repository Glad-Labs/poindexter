"""Unit tests for the cite-or-None type deriver."""
import pytest

from services.triage.derive_labels import derive_type


@pytest.mark.parametrize(
    "title,expected",
    [
        ("feat(content): more length variation", "feature"),
        ("fix(findings): authenticate gh in the worker", "bug"),
        ("harden(findings): don't let fallback suppress critical", "tech-debt"),
        ("refactor: collapse the dispatcher", "tech-debt"),
        ("chore(deps): bump httpx", "chore"),
        ("docs: update the runbook", "documentation"),
        ("test(qa): cover the edge case", "testing"),
        ("feat: scoped-less prefix still works", "feature"),
        ("FEAT(x): case-insensitive", "feature"),
    ],
)
def test_derive_type_from_prefix(title, expected):
    assert derive_type(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "",
        "No conventional prefix here",
        "wip(something): unknown prefix word",
        "Add a thing without a colon",
        "feat without parens or colon",
    ],
)
def test_derive_type_returns_none_when_no_basis(title):
    # cite-or-surface: no derivable basis -> None (leave bare), never a default.
    assert derive_type(title) is None
