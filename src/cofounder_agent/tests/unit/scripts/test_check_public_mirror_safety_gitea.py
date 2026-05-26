"""Contract test for the gitea#NNN LEAK_GUARD pattern.

Pins the 2026-05-26 audit finding: 43 dead "gitea#NNN" citations
across 30 would-ship files slipped through the leak guard because
the existing rule only caught ".gitea/" (directory), not the
citation form. Standing rule per feedback_no_operator_info_to_public_repo:
dead Gitea refs must never land in Glad-Labs/poindexter.

The test loads the LEAK_PATTERNS tuple from check_public_mirror_safety
and exercises the new entry directly against representative shapes
captured from the cleanup PR. Decoupled from any specific file
location so a future cleanup that moves comments around can't
silently regress the guard.
"""

from __future__ import annotations

import re
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_check_module():
    repo_root = Path(__file__).resolve().parents[5]
    script = repo_root / "scripts" / "ci" / "check_public_mirror_safety.py"
    spec = spec_from_file_location("check_public_mirror_safety", script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load_check_module()


def _gitea_pattern() -> re.Pattern[str]:
    for lp in CHECK._LEAK_PATTERNS:
        if lp.regex.search("gitea#123"):
            return lp.regex
    raise AssertionError(
        "No LEAK_PATTERNS entry matches 'gitea#123'. The gitea#NNN "
        "guard added 2026-05-26 must be present."
    )


def test_gitea_pattern_is_registered() -> None:
    """A LEAK_PATTERNS entry must match a bare gitea#NNN citation."""
    assert _gitea_pattern() is not None


def test_gitea_pattern_matches_citation_shapes() -> None:
    """Every shape observed in the 2026-05-26 cleanup must match."""
    pat = _gitea_pattern()
    samples = [
        "(gitea#225)",
        "(gitea#271 Phase 3.B)",
        "— part of gitea#271 Phase 3 Wave B.",
        "Closes gitea#277.",
        "gitea#280: any UPDATE to",
        "(GH#54 / gitea#271 Phase 6)",  # mixed-tracker citation
        "Part of gitea#272 Phase 3 Wave B.",
    ]
    unmatched = [s for s in samples if not pat.search(s)]
    assert not unmatched, f"pattern missed: {unmatched}"


def test_gitea_pattern_does_not_match_github_issues() -> None:
    """Real GitHub issue refs (#NNN / Org/repo#NNN) must not trip."""
    pat = _gitea_pattern()
    safe = [
        "Glad-Labs/poindexter#329",
        "closes #410",
        "(see #485)",
        "gitea decommissioned",  # the word alone, no #NNN
        ".gitea/workflows/",  # the directory ref, caught by an earlier rule
    ]
    matched = [s for s in safe if pat.search(s)]
    assert not matched, f"false positive on: {matched}"


def test_leak_pattern_label_helps_pr_author() -> None:
    """The LeakPattern.why field must give actionable remediation
    so a PR author hitting this guard knows how to fix it."""
    pat = _gitea_pattern()
    for lp in CHECK._LEAK_PATTERNS:
        if lp.regex is pat:
            assert "internal tracker" in lp.why or "tracker" in lp.why
            return
    raise AssertionError("gitea pattern is missing its remediation copy")
