"""Tests for scripts/ci/mypy_lint.py, the mypy ratchet.

mypy errors are grandfathered per file, per error code in
``mypy_baseline.json``. CI fails only on a NET-NEW error, nothing files an
issue, and the baseline only shrinks. Same doctrine as the bandit and semgrep
ratchets.

The properties pinned here are the ones that would turn the gate into a no-op
without anyone noticing:

- **A run that did not complete never reads as clean.** mypy exit 2 (a
  blocking error or a crash), a non-zero exit with nothing parseable, and a
  parsed count that disagrees with mypy's own summary line all fail. The
  count check is the vendor-format guard: if mypy changes its line shape, the
  parser undercounts, the summary disagrees, and the lint goes red instead of
  quietly letting new errors through.
- **Per-code keys.** A new ``[arg-type]`` must not ride in behind a fixed
  ``[assignment]`` in the same file, even when the file's total is unchanged.
- **Shrink-only updates.** ``--update-baseline`` must refuse to record a new
  error unless growth is asked for explicitly.
- **One config.** A second ``[tool.mypy]`` beneath the repo root would take
  over bare ``mypy`` runs from the package directory and disagree with CI.

The real-mypy tests run the installed mypy on a four-file tree, in well under
a second. They are the positive control. The parser is checked against what
this mypy version actually prints, not against strings written by hand.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import tomllib
import yaml

from tests.unit._nonempty import nonempty


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "ci" / "mypy_lint.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/ci/mypy_lint.py")


REPO_ROOT = _find_repo_root(Path(__file__))
SOURCE_ROOT = REPO_ROOT / "src" / "cofounder_agent"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load_lint_module():
    path = REPO_ROOT / "scripts" / "ci" / "mypy_lint.py"
    spec = importlib.util.spec_from_file_location("mypy_lint_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LINT = _load_lint_module()

CWD = SOURCE_ROOT  # mypy prints paths relative to the directory it runs in
PREFIX = "src/cofounder_agent/"


def _parse(returncode: int, stdout: str, stderr: str = ""):
    return LINT.parse_report(returncode, stdout, stderr, cwd=CWD, repo_root=REPO_ROOT)


def _error(path: str, code: str, line: int = 1, message: str = "msg"):
    return LINT.MypyError(path=path, line=line, code=code, message=message, raw="")


def _report(errors=(), checked: int = 907):
    return LINT.MypyReport(errors=tuple(errors), checked=checked, summary="")


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

TWO_FILE_RUN = """\
poindexter/brain/alert_sync.py:423: error: Incompatible types in assignment (expression has type "str", target has type "int | bool | None")  [assignment]
poindexter/brain/alert_sync.py:511: error: Unsupported operand types for + ("None" and "int")  [operator]
poindexter/brain/alert_sync.py:511: note: Left operand is of type "int | bool | None"
poindexter/services/chat_agent.py:106: error: Item "None" of "Any | dict[Any, Any] | None" has no attribute "get"  [union-attr]
Found 3 errors in 2 files (checked 907 source files)
"""


class TestParseReport:
    def test_error_lines_become_keyed_errors(self):
        report = _parse(1, TWO_FILE_RUN)
        assert report.checked == 907
        assert [(e.path, e.line, e.code) for e in report.errors] == [
            (PREFIX + "poindexter/brain/alert_sync.py", 423, "assignment"),
            (PREFIX + "poindexter/brain/alert_sync.py", 511, "operator"),
            (PREFIX + "poindexter/services/chat_agent.py", 106, "union-attr"),
        ]

    def test_message_excludes_the_code_suffix(self):
        report = _parse(1, TWO_FILE_RUN)
        assert report.errors[1].message == 'Unsupported operand types for + ("None" and "int")'

    def test_notes_are_not_counted(self):
        """The note under line 511 is context for the error above it. Counting
        it would inflate the baseline and break the summary cross-check."""
        report = _parse(1, TWO_FILE_RUN)
        assert len(report.errors) == 3

    def test_clean_run(self):
        report = _parse(0, "Success: no issues found in 907 source files\n")
        assert report.errors == ()
        assert report.checked == 907

    def test_singular_summary_forms_parse(self):
        out = 'main.py:3: error: Name "x" is not defined  [name-defined]\nFound 1 error in 1 file (checked 1 source file)\n'
        report = _parse(1, out)
        assert report.checked == 1
        assert [e.code for e in report.errors] == ["name-defined"]

    def test_column_numbers_are_tolerated(self):
        out = 'main.py:3:5: error: Name "x" is not defined  [name-defined]\nFound 1 error in 1 file (checked 9 source files)\n'
        assert [(e.path, e.line) for e in _parse(1, out).errors] == [(PREFIX + "main.py", 3)]

    def test_only_the_trailing_bracket_is_the_code(self):
        out = (
            'main.py:3: error: Value of type "list[int]  [x]" is odd  [index]\n'
            "Found 1 error in 1 file (checked 9 source files)\n"
        )
        (error,) = _parse(1, out).errors
        assert error.code == "index"
        assert error.message == 'Value of type "list[int]  [x]" is odd'

    def test_a_note_mentioning_error_is_still_a_note(self):
        out = (
            "main.py:3: error: Bad thing  [misc]\n"
            'main.py:3: note: this note says "error: something" in its text\n'
            "main.py: note: an unlocated note, also saying error: here\n"
            "Found 1 error in 1 file (checked 9 source files)\n"
        )
        assert len(_parse(1, out).errors) == 1

    def test_an_ordinary_message_quoting_internal_error_is_not_a_crash(self):
        out = (
            'main.py:3: error: Name "INTERNAL ERROR" is not defined  [name-defined]\n'
            "Found 1 error in 1 file (checked 9 source files)\n"
        )
        assert len(_parse(1, out).errors) == 1

    def test_backslashes_and_absolute_paths_are_normalized(self):
        absolute = SOURCE_ROOT / "poindexter" / "memory" / "client.py"
        out = (
            "poindexter\\services\\ragas_eval.py:1: error: a  [arg-type]\n"
            f"{absolute}:2: error: b  [assignment]\n"
            "Found 2 errors in 2 files (checked 9 source files)\n"
        )
        assert [e.path for e in _parse(1, out).errors] == [
            PREFIX + "poindexter/services/ragas_eval.py",
            PREFIX + "poindexter/memory/client.py",
        ]


class TestParseReportRefusesUntrustworthyRuns:
    """Each of these is a way a gate can end up reporting clean over nothing."""

    def test_exit_2_is_a_failure_even_with_parseable_errors(self):
        out = "pkg/syntax.py:1: error: Invalid syntax  [syntax]\nFound 1 error in 1 file (errors prevented further checking)\n"
        with pytest.raises(LINT.MypyRunError, match="exited 2"):
            _parse(2, out)

    def test_any_other_exit_code_is_a_failure(self):
        with pytest.raises(LINT.MypyRunError, match="exited 3"):
            _parse(3, "Success: no issues found in 907 source files\n")

    def test_nonzero_exit_with_nothing_parseable_is_a_failure(self):
        with pytest.raises(LINT.MypyRunError):
            _parse(1, "", "/usr/bin/python3: No module named mypy\n")

    def test_nonzero_exit_with_a_summary_but_no_error_lines_is_a_failure(self):
        with pytest.raises(LINT.MypyRunError, match="nothing parseable|could parse"):
            _parse(1, "Found 3 errors in 2 files (checked 907 source files)\n")

    def test_a_crash_is_a_failure_whatever_the_exit_code(self):
        out = (
            "poindexter/x.py:1: error: INTERNAL ERROR -- Please try using mypy master on GitHub:\n"
            "Found 1 error in 1 file (checked 907 source files)\n"
        )
        with pytest.raises(LINT.MypyRunError, match="crashed"):
            _parse(1, out)

    def test_a_traceback_on_stderr_is_a_failure(self):
        with pytest.raises(LINT.MypyRunError, match="crashed"):
            _parse(
                0,
                "Success: no issues found in 907 source files\n",
                "Traceback (most recent call last):\n  File ...\nKeyError: 'x'\n",
            )

    def test_an_error_without_a_code_is_a_failure(self):
        """With `hide_error_codes`, errors can't be keyed per code. Counting
        them under a made-up key would let one kind stand in for another."""
        out = 'main.py:3: error: Name "x" is not defined\nFound 1 error in 1 file (checked 9 source files)\n'
        with pytest.raises(LINT.MypyRunError, match=r"no `\[code\]`"):
            _parse(1, out)

    def test_an_error_without_a_line_number_is_a_failure(self):
        out = (
            'poindexter/x.py: error: Duplicate module named "x"\n'
            "Found 1 error in 1 file (checked 9 source files)\n"
        )
        with pytest.raises(LINT.MypyRunError, match="no `path:line:` location"):
            _parse(1, out)

    def test_parsed_count_must_match_the_summary(self):
        """The vendor-format guard: an error line in a shape the regex misses
        makes the parsed count fall short of mypy's own tally."""
        out = TWO_FILE_RUN.replace("Found 3 errors", "Found 4 errors")
        with pytest.raises(LINT.MypyRunError, match="disagree"):
            _parse(1, out)

    def test_parsed_file_count_must_match_the_summary(self):
        out = TWO_FILE_RUN.replace("in 2 files", "in 3 files")
        with pytest.raises(LINT.MypyRunError, match="disagree"):
            _parse(1, out)

    def test_no_summary_line_is_a_failure(self):
        out = "\n".join(TWO_FILE_RUN.splitlines()[:-1]) + "\n"
        with pytest.raises(LINT.MypyRunError, match="no summary line"):
            _parse(1, out)

    def test_exit_0_with_an_error_line_is_a_failure(self):
        out = "main.py:3: error: x  [misc]\nSuccess: no issues found in 9 source files\n"
        with pytest.raises(LINT.MypyRunError, match="did not report a clean run"):
            _parse(0, out)

    def test_exit_0_with_a_found_summary_is_a_failure(self):
        with pytest.raises(LINT.MypyRunError, match="did not report a clean run"):
            _parse(0, TWO_FILE_RUN)


# ---------------------------------------------------------------------------
# ratchet arithmetic
# ---------------------------------------------------------------------------

FILE = PREFIX + "poindexter/brain/alert_sync.py"


class TestCounts:
    def test_keyed_per_file_per_code(self):
        errors = [
            _error(FILE, "assignment"),
            _error(FILE, "assignment", line=9),
            _error(FILE, "operator"),
            _error(PREFIX + "main.py", "arg-type"),
        ]
        assert LINT.counts_from_errors(errors) == {
            FILE: {"assignment": 2, "operator": 1},
            PREFIX + "main.py": {"arg-type": 1},
        }

    def test_moving_an_error_to_another_line_does_not_churn_the_counts(self):
        """No line numbers in the key: an edit above an old error must leave
        the baseline alone."""
        before = LINT.counts_from_errors([_error(FILE, "assignment", line=423)])
        after = LINT.counts_from_errors([_error(FILE, "assignment", line=431)])
        assert before == after


class TestFindRegressions:
    def test_new_code_rides_in_behind_nothing(self):
        """The case per-code keys exist for. The file fixes one [assignment]
        and gains one [arg-type], so its TOTAL is unchanged. A per-file count
        would see 2 <= 2 and wave it through."""
        counts = {FILE: {"assignment": 1, "arg-type": 1}}
        baseline = {FILE: {"assignment": 2}}
        assert sum(counts[FILE].values()) == sum(baseline[FILE].values())
        assert LINT.find_regressions(counts, baseline) == [(FILE, "arg-type", 1, 0)]

    def test_count_increase_is_a_regression(self):
        assert LINT.find_regressions({FILE: {"operator": 6}}, {FILE: {"operator": 5}}) == [
            (FILE, "operator", 6, 5)
        ]

    def test_new_file_is_a_regression(self):
        assert LINT.find_regressions({FILE: {"misc": 1}}, {}) == [(FILE, "misc", 1, 0)]

    def test_equal_is_clean(self):
        assert LINT.find_regressions({FILE: {"misc": 2}}, {FILE: {"misc": 2}}) == []

    def test_fewer_is_clean_the_ratchet_only_shrinks(self):
        assert LINT.find_regressions({FILE: {"misc": 1}}, {FILE: {"misc": 2}}) == []
        assert LINT.find_regressions({}, {FILE: {"misc": 2}}) == []


class TestFindStale:
    def test_lists_every_entry_below_its_baseline(self):
        counts = {FILE: {"assignment": 4}}
        baseline = {FILE: {"assignment": 6, "operator": 5}, PREFIX + "main.py": {"misc": 1}}
        assert LINT.find_stale(counts, baseline) == [
            (PREFIX + "main.py", "misc", 0, 1),
            (FILE, "assignment", 4, 6),
            (FILE, "operator", 0, 5),
        ]

    def test_at_or_above_baseline_is_not_stale(self):
        assert LINT.find_stale({FILE: {"misc": 3}}, {FILE: {"misc": 2}}) == []
        assert LINT.find_stale({FILE: {"misc": 2}}, {FILE: {"misc": 2}}) == []


class TestValidateBaseline:
    @pytest.mark.parametrize(
        "bad",
        [
            [],
            {"src\\cofounder_agent\\main.py": {"misc": 1}},
            {"/abs/main.py": {"misc": 1}},
            {PREFIX + "main.py": {}},
            {PREFIX + "main.py": {"Not A Code": 1}},
            {PREFIX + "main.py": {"misc": 0}},
            {PREFIX + "main.py": {"misc": -1}},
            {PREFIX + "main.py": {"misc": True}},
            {PREFIX + "main.py": {"misc": "1"}},
        ],
    )
    def test_malformed_baselines_are_rejected(self, bad):
        with pytest.raises(ValueError):
            LINT.validate_baseline(bad)

    def test_missing_baseline_allows_nothing(self, tmp_path):
        """An absent file must fail loud (allow zero), never permit everything."""
        assert LINT.load_baseline(tmp_path / "absent.json") == {}


# ---------------------------------------------------------------------------
# main(): the check and the shrink-only update
# ---------------------------------------------------------------------------


@pytest.fixture
def run_main(monkeypatch, tmp_path):
    """Drive main() with a canned mypy report and a scratch baseline file."""
    baseline_path = tmp_path / "mypy_baseline.json"
    monkeypatch.setattr(LINT, "BASELINE_PATH", baseline_path)

    def _run(argv, *, errors=(), checked=907, baseline=None, scan_error=None):
        if baseline is not None:
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

        def fake_scan(**_kwargs):
            if scan_error is not None:
                raise scan_error
            return _report(errors, checked=checked)

        monkeypatch.setattr(LINT, "scan", fake_scan)
        return LINT.main(argv)

    _run.baseline_path = baseline_path
    return _run


class TestMain:
    def test_clean_tree_exits_0(self, run_main, capsys):
        rc = run_main([], errors=[_error(FILE, "misc")], baseline={FILE: {"misc": 1}})
        assert rc == 0
        assert "clean, no new errors (1 found / 1 baselined" in capsys.readouterr().out

    def test_regression_exits_1_and_names_the_candidate_lines(self, run_main, capsys):
        errors = [
            _error(FILE, "misc", line=10, message="old"),
            _error(FILE, "misc", line=20, message="new"),
        ]
        rc = run_main([], errors=errors, baseline={FILE: {"misc": 1}})
        out = capsys.readouterr().out
        assert rc == 1
        assert f"{FILE}: [misc] = 2 error(s), baseline allows 1" in out
        assert f"{FILE}:10: old" in out and f"{FILE}:20: new" in out
        assert "type: ignore[<code>]" in out

    def test_below_baseline_is_clean_and_says_so(self, run_main, capsys):
        rc = run_main([], errors=[], baseline={FILE: {"misc": 2}})
        out = capsys.readouterr().out
        assert rc == 0
        assert "re-baseline to lock the win in" in out
        assert f"below baseline: {FILE}: [misc] 0 found, baseline allows 2" in out

    def test_update_refuses_growth_and_leaves_the_file_alone(self, run_main, capsys):
        baseline = {FILE: {"misc": 1}}
        rc = run_main(
            ["--update-baseline"],
            errors=[_error(FILE, "misc"), _error(FILE, "arg-type")],
            baseline=baseline,
        )
        assert rc == 1
        assert json.loads(run_main.baseline_path.read_text(encoding="utf-8")) == baseline
        assert "only shrinks" in capsys.readouterr().out

    def test_update_shrinks_and_drops_fixed_entries(self, run_main):
        rc = run_main(
            ["--update-baseline"],
            errors=[_error(FILE, "assignment")],
            baseline={FILE: {"assignment": 3, "operator": 2}, PREFIX + "main.py": {"misc": 1}},
        )
        assert rc == 0
        assert json.loads(run_main.baseline_path.read_text(encoding="utf-8")) == {
            FILE: {"assignment": 1}
        }

    def test_update_with_allow_growth_records_new_errors(self, run_main, capsys):
        rc = run_main(
            ["--update-baseline", "--allow-growth"],
            errors=[_error(FILE, "misc"), _error(FILE, "arg-type")],
            baseline={FILE: {"misc": 1}},
        )
        assert rc == 0
        assert json.loads(run_main.baseline_path.read_text(encoding="utf-8")) == {
            FILE: {"arg-type": 1, "misc": 1}
        }
        assert "grew by 1" in capsys.readouterr().out

    def test_a_malformed_baseline_fails_with_a_message_not_a_traceback(self, run_main, capsys):
        run_main.baseline_path.write_text("{not json", encoding="utf-8")
        assert run_main([], errors=[_error(FILE, "misc")]) == 1
        assert "mypy_baseline.json is malformed" in capsys.readouterr().err

    def test_allow_growth_without_update_is_a_usage_error(self, run_main):
        with pytest.raises(SystemExit) as exc:
            run_main(["--allow-growth"])
        assert exc.value.code == 2

    def test_an_untrustworthy_run_exits_1_and_writes_nothing(self, run_main, capsys):
        rc = run_main(
            ["--update-baseline", "--allow-growth"], scan_error=LINT.MypyRunError("mypy exited 2")
        )
        assert rc == 1
        assert not run_main.baseline_path.exists()
        assert "no trustworthy result" in capsys.readouterr().err

    def test_a_thin_run_trips_the_floor_before_any_baseline_is_written(self, run_main):
        with pytest.raises(SystemExit) as exc:
            run_main(["--update-baseline", "--allow-growth"], errors=[], checked=12)
        assert exc.value.code == 1
        assert not run_main.baseline_path.exists()


# ---------------------------------------------------------------------------
# scan floor
# ---------------------------------------------------------------------------


class TestScanFloor:
    def test_missing_source_root_fails(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            LINT.scan(
                source_root=tmp_path / "src" / "cofounder_agent",
                config=REPO_ROOT / "pyproject.toml",
            )
        assert exc.value.code == 1

    def test_zero_files_checked_fails(self):
        with pytest.raises(SystemExit):
            LINT.check_floor(_report(checked=0))

    def test_far_fewer_files_than_the_tree_holds_fails(self):
        with pytest.raises(SystemExit):
            LINT.check_floor(_report(checked=LINT.MIN_FILES_CHECKED - 1))

    def test_a_full_run_passes_the_floor(self):
        assert LINT.check_floor(_report(checked=907)) == 907

    def test_floor_is_well_under_what_the_tree_holds(self):
        """The floor must trip on a collapse, not on ordinary deletions. The
        poindexter/ package alone is most of what mypy checks."""
        on_disk = [
            p
            for p in (SOURCE_ROOT / "poindexter").rglob("*.py")
            if "tests" not in p.relative_to(SOURCE_ROOT).parts
        ]
        assert len(on_disk) >= 1.5 * LINT.MIN_FILES_CHECKED, (
            f"only {len(on_disk)} checkable files against a floor of "
            f"{LINT.MIN_FILES_CHECKED}. Re-derive the floor rather than letting "
            "it trip on ordinary change."
        )


# ---------------------------------------------------------------------------
# real mypy: the positive control
# ---------------------------------------------------------------------------


def _synthetic_tree(root: Path, files: dict[str, str]) -> Path:
    """A tiny repo configured by the REAL root [tool.mypy], so the run below
    has production settings and only the tree is synthetic."""
    shutil.copy2(REPO_ROOT / "pyproject.toml", root / "pyproject.toml")
    source_root = root / "src" / "cofounder_agent"
    for rel, body in files.items():
        target = source_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return source_root


KNOWN_ERRORS = {
    "pkg/__init__.py": "",
    "pkg/bad.py": (
        "def takes_int(value: int) -> int:\n"
        "    return value\n\n\n"
        "def caller() -> None:\n"
        '    takes_int("not an int")\n'
        '    count: int = "three"\n'
        '    takes_int("again")\n'
        "    del count\n"
    ),
    # An error with a note under it: the note must not be counted.
    "pkg/notes.py": "def maybe(value: int | None) -> int:\n    return value + 1\n",
    "pkg/clean.py": "def ok() -> int:\n    return 1\n",
}


class TestRealMypy:
    def test_the_parser_reads_what_this_mypy_prints(self, tmp_path):
        source_root = _synthetic_tree(tmp_path, KNOWN_ERRORS)
        report = LINT.scan(
            source_root=source_root, config=tmp_path / "pyproject.toml", repo_root=tmp_path
        )
        assert report.checked == 4
        assert LINT.counts_from_errors(report.errors) == {
            "src/cofounder_agent/pkg/bad.py": {"arg-type": 2, "assignment": 1},
            "src/cofounder_agent/pkg/notes.py": {"operator": 1},
        }

    def test_a_blocking_error_is_a_failure_not_a_clean_run(self, tmp_path):
        """A syntax error makes mypy stop with exit 2 before it type-checks
        anything else. That must never read as clean."""
        files = dict(KNOWN_ERRORS, **{"pkg/syntax.py": "def broken(:\n"})
        source_root = _synthetic_tree(tmp_path, files)
        with pytest.raises(LINT.MypyRunError, match="exited 2"):
            LINT.scan(
                source_root=source_root, config=tmp_path / "pyproject.toml", repo_root=tmp_path
            )

    def test_a_tree_that_is_too_small_fails_the_floor_through_the_real_cli(self, tmp_path):
        """End to end through the script: a clean mypy run over four files is
        a scan that barely happened, not a pass."""
        _synthetic_tree(
            tmp_path, {"pkg/__init__.py": "", "pkg/clean.py": KNOWN_ERRORS["pkg/clean.py"]}
        )
        ci = tmp_path / "scripts" / "ci"
        ci.mkdir(parents=True)
        for name in ("mypy_lint.py", "lib_scan_floor.py"):
            shutil.copy2(REPO_ROOT / "scripts" / "ci" / name, ci / name)
        proc = subprocess.run(
            [sys.executable, str(ci / "mypy_lint.py")],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "mypy checked only 2 source file(s)" in proc.stderr


# ---------------------------------------------------------------------------
# one config
# ---------------------------------------------------------------------------


class TestOneMypyConfig:
    def test_the_package_pyproject_carries_no_mypy_table(self):
        data = tomllib.loads((SOURCE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert "mypy" not in data.get("tool", {}), (
            "src/cofounder_agent/pyproject.toml has a [tool.mypy] again. mypy's "
            "config discovery finds it before the root one for any bare run from "
            "that directory, so the two would silently disagree. Put settings in "
            "the repo-root pyproject.toml."
        )

    def test_the_lint_runs_the_root_config_from_the_package_dir(self):
        """The same invocation as `npm run type:check`: the root config, with
        mypy's working directory at the package root that `mypy_path` names."""
        assert LINT.CONFIG_PATH == REPO_ROOT / "pyproject.toml"
        assert LINT.SOURCE_ROOT == SOURCE_ROOT
        mypy = tomllib.loads(LINT.CONFIG_PATH.read_text(encoding="utf-8"))["tool"]["mypy"]
        assert mypy["mypy_path"] == "$MYPY_CONFIG_FILE_DIR/src/cofounder_agent"
        assert mypy["explicit_package_bases"] is True

    def test_bare_mypy_from_the_package_dir_discovers_the_root_config(self, monkeypatch):
        """Behavioural, through mypy's own discovery: from src/cofounder_agent
        with no --config-file, mypy must land on the repo-root pyproject.toml.
        Any mypy.ini, .mypy.ini, setup.cfg [mypy] or [tool.mypy] in between
        would win instead, and this fails."""
        found = _discovered_config(monkeypatch, SOURCE_ROOT)
        assert found == REPO_ROOT / "pyproject.toml"

    def test_discovery_control_a_nested_table_would_win(self, monkeypatch, tmp_path):
        """Positive control for the test above: in a scratch repo, a nested
        pyproject.toml WITH [tool.mypy] is what discovery returns."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n", encoding="utf-8")
        nested = tmp_path / "src" / "pkg"
        nested.mkdir(parents=True)
        (nested / "pyproject.toml").write_text("[tool.mypy]\nstrict = false\n", encoding="utf-8")
        assert _discovered_config(monkeypatch, nested) == nested / "pyproject.toml"


def _discovered_config(monkeypatch, cwd: Path) -> Path | None:
    from mypy.config_parser import parse_config_file
    from mypy.options import Options

    # parse_config_file exports MYPY_CONFIG_FILE_DIR as a side effect.
    monkeypatch.setenv("MYPY_CONFIG_FILE_DIR", os.environ.get("MYPY_CONFIG_FILE_DIR", ""))
    monkeypatch.chdir(cwd)
    options = Options()
    parse_config_file(options, lambda: None, None)
    if options.config_file is None:
        return None
    return (cwd / options.config_file).resolve()


# ---------------------------------------------------------------------------
# the committed baseline
# ---------------------------------------------------------------------------


class TestCommittedBaseline:
    def test_is_wellformed(self):
        LINT.validate_baseline(json.loads(LINT.BASELINE_PATH.read_text(encoding="utf-8")))

    def test_every_key_names_a_file_that_exists(self):
        """A key for a deleted or moved file is dead weight the ratchet can
        never enforce. Re-baseline when a file with errors moves."""
        for rel in nonempty(LINT.load_baseline(), "LINT.load_baseline()"):
            assert (REPO_ROOT / rel).is_file(), f"{rel} is in the baseline but not in the tree"

    def test_every_key_is_under_the_scanned_tree(self):
        for rel in nonempty(LINT.load_baseline(), "LINT.load_baseline()"):
            assert rel.startswith(PREFIX), rel
            assert "/tests/" not in rel, f"{rel}: tests/ is excluded from the scan"


# ---------------------------------------------------------------------------
# CI wiring
# ---------------------------------------------------------------------------


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML reads the bare key `on` as boolean True (YAML 1.1).
    return workflow.get("on", workflow.get(True)) or {}


def _mypy_job() -> tuple[str, dict]:
    jobs = _workflow("python-lint.yml")["jobs"]
    matches = [
        (name, job)
        for name, job in jobs.items()
        if any(
            "scripts/ci/mypy_lint.py" in (step.get("run") or "") for step in job.get("steps", [])
        )
    ]
    assert len(matches) == 1, (
        f"expected exactly one job running mypy_lint.py, found {[m[0] for m in matches]}"
    )
    return matches[0]


def _install_step(job: dict) -> dict:
    steps = [s for s in job.get("steps", []) if "poetry install" in (s.get("run") or "")]
    assert len(steps) == 1, f"expected one `poetry install` step, found {len(steps)}"
    return steps[0]


class TestCiWiring:
    def test_python_lint_has_no_paths_filter(self):
        """A path-filtered workflow never reports on an unrelated PR, so a
        required check on it hangs the PR forever. Keep this promotable."""
        for event, spec in nonempty(
            _triggers(_workflow("python-lint.yml")).items(), "python-lint triggers"
        ):
            assert not (isinstance(spec, dict) and ({"paths", "paths-ignore"} & spec.keys())), event

    def test_the_job_runs_the_lint_with_the_backend_venv_interpreter(self):
        _, job = _mypy_job()
        run = next(
            s["run"] for s in job["steps"] if "scripts/ci/mypy_lint.py" in (s.get("run") or "")
        )
        assert "src/cofounder_agent/.venv/bin/python scripts/ci/mypy_lint.py" in run

    def test_the_job_installs_into_an_isolated_in_project_venv(self):
        """The self-hosted runners persist their interpreter between jobs, and
        other jobs install extra packages into it. mypy's result depends on
        what is installed, so the lint gets a venv of its own."""
        _, job = _mypy_job()
        step = _install_step(job)
        env = {k: str(v).lower() for k, v in (step.get("env") or {}).items()}
        assert env.get("POETRY_VIRTUALENVS_CREATE") == "true"
        assert env.get("POETRY_VIRTUALENVS_IN_PROJECT") == "true"
        assert step.get("working-directory") == "src/cofounder_agent"

    def test_the_job_installs_what_the_unit_tests_install(self):
        """Derived from unit-tests.yml, not hand-listed: the baseline was
        measured in the env the unit tests run in."""
        unit_step = _install_step(_workflow("unit-tests.yml")["jobs"]["test-backend"])
        want = re.search(r'--extras "([^"]+)"', unit_step["run"])
        assert want, "unit-tests.yml's install no longer passes --extras"
        _, job = _mypy_job()
        got = re.search(r'--extras "([^"]+)"', _install_step(job)["run"])
        assert got and got.group(1) == want.group(1)

    def test_poetry_pin_matches_unit_tests(self):
        unit = (WORKFLOWS / "unit-tests.yml").read_text(encoding="utf-8")
        pin = re.search(r'pip install "poetry==([^"]+)"', unit)
        assert pin, "unit-tests.yml no longer pins poetry"
        lint = _workflow("python-lint.yml")
        assert str(lint["env"]["POETRY_VERSION"]) == pin.group(1)

    def test_the_job_skips_the_public_mirror_like_its_siblings(self):
        _, job = _mypy_job()
        assert job.get("if") == "github.repository != 'Glad-Labs/poindexter'"
