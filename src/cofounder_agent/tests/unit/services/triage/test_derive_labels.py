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


def test_derive_type_accepts_none_input():
    # None is a valid sentinel from callers that haven't set a title yet.
    assert derive_type(None) is None


def test_derive_type_whitespace_only():
    # Whitespace-only titles match ^\s* then fail on [A-Za-z]+, so no basis.
    assert derive_type("   ") is None


@pytest.mark.parametrize(
    "title,expected",
    [
        # bug is a first-class prefix in _PREFIX_TYPE, not just an alias for fix
        ("bug(auth): null pointer on token refresh", "bug"),
        # perf maps to tech-debt (optimisation work is debt reduction)
        ("perf(cache): avoid repeated DB round-trips", "tech-debt"),
    ],
)
def test_derive_type_untested_known_prefixes(title, expected):
    assert derive_type(title) == expected


@pytest.mark.parametrize(
    "title,expected",
    [
        # Breaking-change bang with scope
        ("feat(api)!: remove deprecated endpoint", "feature"),
        # Breaking-change bang without scope
        ("fix!: change error response shape", "bug"),
    ],
)
def test_derive_type_breaking_change_bang(title, expected):
    # The regex's !? makes the exclamation mark optional; type derives normally.
    assert derive_type(title) == expected


def test_derive_type_leading_whitespace():
    # The regex starts with \s* so indented CC titles (copy-paste artefacts) still resolve.
    assert derive_type("  feat(ui): add dark-mode toggle") == "feature"
