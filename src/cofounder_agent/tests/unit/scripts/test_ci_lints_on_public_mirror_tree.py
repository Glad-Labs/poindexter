"""Every lint step the public mirror's CI runs must pass on the tree the mirror gets.

The public mirror (Glad-Labs/poindexter) is this repository after
``scripts/sync-to-github.sh`` has deleted the operator-private files, and its
CI runs the same workflows. The pytest steps degrade to ``--collect-only``
there, but every ``python scripts/...`` lint step runs for real, on the
stripped tree. Nothing gates on the result: the sync force-pushes, and a red
run on a branch nobody watches stays red. From 2026-09-20T21:04Z to 2026-09-28
the mirror's ``unit-tests`` job failed on every sync. ``comment_reference_lint``
read each comment citing a stripped file as a dead reference, and
``settings_phantom_read_lint`` read an ALLOWLIST entry whose only reader is
stripped as stale. A failing step ends the job, so the lints after those two
never ran on the mirror at all.

How the tree is built
---------------------
Not approximated. The tracked files are copied into a scratch repository, and
the REAL sync script runs there with a local bare repository as its ``github``
remote. The strips, the cosmetic ``org/name`` rewrite, the release-please
config swap and the sync's own leak guard all run as in a real sync, and the
result is cloned. When the sync changes, this changes with it: there is no copy
of its strip list here to drift. If the sync itself fails, so does this test,
because a real sync would stop at the same point and freeze the mirror.

Which lints run
---------------
Derived from the workflows as they land on the mirror, i.e. read AFTER the
rewrite, so a job guard the rewrite inverts is modelled as inverted. A job or
step whose ``if:`` is definitely false there is skipped, as Actions skips it:
``github.repository != 'Glad-Labs/poindexter'`` and the private-push guard on
``lint-main``. Anything the evaluator cannot decide counts as running, so an
unknown guard costs a lint run and never hides one. Each ``python
scripts/...py`` invocation then runs in the mirror tree, with the environment
Actions gives the mirror (``GITHUB_REPOSITORY=Glad-Labs/poindexter``). A script
that cannot run from a plain checkout, because it needs a live service or a
binary its workflow downloads, declares ``# mirror-tree-exempt: <reason>`` in
its own source.

Also checked: no ratchet baseline under ``scripts/ci`` names a file the sync
strips. The baseline ships and its key IS the name. Two
``comment_reference_baseline.json`` keys did exactly that until 2026-09-28.

The sync strips this file too, and must. It drives a script the mirror does not
carry and names files the mirror must not name, so it can only run here.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
SYNC_SCRIPT = "scripts/sync-to-github.sh"
MIRROR_REPOSITORY = "Glad-Labs/poindexter"
EXEMPT_MARKER = "# mirror-tree-exempt:"

# Never handed to a lint: nothing in this test may reach a real database. That
# includes a workflow's own literal DSN: migrations-smoke.yml points
# DATABASE_URL at localhost:5432, the job's service container in CI, which on a
# developer machine can be a real Postgres.
_DB_VARS = frozenset({
    "DATABASE_URL", "LOCAL_DATABASE_URL", "POINDEXTER_MEMORY_DSN", "POINDEXTER_GPU_LOCK_DSN",
})


def _is_db_setting(key: str, value: str) -> bool:
    return (key in _DB_VARS or key.startswith("PG")          # libpq / asyncpg env
            or value.startswith(("postgres://", "postgresql://")))


# ---------------------------------------------------------------------------
# The mirror's view of an Actions ``if:`` expression
# ---------------------------------------------------------------------------

def _unwrap(expr: str) -> str:
    s = " ".join(expr.split())
    if s.startswith("${{") and s.endswith("}}"):
        s = s[3:-2].strip()
    while s.startswith("(") and s.endswith(")") and _closes_at_end(s):
        s = s[1:-1].strip()
    return s


def _closes_at_end(s: str) -> bool:
    """True when the ``(`` at s[0] is closed by the ``)`` at s[-1]."""
    depth, quoted = 0, False
    for i, c in enumerate(s):
        if c == "'":
            quoted = not quoted
        elif not quoted and c == "(":
            depth += 1
        elif not quoted and c == ")":
            depth -= 1
            if depth == 0 and i < len(s) - 1:
                return False
    return depth == 0


def _split_top(s: str, op: str) -> list[str]:
    """Split on ``op`` where it sits outside every paren and quote."""
    parts, depth, quoted, start, i = [], 0, False, 0, 0
    while i < len(s):
        c = s[i]
        if c == "'":
            quoted = not quoted
        elif not quoted and c == "(":
            depth += 1
        elif not quoted and c == ")":
            depth -= 1
        elif not quoted and depth == 0 and s.startswith(op, i):
            parts.append(s[start:i])
            i += len(op)
            start = i
            continue
        i += 1
    parts.append(s[start:])
    return parts


_REPO_CMP = re.compile(
    r"^github\.repository\s*(==|!=)\s*'([^']*)'$|^'([^']*)'\s*(==|!=)\s*github\.repository$"
)
_PRIVATE_CMP = re.compile(r"^github\.event\.repository\.private\s*(==|!=)\s*(true|false)$")


def _atom_truth(s: str) -> bool | None:
    m = _REPO_CMP.match(s)
    if m:
        op, name = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        same = name.casefold() == MIRROR_REPOSITORY.casefold()   # Actions ignores case here too
        return same if op == "==" else not same
    if s == "github.event.repository.private":
        return False                                   # the mirror is public
    m = _PRIVATE_CMP.match(s)
    if m:
        same = m.group(2) == "false"
        return same if m.group(1) == "==" else not same
    if s == "always()":
        return True
    return None


def mirror_truth(expr: object) -> bool | None:
    """How an ``if:`` evaluates on the mirror: True, False, or None (undecidable).

    Three-valued on purpose. Only the repository identity is known here, so
    ``needs.*`` / ``steps.*`` / event names stay unknown, and an unknown operand
    can only make the answer unknown. Callers skip a job only on a definite
    False, the one case where Actions certainly skips it too.
    """
    if expr is None:
        return True
    if isinstance(expr, bool):
        return expr
    s = _unwrap(str(expr))
    for op, short_circuit in (("||", True), ("&&", False)):
        parts = _split_top(s, op)
        if len(parts) > 1:
            vals = [mirror_truth(p) for p in parts]
            if short_circuit in vals:
                return short_circuit
            if all(v is (not short_circuit) for v in vals):
                return not short_circuit
            return None
    if s.startswith("!") and not s.startswith("!="):
        inner = mirror_truth(s[1:])
        return None if inner is None else not inner
    return _atom_truth(s)


# ---------------------------------------------------------------------------
# Building the mirror
# ---------------------------------------------------------------------------

def _git_env(home: Path) -> dict[str, str]:
    """An environment in which git can only see the scratch repositories.

    A git hook or CI step can export GIT_DIR / GIT_INDEX_FILE, which would point
    every call below at the real repository, and the user's global config can
    carry hooks, signing or autocrlf. HOME is replaced as well as
    GIT_CONFIG_GLOBAL because older git ignores the latter. ``python3`` must be
    this interpreter: the sync shells out to it.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        HOME=str(home),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="mirror-sim",
        GIT_AUTHOR_EMAIL="mirror-sim@example.invalid",
        GIT_COMMITTER_NAME="mirror-sim",
        GIT_COMMITTER_EMAIL="mirror-sim@example.invalid",
        PATH=os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")]),
    )
    return env


def _run(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int = 300) -> str:
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        pytest.fail(f"`{' '.join(cmd)}` exited {proc.returncode} in {cwd}:\n"
                    f"{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return proc.stdout


@dataclass(frozen=True)
class MirrorTree:
    root: Path
    stripped: frozenset[str]   # repo-relative paths the sync removed


@pytest.fixture(scope="module")
def mirror(tmp_path_factory: pytest.TempPathFactory) -> MirrorTree:
    work = tmp_path_factory.mktemp("public-mirror")
    home = work / "home"
    home.mkdir()
    env = _git_env(home)
    scrubbed = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    # The working tree of every tracked file, so a local edit is tested before
    # it is committed. `ls-files` (not a directory walk) is what the sync
    # itself works from, and it keeps untracked scratch files out.
    listed = _run(["git", "ls-files", "-z"], REPO, scrubbed).split("\0")
    source = work / "source"
    copied: list[str] = []
    for rel in filter(None, listed):
        src, dst = REPO / rel, source / rel
        if not src.exists() and not src.is_symlink():
            continue                               # deleted, not yet staged
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            os.symlink(os.readlink(src), dst)
        else:
            shutil.copy2(src, dst)
        copied.append(rel)
    assert copied, "git ls-files listed nothing: is this a git checkout?"

    pathspec = work / "pathspec.nul"
    pathspec.write_text("\0".join(copied), encoding="utf-8")
    _run(["git", "init", "-q"], source, env)
    _run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], source, env)
    # -f: ten tracked files match a .gitignore rule; a plain add drops them.
    # Literal pathspecs: route files like `[slug]/page.js` are globs otherwise.
    # (Only here. The sync's own `git rm` of `COMMIT_MESSAGE_*.txt` needs the glob.)
    _run(["git", "add", "-f", f"--pathspec-from-file={pathspec}", "--pathspec-file-nul"],
         source, dict(env, GIT_LITERAL_PATHSPECS="1"))
    _run(["git", "commit", "-q", "--no-verify", "-m", "source snapshot"], source, env)

    remote = work / "mirror.git"
    _run(["git", "init", "-q", "--bare", str(remote)], work, env)
    _run(["git", "remote", "add", "github", str(remote)], source, env)
    sync = subprocess.run(["bash", SYNC_SCRIPT], cwd=source, env=env,
                          capture_output=True, text=True, timeout=600)
    if sync.returncode != 0:
        pytest.fail(
            f"{SYNC_SCRIPT} failed against the simulated remote (exit {sync.returncode}). "
            "A real sync stops at the same point and the public mirror freezes.\n"
            f"{sync.stdout[-4000:]}\n{sync.stderr[-4000:]}"
        )

    root = work / "mirror"
    _run(["git", "clone", "-q", "-b", "main", str(remote), str(root)], work, env)
    shipped = set(filter(None, _run(["git", "ls-files", "-z"], root, env).split("\0")))
    return MirrorTree(root=root, stripped=frozenset(set(copied) - shipped))


# ---------------------------------------------------------------------------
# The lint steps the mirror runs
# ---------------------------------------------------------------------------

_INVOCATION = re.compile(
    r"^(?P<env>(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*)"
    r"python(?:3(?:\.\d+)?)?\s+(?P<script>scripts/[\w./-]+\.py)(?P<rest>.*)$"
)
# Shell the test cannot reproduce faithfully from a single line.
_UNREPRODUCIBLE = re.compile(r"[$`|;&<>\\]")


@dataclass(frozen=True)
class LintStep:
    where: str                           # "<workflow>::<job>"
    script: str
    args: tuple[str, ...]
    cwd: str
    env: tuple[tuple[str, str], ...]
    reproducible: bool


def _literal_env(*blocks: object) -> dict[str, str]:
    out: dict[str, str] = {}
    for block in blocks:
        if isinstance(block, dict):
            out.update({str(k): str(v) for k, v in block.items()
                        if not isinstance(v, (dict, list)) and "${{" not in str(v)})
    return out


def mirror_lint_steps(root: Path) -> Iterator[LintStep]:
    for wf in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        wf_run = ((doc.get("defaults") or {}).get("run") or {})
        for job_id, job in (doc.get("jobs") or {}).items():
            if not isinstance(job, dict) or mirror_truth(job.get("if")) is False:
                continue
            job_run = ((job.get("defaults") or {}).get("run") or {})
            for step in job.get("steps") or []:
                run = step.get("run")
                if not isinstance(run, str) or mirror_truth(step.get("if")) is False:
                    continue
                cwd = (step.get("working-directory") or job_run.get("working-directory")
                       or wf_run.get("working-directory") or ".")
                env = _literal_env(doc.get("env"), job.get("env"), step.get("env"))
                for line in run.splitlines():
                    m = _INVOCATION.match(line.strip())
                    if not m:
                        continue
                    rest = m.group("rest").split(" #", 1)[0].strip()
                    prefix = m.group("env").split()
                    ok = not _UNREPRODUCIBLE.search(rest) and not any("$" in a for a in prefix)
                    step_env = dict(env, **dict(a.split("=", 1) for a in prefix))
                    yield LintStep(
                        where=f"{wf.name}::{job_id}",
                        script=m.group("script"),
                        args=tuple(shlex.split(rest)) if ok else (),
                        cwd=cwd,
                        env=tuple(sorted(step_env.items())),
                        reproducible=ok,
                    )


def _exempt_reason(script: Path) -> str | None:
    head = script.read_text(encoding="utf-8", errors="replace")[:4000]
    i = head.find(EXEMPT_MARKER)
    return head[i + len(EXEMPT_MARKER):].split("\n", 1)[0].strip() if i >= 0 else None


def _mirror_env(extra: dict[str, str]) -> dict[str, str]:
    """What a lint sees in the mirror's Actions job, minus anything dangerous."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GITHUB_", "GIT_")) and k != "PYTHONPATH"
           and not _is_db_setting(k, v)}
    env.update({k: v for k, v in extra.items() if not _is_db_setting(k, v)})
    env.update(
        CI="true",
        GITHUB_ACTIONS="true",
        GITHUB_REPOSITORY=MIRROR_REPOSITORY,
        GITHUB_EVENT_NAME="push",
        GITHUB_REF="refs/heads/main",
        PYTHONDONTWRITEBYTECODE="1",
    )
    return env


def _run_lint(root: Path, step: LintStep) -> tuple[LintStep, int, str]:
    proc = subprocess.run(
        [sys.executable, str(root / step.script), *step.args],
        cwd=root / step.cwd, env=_mirror_env(dict(step.env)),
        capture_output=True, text=True, timeout=600,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:])
    return step, proc.returncode, tail


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        (None, True),
        ("github.repository != 'Glad-Labs/poindexter'", False),
        ("github.repository == 'Glad-Labs/poindexter'", True),
        ("github.repository == 'glad-labs/POINDEXTER'", True),
        ("'Glad-Labs/poindexter' != github.repository", False),
        # test-backend: runs on the mirror's push
        ("${{ !(github.event_name == 'push' && github.event.repository.private) }}", True),
        # lint-main: private pushes only
        ("${{ github.event_name == 'push' && github.event.repository.private }}", False),
        ("github.event.repository.private == false", True),
        ("github.event.repository.private != false", False),
        ("${{ needs.changes.outputs.deps == 'true' || github.event_name == 'schedule' }}", None),
        ("steps.changes.outputs.needs_tests == 'true'", None),
        ("github.repository == 'Glad-Labs/poindexter' && github.event_name == 'workflow_dispatch'",
         None),
        ("(github.repository != 'Glad-Labs/poindexter')", False),
        ("github.repository != 'Glad-Labs/poindexter' || github.event_name == 'schedule'", None),
        ("github.repository != 'Glad-Labs/poindexter' && github.event_name == 'schedule'", False),
        ("always()", True),
        ("success()", None),
        (True, True),
        (False, False),
    ],
)
def test_job_guards_are_decided_as_actions_decides_them_on_the_mirror(expr, expected):
    """A guard read as False drops its job's lints from the check, so each idiom
    the workflows use is pinned, including the ones that must stay undecided."""
    assert mirror_truth(expr) is expected


def test_no_database_setting_reaches_a_lint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Workflow literals are passed through, so the DSN a CI job gives its service
    container would reach a developer's real Postgres without this filter."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://from-the-shell/prod")
    monkeypatch.setenv("PGHOST", "db.example.invalid")
    env = _mirror_env({
        "DATABASE_URL": "postgres://postgres:postgres@localhost:5432/poindexter_test",
        "OTHER_DSN": "postgresql://x@localhost/y",
        "GITHUB_REPOSITORY": "someone/else",
        "KEPT": "plain",
    })
    assert not {"DATABASE_URL", "OTHER_DSN", "PGHOST"} & env.keys()
    assert env["KEPT"] == "plain"
    assert env["GITHUB_REPOSITORY"] == MIRROR_REPOSITORY


def test_the_mirror_lint_wall_was_found(mirror: MirrorTree) -> None:
    """Guard the guard: a derivation that found nothing would pass vacuously."""
    steps = list(mirror_lint_steps(mirror.root))
    scripts = {s.script for s in steps}
    assert len(scripts) >= 20, sorted(scripts)
    # The two that held the mirror red, and one from each other workflow family.
    for expected in (
        "scripts/ci/comment_reference_lint.py",
        "scripts/ci/settings_phantom_read_lint.py",
        "scripts/ci/docs_link_rot_lint.py",
        "scripts/ci/migrations_lint.py",
        "scripts/ci/check-action-pins.py",
    ):
        assert expected in scripts, f"{expected} not derived from the mirror's workflows"
    wheres = {s.where for s in steps}
    assert any(w.startswith("unit-tests.yml::test-backend") for w in wheres), wheres
    assert not any(w.endswith("::lint-main") for w in wheres), (
        "lint-main only runs on private-repository pushes; it must not be derived "
        "as a mirror job"
    )
    assert not any(w.startswith("ports-lint.yml") for w in wheres), (
        "ports-lint is guarded with github.repository != 'Glad-Labs/poindexter'"
    )


def test_every_mirror_lint_step_passes_on_the_mirror_tree(mirror: MirrorTree) -> None:
    problems: list[str] = []
    exempt: list[str] = []
    runnable: dict[tuple, LintStep] = {}
    for step in mirror_lint_steps(mirror.root):
        path = mirror.root / step.script
        if not path.is_file():
            problems.append(
                f"{step.where} runs {step.script}, which the mirror does not have. "
                "Strip the step, or guard its job with "
                "`if: github.repository != 'Glad-Labs/poindexter'`."
            )
            continue
        reason = _exempt_reason(path)
        if reason:
            exempt.append(f"{step.script} ({reason})")
            continue
        if not step.reproducible:
            problems.append(
                f"{step.where} invokes {step.script} with shell this test cannot "
                "reproduce. Simplify the invocation, or declare "
                f"`{EXEMPT_MARKER} <reason>` in the script."
            )
            continue
        runnable.setdefault((step.script, step.args, step.cwd, step.env), step)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda s: _run_lint(mirror.root, s), runnable.values()))
    for step, code, tail in results:
        if code != 0:
            problems.append(
                f"{step.where}: `python {step.script} {' '.join(step.args)}` exited "
                f"{code} on the mirror tree:\n{tail}"
            )

    assert not problems, (
        "The public mirror's CI would fail. It runs these steps on the tree the "
        "sync publishes, so a check that depends on which files exist can "
        "disagree with itself there. Make the check correct on the stripped tree "
        "without naming a stripped file in anything that ships (see "
        "scripts/ci/lib_public_mirror.py).\n\n" + "\n\n".join(problems)
        + (f"\n\n(skipped, mirror-tree-exempt: {'; '.join(exempt)})" if exempt else "")
        + "\n\n(Running locally? Like the sync, the simulation ships TRACKED files "
        "only, so a new file is missing from it until you `git add` it.)"
    )


def test_no_ratchet_baseline_names_a_stripped_file(mirror: MirrorTree) -> None:
    """A baseline ships, and its key is the file's path, so an entry for a stripped
    file discloses it. Fix the finding in the stripped file instead."""
    prefix = "src/cofounder_agent/"
    # Some baselines key by path relative to the backend root, so look for both.
    spellings = {p: [p] + ([p[len(prefix):]] if p.startswith(prefix) else [])
                 for p in mirror.stripped}
    leaks: list[str] = []
    for baseline in sorted((mirror.root / "scripts" / "ci").glob("*.json")):
        text = baseline.read_text(encoding="utf-8")
        json.loads(text)   # a baseline that is not JSON is its own failure
        leaks += [f"{baseline.name}: {path}" for path, forms in sorted(spellings.items())
                  if any(form in text for form in forms)]
    assert not leaks, (
        "A ratchet baseline that ships to the public mirror names files the sync "
        "strips. Fix those findings in the stripped files, then re-baseline:\n  "
        + "\n  ".join(leaks)
    )


def test_stripped_set_is_plausible(mirror: MirrorTree) -> None:
    """The sync stripped something, and this file is among it."""
    assert this_file() in mirror.stripped
    assert SYNC_SCRIPT in mirror.stripped
    assert len(mirror.stripped) > 50, len(mirror.stripped)


def this_file() -> str:
    return Path(__file__).resolve().relative_to(REPO).as_posix()
