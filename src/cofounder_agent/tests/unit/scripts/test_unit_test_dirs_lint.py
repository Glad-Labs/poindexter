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
import os
import re
import shutil
import subprocess
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


@pytest.mark.parametrize("gate", ["if: false", "if: ${{ false }}"])
def test_a_job_that_never_runs_does_not_count(tmp_path, capsys, gate):
    """A job-level `if: false` skips every step, and a skipped job still
    satisfies a required check, so all of its tests would gate nothing."""
    workflow = (
        f"jobs:\n  off:\n    {gate}\n    runs-on: x\n    steps:\n" + _step("all", "$PYTEST tests/unit/")
    )
    code, out = _run(tmp_path, workflow, capsys)

    assert code == 1
    assert "cannot fail the job" in out and "on the step or its job" in out


def test_a_job_whose_if_may_be_true_still_counts(tmp_path, capsys):
    workflow = (
        "jobs:\n  t:\n    if: ${{ !(github.event_name == 'push') }}\n    runs-on: x\n    steps:\n"
        + _step("all", "$PYTEST tests/unit/")
    )
    code, out = _run(tmp_path, workflow, capsys)

    assert code == 0, out


# ---------------------------------------------------------------------------
# A step whose script discards pytest's exit status cannot fail the job
# ---------------------------------------------------------------------------

_P = "$PYTEST tests/unit/b/ -q"


def _block(*lines: str) -> str:
    """A ``run: |`` block scalar for :func:`_step`."""
    return "|\n" + "\n".join(f"          {line}" for line in lines)


@pytest.mark.parametrize(
    ("run", "why"),
    [
        (f"{_P} || true", "`|| true` runs in its place"),
        (f"{_P} || echo flaky", "`|| echo flaky` runs in its place"),
        # The default shell is `bash -e {0}`: no pipefail, so tee's 0 wins.
        (f"{_P} | tee pytest.log", "piped without pipefail"),
        (_block(f"{_P} &", "wait"), "run in the background"),
        (_block("set +e", _P, "echo done"), "`set +e` lets the script run"),
        (_block(f"{_P} || code=$?", 'echo "$code"'), "`|| code=$?` runs in its place"),
        (_block(f"{_P} && echo ok", "echo done"), "a failing `&&` list"),
    ],
    ids=["|| true", "|| echo", "| tee", "&", "set +e", "capture, no re-raise", "&& mid-script"],
)
def test_a_discarded_exit_status_does_not_count(tmp_path, capsys, run, why):
    code, out = _run(tmp_path, _workflow(_A_STEP, _step("b soft", run), _ROOT_STEP), capsys)

    assert code == 1, out
    assert "tests/unit/b/" in out and "discards pytest's exit status" in out
    assert why in out and "b soft" in out
    assert "Fix for a discarded status" in out
    assert "tests/unit/a/" not in out.split("Fix")[0]


def _shell_step(name: str, run: str, shell: str) -> str:
    return (
        f"      - name: {name}\n"
        f"        shell: {shell}\n"
        "        working-directory: src/cofounder_agent\n"
        f"        run: {run}\n"
    )


_TEE = f"{_P} | tee pytest.log"


@pytest.mark.parametrize(
    "workflow",
    [
        _workflow(_A_STEP, _step("b", f"{_P} || exit 1"), _ROOT_STEP),
        _workflow(_A_STEP, _step("b", f'{_P} || {{ echo "::error::b"; exit 1; }}'), _ROOT_STEP),
        _workflow(_A_STEP, _step("b", _block("set -o pipefail", _TEE)), _ROOT_STEP),
        # `shell: bash` is `bash -eo pipefail`, from the step, job or workflow.
        _workflow(_A_STEP, _shell_step("b", _TEE, "bash"), _ROOT_STEP),
        (
            "jobs:\n  t:\n    runs-on: x\n    defaults:\n      run:\n        shell: bash\n    steps:\n"
            + _A_STEP + _step("b", _TEE) + _ROOT_STEP
        ),
        "defaults:\n  run:\n    shell: bash\n" + _workflow(_A_STEP, _step("b", _TEE), _ROOT_STEP),
    ],
    ids=[
        "|| exit 1", "|| { ...; exit 1; }", "set -o pipefail",
        "step shell: bash", "job defaults", "workflow defaults",
    ],
)
def test_a_status_that_reaches_the_step_counts(tmp_path, capsys, workflow):
    code, out = _run(tmp_path, workflow, capsys)

    assert code == 0, out


@pytest.mark.parametrize("shell", ["pwsh", "sh", "bash {0}"])
def test_a_step_shell_this_lint_does_not_read_does_not_count(tmp_path, capsys, shell):
    """`sh` is dash on Ubuntu (no pipefail; `cmd &> f` backgrounds cmd), and a
    custom template drops GitHub's `-e`: neither matches the model, so both
    are reported rather than guessed at."""
    workflow = _workflow(_A_STEP, _shell_step("b", _P, shell), _ROOT_STEP)
    code, out = _run(tmp_path, workflow, capsys)

    assert code == 1
    assert f"`shell: {shell}` is not a shell this lint reads" in out


# ---------------------------------------------------------------------------
# The exit-status model is held to a real shell
# ---------------------------------------------------------------------------

# How GitHub runs a `run` script on Linux, by `shell:` ("" = unspecified).
_GITHUB_SHELLS = {
    "": ["bash", "-e"],
    "bash": ["bash", "--noprofile", "--norc", "-eo", "pipefail"],
}
# (shell, script), with both verdicts, through each path the model follows a
# status down. The unread-shell branch cannot be run here; it is tested above.
_SHAPES = [
    ("", _P),
    ("", f"{_P}\necho done"),
    ("", f"{_P} || true"),
    ("", f"{_P} || :"),
    ("", f"{_P} || exit 0"),
    ("", f"{_P} || exit 256"),
    ("", f"{_P} || echo soft-fail"),
    ("", f"{_P} || false"),
    ("", f"{_P} || exit"),
    ("", f"{_P} || exit $?"),
    ("", f"{_P} || exit 1"),
    ("", f'{_P} || {{ echo "::error::b failed"; exit 1; }}'),
    ("", f'{_P} || {{ echo "::error::b failed"; }}'),
    ("", f'{_P} || {{ echo "::error::b failed"; exit; }}'),
    ("", f'{_P} || {{ code=$?; echo "$code"; exit "$code"; }}'),
    ("", f'{_P} || {{\n  echo "::error::b failed"\n  exit 1\n}}'),
    ("", f'{_P} || code=$?\nexit "${{code:-0}}"'),
    ("", f'{_P} || code=$?\necho "code=$code"'),
    # The modules step's exit-5 handling.
    ("", f'{_P} || code=$?\nif [ "${{code:-0}}" -eq 5 ]; then\n  exit 0\nfi\nexit "${{code:-0}}"'),
    ("", f"{_P} | tee log"),
    ("", f"{_P} 2>&1 | tee log"),
    ("", f"set -o pipefail\n{_P} | tee log"),
    ("", f"set -euo pipefail\n{_P} | tee log\necho done"),
    ("", f"{_P} 2>&1"),
    ("", f"{_P} > out.log 2>&1"),
    ("", f"{_P} &\nwait"),
    ("", f"set +e\n{_P}\necho done"),
    ("", f"set +e\n{_P}"),
    ("", f"set +e\n{_P}\nexit $?"),
    ("", f'set +e\n{_P}\ncode=$?\necho "code=$code"\nexit "$code"'),
    ("", f"set +o errexit\n{_P}\necho done"),
    ("", f"set +e\nset -e\n{_P}\necho done"),
    ("", f"{_P} && echo ok"),
    ("", f"{_P} && echo ok\necho done"),
    ("", f"{_P} && echo ok || exit 1\necho done"),
    ("", f"{_P} && echo ok || true\necho done"),
    ("", f"{_P} &> out.log"),
    ("bash", f"{_P} | tee log"),
    ("bash", f"{_P} | tee log\necho done"),
    ("bash", f"set +o pipefail\n{_P} | tee log"),
]


def _gates_per_lint(script: str, shell: str) -> tuple[bool, str]:
    commands = lint.shell_commands_with_ops(script)
    index = next(i for i, (command, _op) in enumerate(commands) if lint.pytest_args(command) is not None)
    why = lint.discarded_status(commands, index, shell)
    return why == "", why


def _fails_per_shell(tmp_path: Path, script: str, shell: str) -> tuple[bool, str]:
    """Run ``script`` the way GitHub would, with a pytest that fails."""
    argv = _GITHUB_SHELLS[shell]
    if shutil.which(argv[0]) is None:
        pytest.skip(f"{argv[0]} is not on PATH")
    path = tmp_path / "step.sh"
    path.write_text(script + "\n", encoding="utf-8")
    result = subprocess.run(
        [*argv, str(path)], cwd=tmp_path, env={"PATH": os.environ.get("PATH", ""), "PYTEST": "false"},
        capture_output=True, text=True, timeout=30, check=False,
    )
    return result.returncode != 0, f"exit {result.returncode} {result.stderr.strip()}"


def _shape_id(shape: tuple[str, str]) -> str:
    shell, script = shape
    return f"{shell or 'default'}: {script}".replace("\n", " / ")


@pytest.mark.parametrize(("shell", "script"), _SHAPES, ids=[_shape_id(s) for s in _SHAPES])
def test_the_exit_status_model_agrees_with_the_shell(tmp_path, shell, script):
    """A failing pytest fails the step exactly when the lint says the step gates."""
    gates, why = _gates_per_lint(script, shell)
    fails, ran = _fails_per_shell(tmp_path, script, shell)

    assert gates == fails, f"lint: {why or 'gates'} | shell: {ran}"


def test_the_shape_table_holds_both_verdicts():
    """Guard the guard: a table of one verdict would agree with a constant."""
    verdicts = {_gates_per_lint(script, shell)[0] for shell, script in _SHAPES}

    assert verdicts == {True, False}


def test_an_unread_hand_off_is_reported_even_though_the_shell_fails():
    """Fail closed, on purpose: `|| ( ... )` does fail the step, but the lint
    does not read subshells, so it reports the step instead of trusting it."""
    gates, why = _gates_per_lint(f"{_P} || (echo failed; exit 1)", "")

    assert gates is False
    assert "runs in its place" in why


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


def _real_problems(text: str) -> dict[tuple[str, str], list[Path]]:
    root = _repo_root()
    tests_root = root / lint.TESTS_ROOT_REL
    return lint.uncovered(
        lint.discover_test_files(tests_root), lint.find_invocations(text, root, tests_root), tests_root
    )


def test_the_real_seo_step_made_soft_failing_reports_seo():
    """The seo step still RUNS with `|| true`; it just can never go red."""
    text = _real_workflow().read_text(encoding="utf-8")
    run = "        run: $PYTEST tests/unit/seo/ -q --tb=short -p no:cacheprovider $COV\n"
    assert text.count(run) == 1, "the seo step changed shape; update this test's pattern"

    problems = _real_problems(text.replace(run, run.replace("$COV\n", "$COV || true\n")))

    assert [group for group, _reason in problems] == ["tests/unit/seo/"]
    assert "discards pytest's exit status" in next(iter(problems))[1]


def test_disabling_the_real_test_backend_job_reports_every_directory():
    """`if: false` on the job skips all of it, and a skipped job still satisfies
    the required `test-backend` check: every unit test would gate nothing."""
    text = _real_workflow().read_text(encoding="utf-8")
    disabled = re.sub(
        r"(?m)^(  test-backend:\n(?:    #.*\n)*)    if: .*\n", r"\1    if: false\n", text, count=1
    )
    assert disabled != text, "the test-backend job's `if:` moved; update this test's pattern"

    tests_root = _repo_root() / lint.TESTS_ROOT_REL
    every_group = {lint._group(p, tests_root) for p in lint.discover_test_files(tests_root)}

    assert {group for group, _reason in _real_problems(disabled)} == every_group
    assert _real_problems(text) == {}


def _yaml_steps(path: Path) -> list[tuple[str, dict, str]]:
    """``(job, step, effective shell)`` as PyYAML reads the workflow."""
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    workflow_shell = ((data.get("defaults") or {}).get("run") or {}).get("shell")
    out = []
    for job, body in (data.get("jobs") or {}).items():
        body = body or {}
        job_shell = ((body.get("defaults") or {}).get("run") or {}).get("shell") or workflow_shell
        for step in body.get("steps") or []:
            out.append((str(job), step, str(step.get("shell") or job_shell or "")))
    return out


@pytest.mark.parametrize(
    "workflow",
    sorted((_repo_root() / ".github" / "workflows").glob("*.yml")),
    ids=lambda p: p.name,
)
def test_the_scanner_reads_each_real_workflow_like_a_yaml_parser(workflow):
    """Same steps, same ``run`` text, working directory and shell as PyYAML."""
    expected = _yaml_steps(workflow)
    actual = lint.scan_steps(workflow.read_text(encoding="utf-8"))

    assert [s.job for s in actual] == [job for job, _step, _shell in expected]
    for got, (_job, want, shell) in zip(actual, expected, strict=True):
        want_run = str(want.get("run") or "")
        where = f"{workflow.name}:{got.line} ({got.name})"
        assert got.run.strip() == want_run.strip(), where
        assert got.working_directory == str(want.get("working-directory") or ""), where
        assert got.shell == shell.strip(), where
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
