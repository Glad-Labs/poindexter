"""A PR that changes only what the tests read must still run them.

test-backend's "Detect whether this change touches tested code" step skips
every pytest step when a PR changes nothing its pattern matches. Some tests
read files under infrastructure/, not backend code: Grafana dashboards and
alert rules, the Prometheus scrape config and static alert rules, the vendored
semgrep rulesets, and the systemd units. Every pytest process reads the units,
because tests/unit/_egress_guard.py derives the Ollama ports it refuses from
them. If the pattern misses one of those files, a PR that changes only that
file merges without the tests that check it. The first backend PR after it
then goes red for a file it never touched.

That was the state when tests/unit/infrastructure/ first got a CI step
(2026-09-25): the step existed, and the PRs it guards would have skipped it.

The inputs are not listed here. They are derived from the tests: every path
under infrastructure/ that the code in the trees test-backend runs builds, in
any of these forms, resolved against the repo:

* a ``root / "infrastructure" / ...`` chain;
* a path-parts call, e.g. ``_repo_file("infrastructure", "prometheus", ...)``;
* a path call, e.g. ``Path("infrastructure/...")`` or ``root.glob(...)``;
* a module-level path or glob string, e.g. the egress guard's
  ``OLLAMA_LAUNCH_GLOBS``.

So a new test that reads a new tree fails this until the pattern covers it,
wherever the test lives and however it spells the read. The check also runs
the other way: a tree the pattern triggers on must be read by some test.
Otherwise the clause only buys pytest runs that check nothing that changed.

This cannot see a read made by the code under test or by a subprocess. Name
such an input in the test that depends on it. test_semgrep_lint.py names the
lint's rules directory for this reason. Nor can it see a walk over the whole
repo, such as test_adapter_purity_lint's scan of every .py file.

The scope is infrastructure/ only. Tests also read other files outside
src/cofounder_agent/ and scripts/: the root compose files, docs/, packages/,
and other workflows. Covering those is a separate cost decision.
"""

from __future__ import annotations

import ast
import functools
import os
import re
from pathlib import Path

import pytest
import yaml

WORKFLOW = (".github", "workflows", "unit-tests.yml")
SCOPE = "infrastructure"
_TRIGGER = re.compile(r"backend_hits=\$\(grep -cE '([^']+)'")
# `$PYTEST tests/unit/services/ ...` names the tree `tests/unit`.
_PYTEST_TARGET = re.compile(r"\$PYTEST\s+(tests/[\w-]+)")
_GLOB_CHARS = frozenset("*?[")
# Callees whose string argument is a path. A str method such as
# `.startswith("infrastructure/...")` is not in this set.
_PATH_CALLEES = frozenset(
    {"Path", "PurePath", "PosixPath", "PurePosixPath", "joinpath", "glob", "rglob", "iglob", "open"}
)
_SKIP_DIRS = frozenset({"__pycache__", "node_modules"})
# A root that is a temp dir: `tmp_path`, `tmpdir`, `tmp`, `mkdtemp()`. Not
# `tmpl_dir`: templates are common here (alert-rules.yml.tmpl).
_TEMP_ROOT = re.compile(r"\b(tmp|tmp_\w+|tmpdir\w*)\b|mkd?temp|TemporaryDirectory|gettempdir")


@functools.cache
def _test_backend(repo_root: Path) -> dict:
    path = repo_root.joinpath(*WORKFLOW)
    return yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]["test-backend"]


def _trigger_pattern(repo_root: Path) -> re.Pattern[str]:
    """The ERE the detect-changes step matches each changed path against."""
    steps = _test_backend(repo_root)["steps"]
    script = next((s.get("run") or "" for s in steps if s.get("id") == "changes"), "")
    found = _TRIGGER.search(script)
    assert found, (
        f"no `backend_hits=$(grep -cE '...')` line in the `changes` step of {'/'.join(WORKFLOW)}. "
        "The trigger moved; point this test at it rather than deleting the check."
    )
    return re.compile(found.group(1))


def _test_dirs(repo_root: Path) -> list[Path]:
    """The test trees test-backend runs pytest over, read from its steps."""
    dirs: set[Path] = set()
    for step in _test_backend(repo_root)["steps"]:
        base = repo_root / step.get("working-directory", ".")
        dirs |= {base / target for target in _PYTEST_TARGET.findall(step.get("run") or "")}
    return sorted(d for d in dirs if d.is_dir())


def _const(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _in_scope(text: str) -> bool:
    return text.removeprefix("./").split("/", 1)[0] == SCOPE


def _join(parts: list[ast.AST], names: dict[str, str]) -> str | None:
    """The path the strings in *parts* spell, from the first one in scope.

    ``[root, "infrastructure", "systemd", unit]`` spells
    ``infrastructure/systemd/<unit>`` when *unit* is a module-level string in
    *names*, and ``infrastructure/systemd`` when it is not: a part that is not
    a known non-empty string ends the path, which then names the directory the
    read is in. Returns None for a path rooted in a temp dir, since that is a
    synthetic tree and not the repo, and for anything that does not name a
    tree under the scope. A bare ``infrastructure`` is as often an alert
    category as a path.
    """
    texts = [_const(part) or names.get(getattr(part, "id", ""), "") for part in parts]
    start = next((i for i, text in enumerate(texts) if _in_scope(text)), None)
    if start is None or any(_TEMP_ROOT.search(ast.unparse(part)) for part in parts[:start]):
        return None
    spelled: list[str] = []
    for text in texts[start:]:
        if not text.strip("/"):
            break
        spelled.append(text.strip("/").removeprefix("./"))
    path = "/".join(spelled)
    return path if "/" in path else None


def _operands(node: ast.AST) -> list[ast.AST]:
    """``a / b / c`` as ``[a, b, c]``."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return [*_operands(node.left), node.right]
    return [node]


def _named_paths(source: str) -> set[str]:
    """Every repo-relative path or glob under the scope that *source* builds."""
    tree = ast.parse(source)
    assignments = [s for s in tree.body if isinstance(s, (ast.Assign, ast.AnnAssign)) and s.value]
    # `_UNIT = "poindexter-mcp-http.service"`, so `root / ... / _UNIT` resolves.
    names = {
        target.id: text
        for s in assignments
        if (text := _const(s.value))
        for target in (s.targets if isinstance(s, ast.Assign) else [s.target])
        if isinstance(target, ast.Name)
    }
    divisions = [
        n for n in ast.walk(tree) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
    ]
    inner = {id(n.left) for n in divisions}
    named = {_join(_operands(n), names) for n in divisions if id(n) not in inner}
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        callee = getattr(call.func, "attr", None) or getattr(call.func, "id", "")
        # `root.joinpath(...)`: the receiver is the root the path hangs off.
        receiver = [call.func.value] if isinstance(call.func, ast.Attribute) else []
        args = call.args
        for i, arg in enumerate(args):
            if not _in_scope(_const(arg) or ""):
                continue
            # A path call, or path parts: the next argument continues the path.
            if callee in _PATH_CALLEES or (i + 1 < len(args) and _const(args[i + 1])):
                named.add(_join([*receiver, *args], names))
            break
    for statement in assignments:
        named |= {
            text.removeprefix("./")
            for node in ast.walk(statement.value)
            if (text := _const(node)) and _in_scope(text) and "/" in text
        }
    named.discard(None)
    return named


def _resolve(repo_root: Path, spec: str) -> set[Path]:
    """The repo files a named path or glob covers. A directory stands for its files."""
    hits = repo_root.glob(spec) if _GLOB_CHARS & set(spec) else [repo_root / spec]
    files: set[Path] = set()
    for hit in hits:
        if hit.is_file():
            files.add(hit)
        for parent, dirs, names in os.walk(hit) if hit.is_dir() else ():
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            files |= {Path(parent, name) for name in names}
    return files


@functools.cache
def _derived_inputs(repo_root: Path) -> dict[str, frozenset[str]]:
    """Each file under the scope that a test names, mapped to the tests that name it."""
    dirs = _test_dirs(repo_root)
    assert any(d.parts[-2:] == ("tests", "unit") for d in dirs), (
        f"found no `$PYTEST tests/unit/...` step in test-backend (found {dirs}). "
        "A check that scanned nothing has not passed."
    )
    readers: dict[str, set[str]] = {}
    for module in sorted(p for d in dirs for p in d.rglob("*.py")):
        source = module.read_text(encoding="utf-8")
        if SCOPE not in source:
            continue
        reader = module.relative_to(repo_root).as_posix()
        for spec in _named_paths(source):
            for path in _resolve(repo_root, spec):
                readers.setdefault(path.relative_to(repo_root).as_posix(), set()).add(reader)
    return {path: frozenset(names) for path, names in readers.items()}


def test_every_file_the_tests_read_triggers_the_suite(repo_root: Path) -> None:
    pattern = _trigger_pattern(repo_root)
    inputs = _derived_inputs(repo_root)
    assert inputs, (
        f"found no {SCOPE}/ path named by any test test-backend runs. "
        "A check that scanned nothing has not passed."
    )

    missed = {path: readers for path, readers in sorted(inputs.items()) if not pattern.search(path)}
    if missed:
        pytest.fail(
            "A PR that changes only these files skips every pytest step in "
            "test-backend, including the tests that read them:\n  "
            + "\n  ".join(
                f"{path}  <- {', '.join(sorted(readers))}" for path, readers in missed.items()
            )
            + f"\n\nAdd what those tests read to the detect-changes pattern in {'/'.join(WORKFLOW)} "
            f"(currently {pattern.pattern!r}). Where the tree also holds files no test "
            "reads, match only the read ones; the comment above the pattern says why "
            "infrastructure/cloudflare/ is not matched.",
            pytrace=False,
        )


def test_every_tree_the_suite_triggers_on_is_read_by_a_test(repo_root: Path) -> None:
    pattern = _trigger_pattern(repo_root)
    read = {path.split("/")[1] for path in _derived_inputs(repo_root)}
    trees = sorted(p.name for p in (repo_root / SCOPE).iterdir() if p.is_dir())
    assert trees, (
        f"no directories under {repo_root / SCOPE}. A check that scanned nothing has not passed."
    )

    unread = [
        tree
        for tree in trees
        if tree not in read
        and any(
            pattern.search(p.relative_to(repo_root).as_posix())
            for p in _resolve(repo_root, f"{SCOPE}/{tree}")
        )
    ]
    if unread:
        pytest.fail(
            f"The detect-changes pattern in {'/'.join(WORKFLOW)} runs the whole suite "
            f"when these trees change, but no test names a file in them: {unread}. "
            "Each such run is 9-14 minutes that checks nothing the PR changed. Drop the "
            "clause, or, if a test reads the tree in a way this file cannot see (the "
            "code under test, a subprocess), name the path in that test.",
            pytrace=False,
        )


_SPELLINGS = '''
"""A docstring naming infrastructure/cloudflare/x.ts is prose, not a read."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
DASHBOARDS = ROOT / "infrastructure" / "grafana" / "dashboards"
UNITS = ("infrastructure/systemd/*.service", "scripts/linux/*.sh")
LABELS = {"category": "infrastructure"}
_UNIT = "two.service"


def test_reads(name):
    (ROOT / "infrastructure/systemd/one.timer").read_text()
    (ROOT / "infrastructure" / "systemd" / _UNIT).read_text()
    _repo_file("infrastructure", "prometheus", "alertmanager.yml.tmpl")
    os.path.join(ROOT, "infrastructure", "pgadmin", "servers.json")
    Path("./infrastructure/semgrep/p-python.yaml").read_text()
    ROOT.glob("infrastructure/loki/*.yml")
    (ROOT / "infrastructure" / "tempo" / name / "x.yml").read_text()
    (tmpl_dir / "infrastructure" / "grafana" / "rules.yml.tmpl").read_text()


def test_does_not_read(tmp_path, entry):
    (tmp_path / "infrastructure" / "promtail" / "a.yml").write_text("")
    tmp_path.joinpath("infrastructure", "promtail", "b.yml").write_text("")
    entry.startswith("infrastructure/promtail/")
    decide("warning", "infrastructure", "")
    decide("infrastructure", severity="critical")
    classify("infrastructure/cloudflare/page-views-beacon/src/index.ts")
    Path(__file__).parent / "infrastructure"
    Path(tempfile.mkdtemp()).joinpath("infrastructure", "promtail", "c.yml")
'''


def test_the_derivation_sees_every_spelling_of_a_read_and_nothing_else() -> None:
    assert _named_paths(_SPELLINGS) == {
        "infrastructure/grafana/dashboards",
        "infrastructure/grafana/rules.yml.tmpl",
        "infrastructure/systemd/*.service",
        "infrastructure/systemd/one.timer",
        "infrastructure/systemd/two.service",
        "infrastructure/prometheus/alertmanager.yml.tmpl",
        "infrastructure/pgadmin/servers.json",
        "infrastructure/semgrep/p-python.yaml",
        "infrastructure/loki/*.yml",
        # A part that is not a constant ends the path at the directory read.
        "infrastructure/tempo",
    }
