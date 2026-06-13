"""Contract tests for operator-identity LEAK_GUARD regexes (name + path).

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


# ---------------------------------------------------------------------------
# Claude-projects PATH-encoding regex (operator-identity leak, 2026-06-13).
#
# Claude Code namespaces project memory under ~/.claude/projects/, encoding the
# cwd by flattening the drive-colon + path separators into dashes:
# ``C:\Users\mattm``  ->  ``C--Users-mattm``. That encoded form matched NEITHER
# the ``C:[\\/]Users[\\/]mattm`` nor the ``/c/Users/mattm`` pattern, so refs
# like ``~/.claude/projects/C--Users-mattm/memory/...`` leaked the operator
# username to the public mirror. The guard pattern is case-INSENSITIVE: Docker
# bind mounts can lowercase Windows dir names, and the encoded path was observed
# in four casings across the source tree.
# ---------------------------------------------------------------------------


def _claude_projects_path_pattern() -> re.Pattern[str]:
    """Extract the LeakPattern that matches the Claude-projects path encoding."""
    for lp in CHECK._LEAK_PATTERNS:
        if lp.regex.search("C--Users-mattm"):
            return lp.regex
    raise AssertionError(
        "No LEAK_PATTERNS entry matches 'C--Users-mattm'. Claude Code "
        "flattens C:\\Users\\<user> to C--Users-<user> for project-dir "
        "namespaces; the guard must catch this encoding (the C:\\Users and "
        "/c/Users patterns do not)."
    )


def test_claude_projects_path_pattern_is_registered() -> None:
    """A LEAK_PATTERNS entry must match the C--Users-<user> path encoding."""
    assert _claude_projects_path_pattern() is not None


def test_claude_projects_path_matches_base_scope() -> None:
    """The encoded home-dir scope must be caught inside a realistic ref."""
    pat = _claude_projects_path_pattern()
    assert pat.search("~/.claude/projects/C--Users-mattm/memory/MEMORY.md"), (
        "Pattern did not match the C--Users-mattm projects scope — this is "
        "the operator-path encoding that leaked to the public mirror."
    )


def test_claude_projects_path_matches_suffixed_scope() -> None:
    """The project-suffixed namespace (the junction sibling) must also fire."""
    pat = _claude_projects_path_pattern()
    assert pat.search(
        "~/.claude/projects/C--Users-mattm-glad-labs-website/memory"
    ), (
        "Pattern did not match the -glad-labs-website suffixed scope. The "
        "base C--Users-mattm pattern must catch it as a substring."
    )


def test_claude_projects_path_is_case_insensitive() -> None:
    """Docker bind mounts can lowercase Windows dir names — match any casing.

    The encoded path was observed in four casings in the source tree
    (C--Users-mattm, C--users-mattm, c--Users-mattm-website, c--users-mattm),
    so a case-sensitive guard would miss most of them.
    """
    pat = _claude_projects_path_pattern()
    assert pat.search("c--users-mattm"), "lowercase form must fire"
    assert pat.search("C--users-mattm"), "mixed-case form must fire"


def test_claude_projects_path_does_not_match_generic_placeholder() -> None:
    """A genericized (non-operator) example must NOT fire.

    This keeps the now-public taps/memory.py docstring + its test fixtures
    clean after their operator username was genericized to a placeholder, and
    guards against broadening the pattern to ``C--Users-\\w+`` (which would
    re-flag those public placeholders).
    """
    pat = _claude_projects_path_pattern()
    safe = [
        "C--Users-alice",
        "C--Users-alice-myproject",
        "C--Users-<you>",
        "C--WINDOWS-system32",
    ]
    matched = [s for s in safe if pat.search(s)]
    assert not matched, f"false positive on generic placeholders: {matched}"


# ---------------------------------------------------------------------------
# Windows home-path regex — source-escaped backslash form (2026-06-13).
#
# In Python source a Windows path is written with ESCAPED backslashes
# (``C:\\Users\\mattm`` in the file text), because a bare ``C:\Users`` would be
# an invalid ``\U`` unicode escape. The original ``C:[\\/]Users[\\/]mattm``
# pattern matched only a SINGLE separator, so the doubled form leaked the
# operator home path via .py docstrings (tap_corsair_csv.py, voice_brain_host.py
# both shipped it). The pattern now uses ``[\\/]+`` to span doubled separators.
# ---------------------------------------------------------------------------


def _windows_home_path_pattern() -> re.Pattern[str]:
    """Extract the LeakPattern matching the operator Windows home path."""
    for lp in CHECK._LEAK_PATTERNS:
        if lp.regex.search(r"C:\Users\mattm"):
            return lp.regex
    raise AssertionError(
        "No LEAK_PATTERNS entry matches the C:\\Users\\mattm Windows home path."
    )


def test_windows_home_path_matches_single_separator() -> None:
    """Regression: the original single-backslash / forward-slash forms still fire."""
    pat = _windows_home_path_pattern()
    assert pat.search(r"C:\Users\mattm"), "single-backslash form must fire"
    assert pat.search("C:/Users/mattm"), "forward-slash form must fire"


def test_windows_home_path_matches_source_escaped_backslashes() -> None:
    """The Python-source escaped ``C:\\\\Users\\\\mattm`` form must fire.

    This is the doubled-separator form the single-separator class missed,
    which leaked the operator home path via .py docstrings (tap_corsair_csv.py,
    voice_brain_host.py). ``[\\/]+`` spans the doubled backslashes.
    """
    pat = _windows_home_path_pattern()
    assert pat.search(r"C:\\Users\\mattm"), (
        "Windows pattern missed the source-escaped C:\\\\Users\\\\mattm form "
        "that appears in Python-source docstrings."
    )
