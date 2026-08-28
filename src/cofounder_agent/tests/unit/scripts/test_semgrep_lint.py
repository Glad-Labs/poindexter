"""Unit tests for scripts/ci/semgrep_lint.py.

The semgrep ratchet is the compensating control for GitHub code scanning being
disabled on the private repos — it is the only thing scanning the operator
overlay, which the public mirror strips. So the properties that matter are the
ones that stop it becoming a no-op: it must not report clean off a scan that
did not happen, and a missing baseline must fail rather than permit everything.

These tests do NOT invoke semgrep itself (that needs the binary and ~15s); they
exercise the ratchet logic and the guards around the subprocess call.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

LINT_PATH = Path(__file__).resolve().parents[5] / "scripts" / "ci" / "semgrep_lint.py"


def _load():
    spec = importlib.util.spec_from_file_location("_semgrep_lint", LINT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_semgrep_lint"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load()


# --------------------------------------------------------------------------
# ratchet arithmetic
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_counts_are_keyed_per_file_per_rule(mod) -> None:
    findings = [
        {"path": "a.py", "check_id": "python.lang.security.sql-injection"},
        {"path": "a.py", "check_id": "python.lang.security.sql-injection"},
        {"path": "a.py", "check_id": "python.lang.security.weak-hash"},
        {"path": "b.py", "check_id": "python.lang.security.weak-hash"},
    ]
    assert mod.counts_from_findings(findings) == {
        "a.py": {"sql-injection": 2, "weak-hash": 1},
        "b.py": {"weak-hash": 1},
    }


@pytest.mark.unit
def test_a_new_rule_cannot_ride_in_behind_a_deleted_one(mod) -> None:
    """Per-file-per-RULE keying is the point: totals alone would hide this."""
    baseline = {"a.py": {"weak-hash": 1}}
    counts = {"a.py": {"sql-injection": 1}}  # same total, different rule
    regressions = mod.find_regressions(counts, baseline)
    assert regressions == [("a.py", "sql-injection", 1, 0)]


@pytest.mark.unit
def test_fewer_findings_than_baseline_is_clean(mod) -> None:
    """The ratchet only shrinks."""
    assert mod.find_regressions({"a.py": {"weak-hash": 1}}, {"a.py": {"weak-hash": 3}}) == []


@pytest.mark.unit
def test_equal_to_baseline_is_clean(mod) -> None:
    assert mod.find_regressions({"a.py": {"weak-hash": 2}}, {"a.py": {"weak-hash": 2}}) == []


@pytest.mark.unit
def test_a_finding_in_an_unbaselined_file_is_a_regression(mod) -> None:
    assert mod.find_regressions({"new.py": {"weak-hash": 1}}, {}) == [("new.py", "weak-hash", 1, 0)]


# --------------------------------------------------------------------------
# the guards that stop it becoming a no-op
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_baseline_allows_nothing_rather_than_everything(mod, tmp_path) -> None:
    """Fail-loud direction: an absent baseline must not permit every finding."""
    mod.BASELINE_PATH = tmp_path / "does-not-exist.json"
    assert mod.load_baseline() == {}
    assert mod.find_regressions({"a.py": {"weak-hash": 1}}, mod.load_baseline())


@pytest.mark.unit
def test_no_vendored_rules_raises_rather_than_scanning_with_none(mod, tmp_path) -> None:
    """A rules dir that moved must fail, not scan with an empty ruleset."""
    mod.RULES_DIR = tmp_path / "empty"
    mod.RULES_DIR.mkdir()
    with pytest.raises(RuntimeError, match="no vendored rulesets"):
        mod._rules_args()


@pytest.mark.unit
def test_vendored_rules_are_present_and_non_trivial(mod) -> None:
    """The real rulesets ship in-repo — that is what removes the network dep.

    Skipped on the public mirror, which strips infrastructure/semgrep/: the
    rules are a credential-pattern corpus that public push protection rejects.
    """
    if not Path(mod.RULES_DIR).is_dir():
        pytest.skip("vendored rules stripped (public mirror) — nothing to check")
    args = mod._rules_args()
    assert args, "expected --config args for the vendored rulesets"
    configs = [Path(a) for a in args if a != "--config"]
    assert len(configs) >= 2, f"expected python + secrets packs, got {configs}"
    for cfg in configs:
        assert cfg.is_file()
        assert cfg.stat().st_size > 10_000, f"{cfg.name} looks truncated"


@pytest.mark.unit
def test_empty_semgrep_output_raises_rather_than_reporting_clean(mod, monkeypatch) -> None:
    """No stdout means the scan did not run. Never a clean tree."""

    class _Proc:
        stdout = ""
        stderr = "semgrep: command not found"
        returncode = 127

    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/semgrep")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc())
    with pytest.raises(RuntimeError, match="produced no output"):
        mod.run_semgrep()


@pytest.mark.unit
def test_absent_semgrep_binary_raises(mod, monkeypatch) -> None:
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="not on PATH"):
        mod.run_semgrep()


@pytest.mark.unit
def test_a_timeout_is_surfaced_but_not_fatal(mod, monkeypatch) -> None:
    """Per-file timeouts reduce coverage; they are reported, not swallowed."""
    payload = {
        "results": [],
        "errors": [{"type": "Timeout", "path": "big.py"}],
        "paths": {"scanned": ["a.py", "b.py"]},
    }

    class _Proc:
        stdout = json.dumps(payload)
        stderr = ""
        returncode = 0

    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/semgrep")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc())
    results, scanned, soft = mod.run_semgrep()
    assert results == []
    assert scanned == 2
    assert soft and "big.py" in soft[0]


@pytest.mark.unit
def test_a_hard_scan_error_raises(mod, monkeypatch) -> None:
    """A config/parse error means the results are unreliable — never clean."""
    payload = {
        "results": [],
        "errors": [{"type": "InvalidRuleSchemaError", "path": "rules.yaml"}],
        "paths": {"scanned": []},
    }

    class _Proc:
        stdout = json.dumps(payload)
        stderr = ""
        returncode = 2

    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/semgrep")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc())
    with pytest.raises(RuntimeError, match="scan error"):
        mod.run_semgrep()


@pytest.mark.unit
def test_scan_targets_cover_the_overlay(mod) -> None:
    """The whole reason this lint exists: the mirror-stripped code is scanned.

    modules/finance/ and services/operator_*.py live under src/cofounder_agent,
    so that root must stay in SCAN_TARGETS or the gap silently reopens.

    The overlay assertion is skipped where the overlay does not exist — this
    test file ships to the public mirror, which strips modules/finance/, and a
    shipping test that hard-asserts a stripped path turns the mirror red. The
    mirror-safety guard catches the same trap for scripts.
    """
    assert "src/cofounder_agent" in mod.SCAN_TARGETS
    assert "brain" in mod.SCAN_TARGETS

    overlay = Path(mod.REPO_ROOT) / "src" / "cofounder_agent" / "modules" / "finance"
    if not overlay.exists():
        pytest.skip("overlay not present (public mirror) — nothing to cover here")
    assert overlay.is_dir(), "overlay moved — update SCAN_TARGETS to keep covering it"


@pytest.mark.unit
def test_committed_baseline_is_wellformed(mod) -> None:
    baseline = json.loads(Path(mod.BASELINE_PATH).read_text(encoding="utf-8"))
    assert baseline, "a committed baseline should not be empty"
    for rel, rules in baseline.items():
        assert not Path(rel).is_absolute(), f"baseline key must be repo-relative: {rel}"
        assert "\\" not in rel, f"baseline key must use forward slashes: {rel}"
        for rule, n in rules.items():
            assert isinstance(n, int) and n > 0, f"{rel}:{rule} = {n!r}"
