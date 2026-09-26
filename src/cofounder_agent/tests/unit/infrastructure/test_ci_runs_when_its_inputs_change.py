"""A PR that changes only what the tests read must still run them.

test-backend's "Detect whether this change touches tested code" step skips
every pytest step when a PR changes nothing its pattern matches. Some tests
read files outside src/cofounder_agent/ and scripts/ entirely: Grafana
dashboards and alert rules, the Prometheus scrape config and static alert
rules, the vendored semgrep rulesets, the systemd units every pytest process
reads through tests/unit/_egress_guard.py, the root compose files, the
mcp-server/mcp-server-gladlabs/web/public-site trees a settings-key audit
walks, README.md's quick-start commands, and a handful of single files each
read by one test. If the pattern misses one of those files, a PR that changes
only that file merges without the tests that check it. The first backend PR
after it then goes red for a file it never touched.

That was the state when tests/unit/infrastructure/ first got a CI step
(2026-09-25): the step existed, and the PRs it guards would have skipped it.
Widened past infrastructure/ the same day, once a full-suite run under a
throwaway ``sys.addaudithook`` pytest plugin — logging every ``open`` outside
src/cofounder_agent/ and scripts/, not grepping for a tree name, which mixes
comments/labels/classifier-inputs in with real reads and misses an indirect
read through an imported script's own path constant — showed what else the
tests were reading.

The inputs are not listed here. They are derived from the tests: every path
under a covered root (``SCOPE_ROOTS`` below — a directory, or a single file
that has no children to iterate) that the code in the trees test-backend runs
builds, in any of these forms, resolved against the repo:

* a ``root / "infrastructure" / ...`` chain, however many literal segments
  deep, including a chain that stops at the bare root itself when a
  multi-segment root's remaining literal is the whole match (``root /
  ".github" / "workflows"`` alone, iterated elsewhere via ``.glob(...)``);
* a path-parts call, e.g. ``_repo_file("infrastructure", "prometheus", ...)``;
* a path call, e.g. ``Path("infrastructure/...")`` or ``root.glob(...)``;
* a module-level path or glob string, e.g. the egress guard's
  ``OLLAMA_LAUNCH_GLOBS``;
* a ``@pytest.mark.parametrize(...)`` argument, e.g. ``["README.md",
  "src/cofounder_agent/poindexter/README.md"]`` — the only one of these forms
  that is not a filesystem-path *expression*, because a parametrized test's
  actual read (``REPO_ROOT / readme_rel``) resolves the file only at runtime.

So a new test that reads a new path fails this until the pattern covers it,
wherever the test lives and however it spells the read. The check also runs
the other way: a root the pattern triggers on must be read by some test.
Otherwise the clause only buys pytest runs that check nothing that changed.

This cannot see a read made by the code under test or by a subprocess — an
imported script's own path constant, accessed only as ``LINT.SOME_PATH``
rather than spelled again in the test, is invisible. Name such an input in
the test that depends on it instead: a local, redundant constant plus a test
asserting it still matches the lint's own constant. test_semgrep_lint.py
(``VENDORED_RULES``), test_ports_lint.py (``COMPOSE_LOCAL`` / ``PORTS_DOC``),
and test_settings_audit_blind_spots.py (``WEB_PUBLIC_SITE``) all do this for
exactly this reason. Nor can it see a walk over the whole repo for a property
that has nothing to do with any one file's content, such as
test_adapter_purity_lint's ``rglob("*.py")`` for a dead comment spelling, or
test_check_shell_line_endings' ``rglob`` for CRLF — those are excluded in the
detect-changes comment by name, not by this file.

SCOPE_ROOTS is hand-maintained, the same way the single-string ``SCOPE`` it
replaces was. What IS derived is which files under each root a test actually
reads, and whether the detect-changes pattern's own clauses agree.
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
# Every top-level path this file's ERE-widening covers. A directory (any file
# under it may be read) or a single file — root-level or nested — that has no
# children to iterate: the leaf IS the whole match. `_FILES` entries may
# match bare (nothing needs to follow); `_DIRS` entries need at least one
# literal segment past the root itself, or a bare `root / "infrastructure"`
# read (as likely an alert category as a path — see `_covers`) would count.
_DIRS = (
    "infrastructure",
    "mcp-server",
    "mcp-server-gladlabs",
    "web/public-site",
    ".github/workflows",
)
_FILES = (
    "docker-compose.yml",
    "docker-compose.local.yml",
    "docker-compose.consumer.yml",
    "README.md",
    "pyproject.toml",
    ".gitleaks.toml",
    ".release-please-manifest.json",
    "release-please-config.poindexter.json",
    "packages/brand/src/tokens/colors.css",
    "docs/architecture/anti-hallucination.md",
    "docs/operations/ports.md",
)
SCOPE_ROOTS = _DIRS + _FILES
# The literal first segment of every root — what must appear verbatim in a
# module's source for any of the three derivation mechanisms below to find a
# match. Used only as a cheap pre-filter before AST-parsing a module.
_MARKERS = tuple({root.split("/", 1)[0] for root in SCOPE_ROOTS})
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


def _covers(path: str, root: str) -> bool:
    """*path* is a real occurrence of *root*: equal to it, or a sub-path."""
    return path == root or path.startswith(root + "/")


def _root_for(text: str) -> str | None:
    """A covered root that *text* opens, or already spells on its own.

    None if *text* does neither. Two shapes: *text* is the first segment of a
    multi-segment root (``web`` opens ``web/public-site`` — whether the rest
    of the root is actually spelled next is `_join`'s job, checked once the
    full path is built, so a false start like ``web/starter`` is rejected
    rather than mis-attributed); or *text* is already a complete path under a
    root in one string, as a single call argument spells it (``Path("./
    infrastructure/semgrep/p-python.yaml")`` — this is what `_covers` (rather
    than a second split) is for).
    """
    text = text.removeprefix("./")
    if not text:
        return None
    return next(
        (root for root in SCOPE_ROOTS if text == root.split("/", 1)[0] or _covers(text, root)),
        None,
    )


def _in_scope(text: str) -> bool:
    return _root_for(text) is not None


def _join(parts: list[ast.AST], names: dict[str, str]) -> str | None:
    """The path the strings in *parts* spell, from the first one that opens a
    covered root and whose build actually lands under that root.

    ``[root, "infrastructure", "systemd", unit]`` spells
    ``infrastructure/systemd/<unit>`` when *unit* is a name in *names* (a
    plain string constant, or a Path-valued variable whose OWN chain already
    resolved under a covered root — see ``_alias_names``), and
    ``infrastructure/systemd`` when it is not: a part that is not a known
    non-empty string ends the path, which then names the directory the read
    is in. Returns None for a path rooted in a temp dir, since that is a
    synthetic tree and not the repo, and for anything that does not land
    under a covered root once built (``web`` leading to ``web/starter``, a
    DIFFERENT, uncovered tree, tries the next candidate start rather than
    mis-attributing the read).

    Does NOT reject a bare single-segment directory root (``root /
    "infrastructure"`` alone) — that is `_reportable`'s job, checked at each
    call site, because this function is also how a Path-valued alias gets
    its own value resolved (`_MCP_DIR = ROOT / "mcp-server"` must resolve to
    "mcp-server" internally even though "mcp-server" bare is not, on its
    own, worth reporting as a read).
    """
    texts = [_const(part) or names.get(getattr(part, "id", ""), "") for part in parts]
    for start, text in enumerate(texts):
        root = _root_for(text)
        if root is None:
            continue
        if any(_TEMP_ROOT.search(ast.unparse(part)) for part in parts[:start]):
            return None
        spelled: list[str] = []
        for t in texts[start:]:
            if not t.strip("/"):
                break
            spelled.append(t.strip("/").removeprefix("./"))
        path = "/".join(spelled)
        if _covers(path, root):
            return path
    return None


def _reportable(path: str | None) -> bool:
    """*path* is specific enough to report as a real read, not a bare word
    that is as likely an unrelated label (``LABELS = {"category":
    "infrastructure"}``) as a path. A `_FILES` leaf has no "one level
    deeper" to reach, so it is reportable on its own; a directory root needs
    at least one more segment."""
    return path is not None and (path in _FILES or "/" in path)


def _operands(node: ast.AST) -> list[ast.AST]:
    """``a / b / c`` as ``[a, b, c]``."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return [*_operands(node.left), node.right]
    return [node]


def _assign_targets(statement: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    return [t.id for t in targets if isinstance(t, ast.Name)]


def _alias_names(assignments: list[ast.Assign | ast.AnnAssign]) -> dict[str, str]:
    """Every assignment target resolved to a path or plain string, by fixpoint.

    A plain string constant (``_UNIT = "poindexter-mcp-http.service"``)
    resolves immediately. A ``root / "mcp-server"`` assignment resolves once
    "mcp-server" is known — which needs no other alias — but a LATER ``x = y
    / "z"`` where ``y`` is itself one of these aliases needs *this*
    function's own output fed back in, hence the fixpoint: at most one new
    resolution per assignment, so it can never loop more than that many times.
    """
    names: dict[str, str] = {}
    for _ in range(len(assignments) + 1):
        changed = False
        for statement in assignments:
            if (text := _const(statement.value)) is not None:
                resolved = text
            elif isinstance(statement.value, ast.BinOp) and isinstance(statement.value.op, ast.Div):
                resolved = _join(_operands(statement.value), names)
            else:
                continue
            if resolved is None:
                continue
            for target in _assign_targets(statement):
                if names.get(target) != resolved:
                    names[target] = resolved
                    changed = True
        if not changed:
            break
    return names


def _named_paths(source: str) -> set[str]:
    """Every repo-relative path or glob under a covered root that *source* builds."""
    tree = ast.parse(source)
    assignments = [s for s in tree.body if isinstance(s, (ast.Assign, ast.AnnAssign)) and s.value]
    names = _alias_names(assignments)
    divisions = [
        n for n in ast.walk(tree) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
    ]
    inner = {id(n.left) for n in divisions}
    named = {
        path
        for n in divisions
        if id(n) not in inner and _reportable(path := _join(_operands(n), names))
    }
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
                if _reportable(path := _join([*receiver, *args], names)):
                    named.add(path)
            break
    # Bare string constants naming a covered root: a module-level assignment's
    # value (`UNITS = ("infrastructure/systemd/*.service", ...)`), or a
    # `@pytest.mark.parametrize(...)` argument — the one shape here that is
    # not a filesystem-path expression, because the read it feeds
    # (`REPO_ROOT / readme_rel`) resolves the parameter only at runtime.
    parametrize_args: list[ast.AST] = [
        arg
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        for deco in fn.decorator_list
        if isinstance(deco, ast.Call) and getattr(deco.func, "attr", "") == "parametrize"
        for arg in deco.args
    ]
    for value in (*(s.value for s in assignments), *parametrize_args):
        for node in ast.walk(value):
            if not (text := _const(node)):
                continue
            text = text.removeprefix("./")
            root = _root_for(text)
            # A bare string here is a much weaker signal than `_join`'s (any
            # constant anywhere could coincidentally read "infrastructure" or
            # "docs" for an unrelated reason — see `LABELS` in `_SPELLINGS`),
            # so `_covers` must ALSO hold: `docs` alone shares a first
            # segment with the `docs/operations/ports.md` LEAF root but does
            # not cover it. `_reportable` then applies the same "at least one
            # level deeper for a directory root" filter used everywhere else.
            if root and _covers(text, root) and _reportable(text):
                named.add(text)
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
    """Each file under a covered root that a test names, mapped to the tests that name it."""
    dirs = _test_dirs(repo_root)
    assert any(d.parts[-2:] == ("tests", "unit") for d in dirs), (
        f"found no `$PYTEST tests/unit/...` step in test-backend (found {dirs}). "
        "A check that scanned nothing has not passed."
    )
    readers: dict[str, set[str]] = {}
    for module in sorted(p for d in dirs for p in d.rglob("*.py")):
        source = module.read_text(encoding="utf-8")
        if not any(marker in source for marker in _MARKERS):
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
        "found no path under a covered root (SCOPE_ROOTS) named by any test test-backend runs. "
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
            "infrastructure/cloudflare/ and a handful of others are not matched.",
            pytrace=False,
        )


def test_every_root_the_suite_triggers_on_is_read_by_a_test(repo_root: Path) -> None:
    """The reverse direction, generalized over every SCOPE_ROOTS entry.

    A file root (``_FILES``) has no children — it either was named by a
    test, or it was not. A directory root (``_DIRS``) depends on how its own
    ERE clause is shaped, derived from the clause itself rather than
    hand-listed per root: `infrastructure/(grafana|prometheus|semgrep|
    systemd)/` names specific children, so each one the pattern would
    trigger on needs its OWN file in the derived inputs — same granularity
    as the single-``SCOPE`` original, which is how `infrastructure/
    cloudflare/` (not in the alternation) stays correctly unflagged. A clause
    like `^mcp-server(-gladlabs)?/` or `^web/public-site/` instead matches
    ANY child by construction (proven by probing a name no real file has) —
    for one of those, holding every child (`pyproject.toml`, a `tests/`
    directory nothing reads on its own, `__pycache__`) to its own reader
    would be requiring more than the workflow comment's cost/benefit call
    actually made, so one real read anywhere under the root is enough.
    """
    pattern = _trigger_pattern(repo_root)
    read = _derived_inputs(repo_root)
    unread: list[str] = []

    for root in _FILES:
        if root not in read and pattern.search(root):
            unread.append(root)

    for root in _DIRS:
        root_dir = repo_root / root
        if not root_dir.is_dir():
            continue
        if pattern.search(f"{root}/__unread_probe__"):
            # The clause matches ANY path under root; only a whole-root
            # question is meaningful.
            if not any(path.startswith(root + "/") for path in read):
                unread.append(root)
            continue
        read_children = {path.removeprefix(root + "/").split("/", 1)[0] for path in read if path.startswith(root + "/")}
        for child in sorted(p.name for p in root_dir.iterdir()):
            if child in read_children:
                continue
            child_path = f"{root}/{child}"
            if any(
                pattern.search(p.relative_to(repo_root).as_posix())
                for p in _resolve(repo_root, child_path)
            ):
                unread.append(child_path)

    if unread:
        pytest.fail(
            f"The detect-changes pattern in {'/'.join(WORKFLOW)} runs the whole suite "
            f"when these paths change, but no test names a file under them: {unread}. "
            "Each such run is 9-14 minutes that checks nothing the PR changed. Drop the "
            "clause, or, if a test reads the path in a way this file cannot see (the "
            "code under test, a subprocess), name it in that test — a local, redundant "
            "constant plus a test asserting it matches the lint's own constant, the way "
            "test_semgrep_lint.py / test_ports_lint.py / test_settings_audit_blind_spots.py do.",
            pytrace=False,
        )


_SPELLINGS = '''
"""A docstring naming infrastructure/cloudflare/x.ts is prose, not a read."""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
DASHBOARDS = ROOT / "infrastructure" / "grafana" / "dashboards"
UNITS = ("infrastructure/systemd/*.service", "scripts/linux/*.sh")
LABELS = {"category": "infrastructure", "tree": "docs"}
_UNIT = "two.service"
WEB_PUBLIC_SITE = ROOT / "web" / "public-site"
MCP_DIR = ROOT / "mcp-server"


def test_reads(name):
    (ROOT / "infrastructure/systemd/one.timer").read_text()
    (ROOT / "infrastructure" / "systemd" / _UNIT).read_text()
    _repo_file("infrastructure", "prometheus", "alertmanager.yml.tmpl")
    os.path.join(ROOT, "infrastructure", "pgadmin", "servers.json")
    Path("./infrastructure/semgrep/p-python.yaml").read_text()
    ROOT.glob("infrastructure/loki/*.yml")
    (ROOT / "infrastructure" / "tempo" / name / "x.yml").read_text()
    (tmpl_dir / "infrastructure" / "grafana" / "rules.yml.tmpl").read_text()
    wf_dir = ROOT / ".github" / "workflows"
    for path in wf_dir.glob("*.yml"):
        path.read_text()
    (ROOT / "README.md").read_text()
    (ROOT / "web" / "starter" / "package.json").read_text()
    (MCP_DIR / "oauth_client.py").read_text()


@pytest.mark.parametrize("readme_rel", ["README.md", "src/cofounder_agent/poindexter/README.md"])
def test_reads_parametrized(readme_rel):
    (ROOT / readme_rel).read_text()


def test_does_not_read(tmp_path, entry):
    (tmp_path / "infrastructure" / "promtail" / "a.yml").write_text("")
    tmp_path.joinpath("infrastructure", "promtail", "b.yml").write_text("")
    (tmp_path / "web" / "public-site" / "lib" / "posts.ts").write_text("")
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
        # A multi-segment root with nothing beyond it in THIS chain (the
        # filename comes from a later `.glob(...)` this file cannot see) —
        # kept, unlike a bare word in the standalone-string scan below.
        ".github/workflows",
        "README.md",
        "web/public-site",
        # MCP_DIR = ROOT / "mcp-server" alone is a bare single-segment root
        # (not reportable on its own — see `test_a_bare_directory_root_alone_
        # is_not_reportable`), but `_alias_names` still resolves it, so the
        # LATER `MCP_DIR / "oauth_client.py"` — the real test_mcp_oauth.py
        # shape — reports the file, not the bare root.
        "mcp-server/oauth_client.py",
        # `web/starter` shares web/public-site's first segment but is not a
        # sub-path of it — rejected, not mis-attributed.
    }


def test_a_false_start_on_a_shared_first_segment_is_rejected() -> None:
    """``web/starter`` opens with the same first segment as the covered
    ``web/public-site`` root but is not a sub-path of it. `_join` must reject
    this start rather than mis-attribute the read."""
    assert "web/starter" not in _named_paths(_SPELLINGS)
    assert "web/starter/package.json" not in _named_paths(_SPELLINGS)


def test_a_bare_directory_root_alone_is_not_reportable() -> None:
    """``MCP_DIR = ROOT / "mcp-server"`` resolves internally (`_alias_names`
    needs it to, so ``MCP_DIR / "oauth_client.py"`` later can), but the bare
    root by itself is not a reported read — `_reportable` holds a directory
    root to the same "one level deeper" bar as the standalone-string scan,
    checked once at the divisions/call-loop call sites instead of inside
    `_join`, which also serves the alias-resolution use that must NOT apply
    it."""
    assert "mcp-server" not in _named_paths(_SPELLINGS)


def test_a_bare_word_in_the_standalone_string_scan_is_rejected() -> None:
    """`decide("infrastructure", ...)` and the two `LABELS` dict values are a
    category name and a tree name, not a path — `_join`'s CALL branch never
    reaches them (no `/`-chain, and `decide` is not a path callee). The
    bare-string branch does reach both `LABELS` values: "infrastructure"
    fails the "go one level deeper" check for a directory root, and "docs"
    fails `_covers` outright — it shares a first segment with the `docs/
    architecture/anti-hallucination.md` and `docs/operations/ports.md` LEAF
    roots but is not, itself, either of them."""
    assert "infrastructure" not in _named_paths(_SPELLINGS)
    assert "docs" not in _named_paths(_SPELLINGS)


def test_parametrize_names_a_leaf_file_root_without_a_path_expression() -> None:
    """``test_reads_parametrized`` never builds ``REPO_ROOT / "README.md"`` as
    a literal chain — the argument is a runtime parameter. Only the
    decorator's own argument list spells the path."""
    assert "README.md" in _named_paths(_SPELLINGS)
    # The sibling item is under src/cofounder_agent/, out of this file's scope.
    assert "src/cofounder_agent/poindexter/README.md" not in _named_paths(_SPELLINGS)
