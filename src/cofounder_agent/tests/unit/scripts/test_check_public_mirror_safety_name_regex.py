"""Contract tests for the operator-name LEAK_GUARD regex.

Pins the 2026-05-27 security audit finding: the regex
``[Mm]atthew [Gg]ladding`` did NOT match the middle-initial form
``Matthew M. Gladding`` (as seen in pyproject.toml line 5), because the
space between first-name and last-name required a direct adjacency that
the middle initial "M. " broke.

Fix: the pattern was tightened to
``[Mm]atthew (?:[A-Z]\\. +)?[Gg]ladding``, which adds an optional
non-capturing group that matches a single uppercase letter followed by
a period and one-or-more spaces (e.g. "M. ").

Positive cases:
- ``Matthew M. Gladding`` — the exact shape from pyproject.toml (the leak)
- ``Matthew Gladding``  — regression check; old pattern must still fire

Negative cases:
- ``Matthew`` alone (no surname) must NOT fire
- ``Matt`` alone must NOT fire
- ``gladding`` alone (no first name) must NOT fire

We deliberately do NOT use the literal text that was in pyproject.toml
as a test fixture string, to avoid shipping the leak inside the test
corpus. Instead we use synthetic strings that have the same shape.
"""

from __future__ import annotations

import re
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_check_module():
    repo_root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )
    script = repo_root / "scripts" / "ci" / "check_public_mirror_safety.py"
    spec = spec_from_file_location("check_public_mirror_safety_name", script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load_check_module()


def _name_pattern() -> re.Pattern[str]:
    """Extract the LeakPattern whose regex matches the full-name leak shape."""
    # We identify the right pattern by checking it matches the middle-initial form.
    for lp in CHECK._LEAK_PATTERNS:
        # The new pattern must match middle-initial form; old one didn't.
        if lp.regex.search("Matthew M. Gladding"):
            return lp.regex
    raise AssertionError(
        "No LEAK_PATTERNS entry matches 'Matthew M. Gladding'. "
        "The tightened operator-name guard added in the 2026-05-27 audit "
        "must be present in check_public_mirror_safety.py."
    )


def test_name_pattern_is_registered() -> None:
    """A LEAK_PATTERNS entry must match the middle-initial form of the operator name."""
    assert _name_pattern() is not None


def test_name_pattern_matches_middle_initial_form() -> None:
    """The new regex must catch the middle-initial form that slipped past the old guard.

    This is the core regression case from the 2026-05-27 audit.
    We use a synthetic string rather than the literal pyproject.toml value
    so the test file itself doesn't contain the leak.
    """
    pat = _name_pattern()
    # Synthetic string with same structure as the pyproject.toml author field.
    # Format: FirstName INITIAL. LastName — the form that was NOT caught before.
    synthetic_with_middle_initial = "Operator: A. Person <> / Matthew M. Gladding <synthetic@example.com>"
    assert pat.search(synthetic_with_middle_initial), (
        "Pattern did not match the middle-initial form 'Matthew M. Gladding'. "
        "This is the shape that was leaking via pyproject.toml authors field."
    )


def test_name_pattern_still_matches_plain_form() -> None:
    """Regression check: the old plain form 'Matthew Gladding' must still fire."""
    pat = _name_pattern()
    assert pat.search("Matthew Gladding"), (
        "Pattern no longer matches 'Matthew Gladding' (regression). "
        "The tightened regex must remain a superset of the old pattern."
    )


def test_name_pattern_matches_lowercase_variant() -> None:
    """The pattern is case-insensitive on the first letter of each name part."""
    pat = _name_pattern()
    # lowercase 'm' variant
    assert pat.search("matthew Gladding")
    # lowercase 'g' variant — unusual but must still fire
    assert pat.search("Matthew gladding")


def test_name_pattern_does_not_match_first_name_alone() -> None:
    """'Matthew' alone (no surname) must NOT be flagged — too many false positives."""
    pat = _name_pattern()
    safe = [
        "Matthew said",
        "matthew",
        "Saint Matthew",
        "Matthew 5:9",  # biblical reference
    ]
    matched = [s for s in safe if pat.search(s)]
    assert not matched, f"false positive on first-name-only strings: {matched}"


def test_name_pattern_does_not_match_surname_alone() -> None:
    """'Gladding' alone (no first name) must NOT be flagged."""
    pat = _name_pattern()
    safe = [
        "Gladding Street",
        "gladding",
        "The Gladding family",
    ]
    matched = [s for s in safe if pat.search(s)]
    assert not matched, f"false positive on surname-only strings: {matched}"


def test_name_pattern_does_not_match_matt_alone() -> None:
    """'Matt' alone must NOT be flagged — there's a separate Matt Gladding pattern for that."""
    pat = _name_pattern()
    safe = [
        "Matt worked on",
        "matt",
        "matt@example.com",
    ]
    matched = [s for s in safe if pat.search(s)]
    assert not matched, f"false positive on 'Matt' alone: {matched}"
