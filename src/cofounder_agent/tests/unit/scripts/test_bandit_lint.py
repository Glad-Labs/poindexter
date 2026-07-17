"""Tests for scripts/ci/bandit_lint.py — the bandit ratchet.

Bandit findings are grandfathered per-file-per-rule in ``bandit_baseline.json``;
CI fails only on a NEW finding. This replaces the old
``codebase_audit``-files-a-GitHub-issue-per-finding flow, which produced 91
issues (every one examined was a false positive) and buried the real backlog.

Two things this suite pins that the other three ratchets don't have to:

- **Path normalization.** Bandit echoes back the path as ``<cli-target> +
  os.sep + subpath``, so on Windows a nested file comes out mixed-separator
  (``scripts/ops\\find_phantom_settings.py``). The baseline is committed and
  compared on Linux CI, so it must be separator-normalized or every Windows-
  generated entry is a phantom regression.
- **Per-rule keys.** The other ratchets each guard exactly one rule, so a bare
  per-file count is unambiguous. Bandit multiplexes ~30 rules of differing
  severity through one scan; a per-file count alone would let a new B605 (shell
  injection) ride in free as long as someone deleted a trivial B404 from the
  same file.
"""

import importlib.util
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "ci" / "bandit_lint.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/ci/bandit_lint.py")


def _load_lint_module():
    path = _find_repo_root(Path(__file__)) / "scripts" / "ci" / "bandit_lint.py"
    spec = importlib.util.spec_from_file_location("bandit_lint_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load_lint_module()


def _finding(filename: str, test_id: str = "B608", line: int = 1) -> dict:
    return {
        "filename": filename,
        "test_id": test_id,
        "line_number": line,
        "issue_severity": "MEDIUM",
        "issue_text": "Possible SQL injection vector.",
        "code": "sql = f'SELECT {cols}'",
    }


class TestCountsFromFindings:
    def test_groups_by_file_and_rule(self):
        findings = [
            _finding("brain/foo.py", "B608"),
            _finding("brain/foo.py", "B608"),
            _finding("brain/foo.py", "B310"),
            _finding("scripts/bar.py", "B104"),
        ]
        assert LINT.counts_from_findings(findings) == {
            "brain/foo.py": {"B310": 1, "B608": 2},
            "scripts/bar.py": {"B104": 1},
        }

    def test_empty_findings_yield_empty_counts(self):
        assert LINT.counts_from_findings([]) == {}

    def test_windows_backslashes_are_normalized(self):
        """Bandit joins the CLI target with os.sep, so a nested file on Windows
        comes back mixed-separator. The committed baseline is compared on Linux
        CI — without normalization every such entry is a phantom regression."""
        findings = [_finding("src/cofounder_agent/services/flows\\content_generation.py")]
        assert LINT.counts_from_findings(findings) == {
            "src/cofounder_agent/services/flows/content_generation.py": {"B608": 1}
        }

    def test_fully_backslashed_path_is_normalized(self):
        findings = [_finding("scripts\\ops\\find_phantom_settings.py", "B608")]
        assert LINT.counts_from_findings(findings) == {
            "scripts/ops/find_phantom_settings.py": {"B608": 1}
        }

    def test_absolute_path_is_made_repo_relative(self):
        """Bandit reports absolute paths when handed absolute targets; the
        baseline must stay repo-relative to be portable across checkouts."""
        abs_path = str(LINT.REPO_ROOT / "brain" / "foo.py")
        assert LINT.counts_from_findings([_finding(abs_path)]) == {"brain/foo.py": {"B608": 1}}


class TestPrivateOverlayExclusion:
    """The baseline JSON ships in the public mirror, so a finding in a
    mirror-stripped operator file must never enter it (mirrors
    adapter_purity_lint's mcp-server-gladlabs exclusion)."""

    def test_private_overlay_file_is_dropped(self):
        findings = [_finding("src/cofounder_agent/services/operator_overrides.py", "B608")]
        assert LINT.counts_from_findings(findings) == {}

    def test_every_declared_private_file_is_excluded(self):
        for rel in LINT.PRIVATE_OVERLAY_FILES:
            assert LINT.counts_from_findings([_finding(rel, "B608")]) == {}, rel

    def test_non_private_neighbour_still_counted(self):
        findings = [_finding("src/cofounder_agent/services/publish_service.py", "B608")]
        assert LINT.counts_from_findings(findings) == {
            "src/cofounder_agent/services/publish_service.py": {"B608": 1}
        }


class TestFindRegressions:
    def test_new_rule_in_baselined_file_is_a_regression(self):
        """The per-rule key earning its keep: B310 is fully baselined, but a
        brand-new B605 in the same file must NOT ride in free."""
        counts = {"brain/foo.py": {"B310": 2, "B605": 1}}
        baseline = {"brain/foo.py": {"B310": 2}}
        regressions = LINT.find_regressions(counts, baseline)
        assert len(regressions) == 1
        assert regressions[0][:2] == ("brain/foo.py", "B605")

    def test_new_severe_rule_caught_even_when_file_total_is_unchanged(self):
        """THE case that justifies per-rule keys over a bare per-file count.

        The file sheds one B310 and gains one B605 (shell injection), so the
        per-file TOTAL is unchanged at 2 — a count-only baseline (what the other
        three ratchets use) would see 2 <= 2 and wave the B605 straight through.
        Keying on rule catches it.
        """
        counts = {"brain/foo.py": {"B310": 1, "B605": 1}}  # total 2
        baseline = {"brain/foo.py": {"B310": 2}}  # total 2
        assert sum(counts["brain/foo.py"].values()) == sum(baseline["brain/foo.py"].values())
        regressions = LINT.find_regressions(counts, baseline)
        assert len(regressions) == 1
        assert regressions[0][:2] == ("brain/foo.py", "B605")

    def test_count_increase_is_a_regression(self):
        counts = {"brain/foo.py": {"B608": 3}}
        baseline = {"brain/foo.py": {"B608": 2}}
        assert len(LINT.find_regressions(counts, baseline)) == 1

    def test_new_file_is_a_regression(self):
        assert len(LINT.find_regressions({"brain/new.py": {"B608": 1}}, {})) == 1

    def test_equal_counts_are_clean(self):
        counts = {"brain/foo.py": {"B608": 2}}
        assert LINT.find_regressions(counts, {"brain/foo.py": {"B608": 2}}) == []

    def test_fewer_findings_are_clean_ratchet_only_shrinks(self):
        counts = {"brain/foo.py": {"B608": 1}}
        assert LINT.find_regressions(counts, {"brain/foo.py": {"B608": 5}}) == []

    def test_removed_finding_entirely_is_clean(self):
        assert LINT.find_regressions({}, {"brain/foo.py": {"B608": 5}}) == []


class TestBaselineFile:
    def test_baseline_exists_and_is_nested_rule_keyed(self):
        baseline = LINT.load_baseline()
        assert isinstance(baseline, dict)
        for rel, rules in baseline.items():
            assert isinstance(rules, dict), f"{rel} must map rule -> count"
            for test_id, n in rules.items():
                assert test_id.startswith("B"), f"{rel}: {test_id!r} is not a bandit rule id"
                assert isinstance(n, int) and n > 0, f"{rel}.{test_id} must be a positive int"

    def test_baseline_paths_are_normalized_and_relative(self):
        for rel in LINT.load_baseline():
            assert "\\" not in rel, f"{rel!r} carries a Windows separator"
            assert not Path(rel).is_absolute(), f"{rel!r} must be repo-relative"

    def test_baseline_carries_no_private_overlay_path(self):
        """Guards the public-mirror leak directly, not just via the scan."""
        for rel in LINT.load_baseline():
            assert rel not in LINT.PRIVATE_OVERLAY_FILES, f"{rel} is mirror-stripped"


class TestBanditVersionPinnedForReproducibility:
    """A baseline is only meaningful against the bandit that generated it — a
    version bump can add rules and turn a clean tree red. The workflow's pinned
    install must therefore track poetry.lock; this test is what makes a
    dependabot bump fail loudly here instead of mysteriously in CI."""

    def test_workflow_pin_matches_poetry_lock(self):
        root = LINT.REPO_ROOT
        lock = (root / "src" / "cofounder_agent" / "poetry.lock").read_text(encoding="utf-8")
        locked = None
        for block in lock.split("[[package]]"):
            if 'name = "bandit"' in block:
                for line in block.splitlines():
                    if line.strip().startswith("version = "):
                        locked = line.split("=", 1)[1].strip().strip('"')
                        break
                break
        assert locked, "bandit not found in poetry.lock"

        workflow = (root / ".github" / "workflows" / "unit-tests.yml").read_text(encoding="utf-8")
        assert f"bandit=={locked}" in workflow, (
            f"poetry.lock pins bandit=={locked} but .github/workflows/unit-tests.yml "
            f"does not install that exact version. Sync the lint-main pip install "
            f"pin, then re-run `python scripts/ci/bandit_lint.py --update-baseline` "
            f"if the new version changes findings."
        )


class TestRealTreeIsCleanAgainstBaseline:
    """End-to-end: runs the real bandit over the real tree. This is the check
    that actually gates CI, so it must pass here too."""

    def test_repo_has_no_new_bandit_findings(self):
        counts = LINT.compute_counts()
        regressions = LINT.find_regressions(counts, LINT.load_baseline())
        assert regressions == [], (
            "New bandit finding(s) not in baseline:\n"
            + "\n".join(f"  {r[0]}: {r[1]} = {r[2]} (baseline allows {r[3]})" for r in regressions)
        )
