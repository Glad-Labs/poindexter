"""Operator-identity scrub patterns — STRIPPED from the public mirror.

Carries operator-shaped literals to prove the patterns fire; must stay in
_STRIP_FILES (asserted by the drift test below).
"""
from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from services.operator_leak_patterns import OPERATOR_SCRUB_PATTERNS
from services.rag_scrub import scrub_rag_text


def _scrub(text: str) -> str:
    for rx, repl in OPERATOR_SCRUB_PATTERNS:
        text = rx.sub(repl, text)
    return text


@pytest.mark.unit
class TestOperatorScrubPatterns:
    def test_full_name_incl_middle_initial(self):
        assert "Gladding" not in _scrub("by Matthew M. Gladding, founder")
        assert "Gladding" not in _scrub("Matthew Gladding")

    def test_informal_name(self):
        assert "Gladding" not in _scrub("thanks Matt Gladding")

    def test_windows_home_path(self):
        assert "mattm" not in _scrub(r"C:\Users\mattm\glad-labs-website")

    def test_claude_projects_encoding(self):
        assert "mattm" not in _scrub("~/.claude/projects/C--Users-mattm/memory/x.md")

    def test_tailnet_and_github_handle(self):
        assert "100.81.93.12" not in _scrub("ssh 100.81.93.12")
        assert "mattg-stack" not in _scrub("commit by mattg-stack")

    def test_current_tailnet_ip_scrubs_not_just_the_retired_one(self):
        """The pattern held only the Windows node's IP until the Pop!_OS
        migration re-addressed the tailnet, leaving the LIVE address
        unscrubbed. Both must redact."""
        for ip in ("100.81.93.12", "100.111.15.72"):
            assert ip not in _scrub(f"reach it at {ip}:3000"), ip

    def test_generic_cgnat_addresses_are_left_alone(self):
        """Deliberate boundary: this is an operator-identity list, not a
        100.64.0.0/10 range match. Public SSRF tests reference CGNAT
        literals legitimately, and the mirror guard has no line-level
        exemption — so a range match could only be resolved by weakening
        a security test. Pins the decision against a well-meaning
        re-broadening."""
        for ip in ("100.64.0.1", "100.127.255.254"):
            assert ip in _scrub(f"blocked upstream {ip}"), ip

    def test_negative_generic_prose_untouched(self):
        text = "The image of a lone GPU and a matte black case."
        assert _scrub(text) == text


@pytest.mark.unit
class TestTranscriptScrub:
    """End-to-end scrub of the exact leak forms found scanning real
    claude_sessions rows (writer first-party grounding, Step 2): bare usernames
    in ``ls`` owner columns, bash-mangled / lowercase-drive paths, and bare
    private-repo refs — all forms the source-code identity patterns (built for
    well-formed paths) missed. Exercises ``rag_scrub.scrub_rag_text`` — the real
    production composition (identity + extra + private-repo)."""

    def test_bare_username_in_ls_owner_column(self):
        out = scrub_rag_text("drwxr-xr-x 1 mattm 197609 0 May 28 00:50 .")
        assert "mattm" not in out

    def test_lowercase_drive_letter_path(self):
        out = scrub_rag_text(r"venv path c:\Users\mattm\glad-labs-website not found")
        assert "mattm" not in out

    def test_bash_mangled_path_no_separators(self):
        out = scrub_rag_text("cd: C:Usersmattm.poindexterworktreesalert-triage")
        assert "mattm" not in out

    def test_bare_private_repo_ref(self):
        out = scrub_rag_text("Shipped in glad-labs-stack#928 (PR #928), merged today")
        assert "glad-labs-stack" not in out
        assert "poindexter" in out

    def test_generic_prose_with_stack_and_matte_untouched(self):
        text = "We rebuilt the tech stack; the matte GPU shroud shipped."
        assert scrub_rag_text(text) == text


def _load_leak_guard():
    repo_root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )
    script = repo_root / "scripts" / "ci" / "check_public_mirror_safety.py"
    spec = spec_from_file_location("_mirror_guard", script)
    assert spec is not None and spec.loader is not None
    guard = module_from_spec(spec)
    # Register in sys.modules before exec so @dataclass(frozen=True) annotation
    # resolution (LeakPattern / Hit) can find the module — matches the sibling
    # test_check_public_mirror_safety_* loaders.
    sys.modules[spec.name] = guard
    spec.loader.exec_module(guard)
    return guard


@pytest.mark.unit
def test_patterns_are_subset_of_leak_guard():
    """Drift guard: every operator scrub regex must also be a pattern the
    public-mirror leak guard enforces, so the two never diverge."""
    guard = _load_leak_guard()
    # LeakPattern's compiled-regex field is `.regex`; `.pattern` on the compiled
    # regex gives the source string.
    guard_sources = {lp.regex.pattern for lp in guard._LEAK_PATTERNS}
    for rx, _repl in OPERATOR_SCRUB_PATTERNS:
        assert rx.pattern in guard_sources, f"{rx.pattern!r} not in leak guard"


@pytest.mark.unit
def test_operator_files_are_stripped_from_mirror():
    """Fail loud if the overlay module or this test ever leaves _STRIP_FILES —
    either would ship the operator-name literal to the public mirror."""
    strip = set(_load_leak_guard()._STRIP_FILES)
    assert "src/cofounder_agent/services/operator_leak_patterns.py" in strip
    assert "src/cofounder_agent/tests/unit/services/test_operator_leak_patterns.py" in strip
