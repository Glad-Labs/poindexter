"""Guards for ``scripts/ci/unit_test_dirs_lint.py``.

CI runs pytest once per ``tests/unit`` directory, from a hand-kept list in
``.github/workflows/unit-tests.yml``. On 2026-09-25 three directories were on
no list, so nothing in them had ever gated a PR, and ``tests/unit/seo`` held a
real failure (#4011's composed-plan rule also rejected the seeded
``seo_refresh`` graph). The lint makes the list checked instead of remembered.

Two properties matter, and each has tests here:

* It must not PASS something that does not run: an option's value, an echoed
  command, a heredoc, a ``$( ... )`` subshell, an ``--ignore``, or a step that
  cannot fail the job must never count as coverage.
* Its stdlib YAML scanner must read the real workflows the way a YAML parser
  does. It is compared against PyYAML on every workflow in the repo, because a
  scanner that silently misreads a step is the disarmed-gate failure the
  scan-floor doctrine exists to prevent.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_LINT_REL = Path("scripts") / "ci" / "unit_test_dirs_lint.py"


def _repo_root() -> Path:
    return next(p for p in Path(__file__).resolve().parents if (p / _LINT_REL).is_file())


def _load():
    path = _repo_root() / _LINT_REL
    spec = importlib.util.spec_from_file_location("unit_test_dirs_lint", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[spec.name] = mod  # type: ignore[union-attr]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


lint = _load()


# ---------------------------------------------------------------------------
# A fake repo: tests/unit/{a,b}/ + one root-level file, and a workflow
# ---------------------------------------------------------------------------

_STEP = """\
      - name: {name}
        working-directory: src/cofounder_agent
        run: {run}
"""


def _tree(tmp_path: Path, workflow: str, dirs=("a", "b")) -> Path:
    tests = tmp_path / "src" / "cofounder_agent" / "tests" / "unit"
    for d in dirs:
        (tests / d).mkdir(parents=True)
        (tests / d / f"test_{d}.py").write_text("def test_x():\n    pass\n")
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_root.py").write_text("def test_x():\n    pass\n")
    wf = tmp_path / ".github" / "workflows" / "unit-tests.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text(workflow)
    return tmp_path


def _workflow(*steps: str) -> str:
    return "name: unit-tests\njobs:\n  test-backend:\n    runs-on: ubuntu-latest\n    steps:\n" + "".join(steps)


def _step(name: str, run: str) -> str:
    return _STEP.format(name=name, run=run)


_ROOT_STEP = _step("root", "$PYTEST tests/unit/test_*.py -q --forked")
_A_STEP = _step("a", "$PYTEST tests/unit/a/ -q -p no:cacheprovider $COV")


def _run(tmp_path: Path, workflow: str, capsys) -> tuple[int, str]:
    code = lint.main(_tree(tmp_path, workflow))
    return code, capsys.readouterr().out


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_a_directory_no_step_names_is_reported_with_the_fix(tmp_path, capsys):
    code, out = _run(tmp_path, _workflow(_A_STEP, _ROOT_STEP), capsys)

    assert code == 1
    assert "tests/unit/b/" in out
    assert "no pytest step names it" in out
    assert "tests/unit/a/" not in out.split("Fix:")[0]
    # The fix is printed ready to paste, in the workflow's own shape.
    assert "run: $PYTEST tests/unit/b/ -q --tb=short -p no:cacheprovider $COV" in out


def test_every_directory_named_passes(tmp_path, capsys):
    b = _step("b", "$PYTEST tests/unit/b/ -q")
    code, out = _run(tmp_path, _workflow(_A_STEP, b, _ROOT_STEP), capsys)

    assert code == 0, out
    assert "OK" in out and "3 test file(s)" in out


def test_the_root_level_glob_is_what_covers_root_files(tmp_path, capsys):
    b = _step("b", "$PYTEST tests/unit/b/ -q")
    code, out = _run(tmp_path, _workflow(_A_STEP, b), capsys)

    assert code == 1
    assert "tests/unit/test_*.py" in out


@pytest.mark.parametrize(
    ("run", "reason"),
    [
        ("$PYTEST tests/unit/ --ignore=tests/unit/b", "excluded"),
        ("$PYTEST tests/unit/ --ignore tests/unit/b", "excluded"),
        ("$PYTEST tests/unit/ --ignore-glob=tests/unit/b/*", "excluded"),
        ("$PYTEST tests/unit/ --deselect tests/unit/b/test_b.py", "excluded"),
    ],
)
def test_an_excluded_path_is_not_covered(tmp_path, capsys, run, reason):
    code, out = _run(tmp_path, _workflow(_step("all", run)), capsys)

    assert code == 1
    assert "tests/unit/b/" in out and reason in out
    assert "tests/unit/a/" not in out


def test_deselecting_one_test_does_not_uncover_its_file(tmp_path, capsys):
    run = "$PYTEST tests/unit/ --deselect tests/unit/b/test_b.py::test_x"
    code, out = _run(tmp_path, _workflow(_step("all", run)), capsys)

    assert code == 0, out


@pytest.mark.parametrize(
    "gate",
    ["continue-on-error: true", "if: false", "if: ${{ false }}", "continue-on-error: ${{ matrix.soft }}"],
)
def test_a_step_that_cannot_fail_the_job_does_not_count(tmp_path, capsys, gate):
    soft = (
        "      - name: b soft\n"
        f"        {gate}\n"
        "        working-directory: src/cofounder_agent\n"
        "        run: $PYTEST tests/unit/b/\n"
    )
    code, out = _run(tmp_path, _workflow(_A_STEP, soft, _ROOT_STEP), capsys)

    assert code == 1
    assert "cannot fail the job" in out and "b soft" in out


def test_a_job_level_continue_on_error_does_not_count(tmp_path, capsys):
    workflow = (
        "jobs:\n  soft:\n    continue-on-error: true\n    runs-on: x\n    steps:\n"
        + _step("all", "$PYTEST tests/unit/")
    )
    code, out = _run(tmp_path, workflow, capsys)

    assert code == 1
    assert "cannot fail the job" in out


# ---------------------------------------------------------------------------
# Things that mention pytest but do not run it must never count
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "run",
    [
        '|\n          echo "$PYTEST tests/unit/b/"',
        "|\n          # $PYTEST tests/unit/b/",
        "|\n          cat > notes.md <<'BODY'\n          $PYTEST tests/unit/b/\n          BODY",
        "|\n          out=$(python -m pytest tests/unit/b/ --collect-only)",
        # An option's value is never a target, even when it names a test dir.
        "$PYTEST tests/unit/a/ --rootdir tests/unit/b",
        "$PYTEST tests/unit/a/ -p tests/unit/b",
    ],
    ids=["echo", "comment", "heredoc", "subshell", "--rootdir value", "-p value"],
)
def test_a_mention_is_not_a_run(tmp_path, capsys, run):
    code, out = _run(tmp_path, _workflow(_A_STEP, _step("mention", run), _ROOT_STEP), capsys)

    assert code == 1, out
    assert "tests/unit/b/" in out


@pytest.mark.parametrize(
    "run",
    [
        "python -m pytest tests/unit/b/ -q",
        "poetry run pytest tests/unit/b/",
        "PYTHONPATH=. $PYTEST tests/unit/b/",
        "|\n          $PYTEST \\\n            tests/unit/b/ -q",
        # The modules step's shape: exit-code capture on the same line.
        '|\n          $PYTEST tests/unit/b/ -q || code=$?\n          exit "${code:-0}"',
        # A here-string is not a heredoc: the next line is still read.
        '|\n          n=$(grep -c x <<<"$changed")\n          $PYTEST tests/unit/b/',
        "|\n          cat <<EOF\n          text\n          EOF\n          $PYTEST tests/unit/b/",
    ],
    ids=["python -m", "poetry run", "env prefix", "continuation", "|| capture", "here-string", "after heredoc"],
)
def test_real_invocations_are_read(tmp_path, capsys, run):
    code, out = _run(tmp_path, _workflow(_A_STEP, _step("b", run), _ROOT_STEP), capsys)

    assert code == 0, out


def test_a_path_is_resolved_against_the_steps_working_directory(tmp_path, capsys):
    from_root = "      - name: b from the repo root\n        run: $PYTEST src/cofounder_agent/tests/unit/b/\n"
    wrong_dir = "      - name: b wrong dir\n        run: $PYTEST tests/unit/b/\n"  # no working-directory

    code, _ = _run(tmp_path / "ok", _workflow(_A_STEP, from_root, _ROOT_STEP), capsys)
    assert code == 0

    code, out = _run(tmp_path / "bad", _workflow(_A_STEP, wrong_dir, _ROOT_STEP), capsys)
    assert code == 1 and "tests/unit/b/" in out


def test_a_compact_sequence_and_folded_run_are_read(tmp_path, capsys):
    workflow = (
        "jobs:\n  t:\n    runs-on: x\n    steps:\n"
        "    - name: a\n      working-directory: src/cofounder_agent\n      run: $PYTEST tests/unit/a/\n"
        "    - name: b\n      working-directory: src/cofounder_agent\n      run: >\n"
        "        $PYTEST\n        tests/unit/b/ -q\n"
        "    -\n      working-directory: src/cofounder_agent\n      run: $PYTEST tests/unit/test_root.py\n"
    )
    code, out = _run(tmp_path, workflow, capsys)

    assert code == 0, out


# ---------------------------------------------------------------------------
# Scan floor: a lint that read nothing has not passed
# ---------------------------------------------------------------------------


def test_a_workflow_with_no_pytest_steps_is_a_floor_failure(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        lint.main(_tree(tmp_path, _workflow(_step("lint", "python scripts/ci/x.py"))))

    assert exc.value.code == 1
    assert "examined 0 pytest commands" in capsys.readouterr().err


def test_a_tests_root_with_no_test_files_is_a_floor_failure(tmp_path, capsys):
    root = _tree(tmp_path, _workflow(_A_STEP), dirs=())
    (root / "src" / "cofounder_agent" / "tests" / "unit" / "test_root.py").unlink()

    with pytest.raises(SystemExit) as exc:
        lint.main(root)

    assert exc.value.code == 1
    assert "examined 0 test_*.py files" in capsys.readouterr().err


def test_a_missing_workflow_is_a_floor_failure(tmp_path, capsys):
    root = _tree(tmp_path, _workflow(_A_STEP))
    (root / ".github" / "workflows" / "unit-tests.yml").unlink()

    with pytest.raises(SystemExit) as exc:
        lint.main(root)

    assert exc.value.code == 1
    assert "workflow not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The real repo
# ---------------------------------------------------------------------------


def _real_workflow() -> Path:
    path = _repo_root() / ".github" / "workflows" / "unit-tests.yml"
    if not path.is_file():
        pytest.skip("unit-tests.yml absent from this tree")
    return path


def test_the_real_workflow_runs_every_unit_test_file(capsys):
    _real_workflow()

    assert lint.main(_repo_root()) == 0, capsys.readouterr().out


def test_the_real_workflow_without_the_seo_step_reports_seo():
    """The 2026-09-25 state, reconstructed: seo/ on no list."""
    text = _real_workflow().read_text(encoding="utf-8")
    without = re.sub(r"\n      - name: Unit tests — seo\n(?:        .*\n)+", "\n", text)
    assert without != text, "the seo step moved; update this test's pattern"

    root = _repo_root()
    tests_root = root / lint.TESTS_ROOT_REL
    problems = lint.uncovered(
        lint.discover_test_files(tests_root), lint.find_invocations(without, root, tests_root), tests_root
    )

    assert [group for group, _reason in problems] == ["tests/unit/seo/"]


def _yaml_steps(path: Path) -> list[tuple[str, dict]]:
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for job, body in (data.get("jobs") or {}).items():
        for step in (body or {}).get("steps") or []:
            out.append((str(job), step))
    return out


@pytest.mark.parametrize(
    "workflow",
    sorted((_repo_root() / ".github" / "workflows").glob("*.yml")),
    ids=lambda p: p.name,
)
def test_the_scanner_reads_each_real_workflow_like_a_yaml_parser(workflow):
    """Same steps, same ``run`` text, same working directory as PyYAML."""
    expected = _yaml_steps(workflow)
    actual = lint.scan_steps(workflow.read_text(encoding="utf-8"))

    assert [s.job for s in actual] == [job for job, _ in expected]
    for got, (_job, want) in zip(actual, expected, strict=True):
        want_run = str(want.get("run") or "")
        where = f"{workflow.name}:{got.line} ({got.name})"
        assert got.run.strip() == want_run.strip(), where
        assert got.working_directory == str(want.get("working-directory") or ""), where
        if "name" in want:
            assert got.name == str(want["name"]), where


def test_the_yaml_comparison_saw_the_unit_test_workflow():
    """Guard the guard: an empty parametrization above would pass vacuously."""
    names = {p.name for p in (_repo_root() / ".github" / "workflows").glob("*.yml")}

    assert "unit-tests.yml" in names
    assert len(lint.scan_steps(_real_workflow().read_text(encoding="utf-8"))) >= 40


def test_the_fix_snippet_matches_the_real_steps():
    """The pasted fix should look like its neighbours, not drift from them."""
    text = _real_workflow().read_text(encoding="utf-8")

    assert lint._snippet("tests/unit/seo/") in text
