"""Tests for scripts/ci/kernel_purity_lint.py — the kernel→module guard.

Pins two contracts.

**Detection** (poindexter#666): the kernel substrate (``services/`` +
``plugins/``) must not import ``modules.*``. The correct direction is
module→kernel; the reverse reaches into business-module internals.

**Baseline keying** (poindexter#929): entries are keyed on
``"path::imported.module.target" -> count``, NOT on line number. The old
line-keyed baseline failed CI on any PR that merely added lines *above* a
baselined import — 16 re-baselines across 6 files, every one collateral damage
from an unrelated change. ``TestLineDriftImmunity`` is the regression guard for
exactly that; the rest keep the ratchet honest, so the drift fix doesn't buy
stability by quietly letting new violations through.
"""

import importlib.util
from pathlib import Path

import pytest


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "ci" / "kernel_purity_lint.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/ci/kernel_purity_lint.py")


def _load_lint_module():
    path = _find_repo_root(Path(__file__)) / "scripts" / "ci" / "kernel_purity_lint.py"
    spec = importlib.util.spec_from_file_location("kernel_purity_lint_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load_lint_module()


class TestDetection:
    def test_import_from_modules_is_flagged(self):
        found = LINT.scan_source("def f():\n    from modules.content.api import x\n    return x\n")
        assert found == [(2, "modules.content.api")]

    def test_plain_import_modules_is_flagged(self):
        found = LINT.scan_source("import modules.content.api\n")
        assert found == [(1, "modules.content.api")]

    def test_bare_modules_package_is_flagged(self):
        found = LINT.scan_source("from modules import content\n")
        assert found == [(1, "modules")]

    def test_two_imports_of_same_target_both_counted(self):
        src = (
            "def a():\n"
            "    from modules.content.api import x\n"
            "def b():\n"
            "    from modules.content.api import y\n"
        )
        assert LINT.scan_source(src) == [(2, "modules.content.api"), (4, "modules.content.api")]

    def test_multiple_targets_in_one_import_all_counted(self):
        found = LINT.scan_source("import modules.content.api, modules.finance.api\n")
        assert found == [(1, "modules.content.api"), (1, "modules.finance.api")]

    def test_kernel_import_not_flagged(self):
        assert LINT.scan_source("from services.site_config import SiteConfig\n") == []

    def test_lookalike_prefix_not_flagged(self):
        """``modules_helper`` is not the ``modules`` package — prefix, not namespace."""
        src = "import modules_helper\nfrom modulesomething import x\n"
        assert LINT.scan_source(src) == []

    def test_relative_import_not_flagged(self):
        """``from . import x`` has ``node.module is None`` — never a modules.* reach."""
        assert LINT.scan_source("from . import sibling\nfrom .pkg import thing\n") == []

    def test_syntax_error_yields_no_violations(self):
        assert LINT.scan_source("def f(:\n") == []


class TestLineDriftImmunity:
    """poindexter#929 regression guard — the whole point of the re-keying.

    Adding lines ABOVE a baselined import must not change what the baseline
    matches. Under the old ``"path:lineno"`` keying this is precisely what
    broke CI on PRs that never touched the violation.
    """

    SRC = "def f():\n    from modules.content.api import x\n    return x\n"

    def test_padding_above_shifts_lineno_but_not_target(self):
        padded = ("# pad\n" * 60) + self.SRC
        before = LINT.scan_source(self.SRC)
        after = LINT.scan_source(padded)

        # The line number moves...
        assert before[0][0] == 2
        assert after[0][0] == 62
        # ...but the key the baseline is matched on does not.
        assert [t for _, t in before] == [t for _, t in after] == ["modules.content.api"]

    def test_count_per_target_survives_padding(self):
        src = self.SRC + "\ndef g():\n    from modules.content.api import y\n"
        padded = ("# pad\n" * 60) + src
        assert len(LINT.scan_source(src)) == len(LINT.scan_source(padded)) == 2


class TestBaselineFormat:
    def test_every_key_is_path_and_module_target(self):
        for key in LINT.KERNEL_PURITY_BASELINE:
            rel, _, target = key.partition("::")
            assert rel.endswith(".py"), f"{key}: left side must be a .py path"
            assert target.startswith("modules"), f"{key}: right side must be a modules.* target"

    def test_no_key_is_line_numbered(self):
        """Ratchet against regressing to the line-keyed format (poindexter#929)."""
        offenders = [
            key
            for key in LINT.KERNEL_PURITY_BASELINE
            if key.rpartition(":")[2].isdigit()
        ]
        assert offenders == [], (
            "Baseline keys must be 'path::modules.target', not 'path:lineno' — "
            f"line numbers drift and tax unrelated PRs. Offenders: {offenders}"
        )

    def test_counts_are_positive(self):
        assert all(n >= 1 for n in LINT.KERNEL_PURITY_BASELINE.values())


class TestRationaleComments:
    """Each baselined entry must say WHY — enforced, not honour-system."""

    def test_source_parse_recovers_the_live_dict(self):
        """If this drifts, the rationale check below would pass vacuously."""
        parsed = [key for key, _ in LINT.parse_baseline_source()]
        assert sorted(parsed) == sorted(LINT.KERNEL_PURITY_BASELINE)

    def test_every_real_entry_has_a_rationale(self):
        undocumented = [key for key, documented in LINT.parse_baseline_source() if not documented]
        assert undocumented == [], f"baseline entries missing a `# why` comment: {undocumented}"

    @pytest.mark.parametrize(
        "body,expected_documented",
        [
            ('    # because reasons\n    "a/b.py::modules.x": 1,\n', [True]),
            ('    "a/b.py::modules.x": 1,\n', [False]),
            # A blank line between comment and entry still counts as cover.
            ('    # because reasons\n\n    "a/b.py::modules.x": 1,\n', [True]),
            # An entry directly above does NOT cover the next one — one
            # rationale per entry, so a new entry can't ride in on a neighbour's.
            (
                '    # only covers the first\n'
                '    "a/b.py::modules.x": 1,\n'
                '    "c/d.py::modules.y": 1,\n',
                [True, False],
            ),
        ],
    )
    def test_rationale_detection(self, body, expected_documented):
        source = "KERNEL_PURITY_BASELINE: dict[str, int] = {\n" + body + "}\n"
        assert [doc for _, doc in LINT.parse_baseline_source(source)] == expected_documented


class TestBaselineRatchet:
    """The ratchet may only shrink — verified against the live tree."""

    def test_no_key_exceeds_its_allowance(self):
        counts = LINT.compute_counts()
        offenders = {
            key: (n, LINT.KERNEL_PURITY_BASELINE.get(key, 0))
            for key, n in counts.items()
            if n > LINT.KERNEL_PURITY_BASELINE.get(key, 0)
        }
        assert offenders == {}, (
            "New kernel→module import(s) — route through modules/content/api.py "
            f"or baseline with a rationale. found vs allowed: {offenders}"
        )

    def test_no_stale_baseline_entries(self):
        """A fixed violation must be recorded, or the ratchet silently re-widens."""
        counts = LINT.compute_counts()
        stale = {
            key: (counts.get(key, 0), allowed)
            for key, allowed in LINT.KERNEL_PURITY_BASELINE.items()
            if counts.get(key, 0) < allowed
        }
        assert stale == {}, (
            "Baseline allows more than the tree contains — lower/remove these "
            f"entries so the ratchet stays tight. found vs allowed: {stale}"
        )

    def test_lint_passes_on_the_current_tree(self):
        assert LINT.main() == 0
