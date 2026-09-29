#!/usr/bin/env python3
"""Every ``-m <module>`` launch string that names a backend module must resolve.

Why
===

A module handed to ``python -m`` is resolved by the interpreter at launch and
nowhere earlier. No import-time check sees it, and it is not a string Python
code resolves, so ``poindexter/services/module_paths.py`` (the seam for those)
never sees it either. It lives in compose ``command:`` lines, Dockerfile
``CMD``s, shell launchers, systemd units, workflows and runbooks, and it breaks
silently until the moment something launches it.

Glad-Labs/poindexter#1046 moved the backend under ``poindexter.*``. Step 3
rewrote every import and every string module path in Python, and step 5
deleted the flat roots, so a flat spelling now dies with ``No module named
'services'``. Launch strings are not Python, and four survived with the flat
spelling: both voice-agent compose commands, the voice image's default ``CMD``
and ``scripts/start-livekit-voice-bot.sh``. Voice was parked, so nothing
failed. Un-parking it would have crash-looped both containers at import. Found
2026-09-28, along with five more in runbooks and module docstrings.

What it checks
==============

Every ``-m <dotted.name>`` in a text file, in shell form
(``python -m poindexter.brain.brain_daemon``), exec-list form
(``["python", "-m", "poindexter.brain.brain_daemon"]``) or a YAML block list
(``- -m`` on one line, ``- poindexter.brain.brain_daemon`` on the next):

1. **Flat spelling.** The first segment names a subpackage of ``poindexter``
   (``services``, ``brain``, ``utils`` ...). None of them is a top-level
   package, so the launch fails. Reported with the ``poindexter.``-prefixed
   replacement and whether that resolves.
2. **Stranded launch.** The first segment is ``poindexter``, so the name must
   exist under ``src/cofounder_agent/``: ``<name>.py``, or a package with a
   ``__main__.py`` (which is what ``-m`` runs for a package). This is the
   check that catches the NEXT rename, not just this one.
3. Anything else (``pytest``, ``pip``, ``http.server``) is not ours: skipped.

The flat roots are DERIVED from the package directory, so a new subpackage is
covered the day it is added.

Scope: every file git tracks, plus untracked files it does not ignore (so a
new launcher is checked before ``git add``). Excluded: dated records that
describe the tree as it was, namely ``docs/superpowers/`` (plans and specs;
#1046 left their flat imports alone on purpose) and ``CHANGELOG.md``
(release-please copies merged commit subjects into it verbatim). Binary files
are skipped. A line that shows a retired spelling on purpose opts out with
``launch-path-ok`` anywhere on it.

Runs in ``migrations-smoke.yml``, which has no changed-paths gate, so a PR
that touches only a compose file or a runbook is still checked.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_dir, require_scanned  # noqa: E402

LINT = "module_launch_paths_lint"
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_REL = "src/cofounder_agent"
PACKAGE = "poindexter"

# Dated records: a flat spelling in them is history, not a launch instruction.
EXCLUDED_DIRS = ("docs/superpowers/",)
EXCLUDED_NAMES = frozenset({"CHANGELOG.md"})

OPT_OUT = "launch-path-ok"

# `-m` as its own token (not the tail of `--permission-mode`), then the module:
# shell form `-m a.b` (a `\` continuation may split them), exec-list form
# `"-m", "a.b"`, or a YAML block list with `- a.b` on the next line. The module
# has to end where the argument ends (a quote, whitespace, a bracket, a
# backtick or a sentence-ending period), so a commit message such as
# `-m "services.x: fix"` is not read as a launch.
_LAUNCH = re.compile(
    r"""(?<![\w-])-m["']?(?:\s*,)?\s*(?:\\\s+)?(?:-\s+)?["']?"""
    r"""(?P<module>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)"""
    r"""(?=["'\s\]),;`|&<>]|\.(?:\s|$)|$)"""
)


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    module: str
    message: str

    def render(self) -> str:
        return f"  {self.path}:{self.line}  -m {self.module}\n      {self.message}"


def flat_roots(package_dir: Path) -> frozenset[str]:
    """The subpackages of ``poindexter``, derived from the tree.

    As a first segment every one of them is a flat spelling. None is a
    top-level package: step 5 of #1046 deleted the old flat roots, and ``cli``
    and ``memory`` were never top-level to begin with.
    """
    return frozenset(
        d.name
        for d in package_dir.iterdir()
        if d.is_dir()
        and d.name.isidentifier()
        and not d.name.startswith("__")
        and next(d.rglob("*.py"), None) is not None
    )


def resolves(module: str, backend_root: Path) -> bool:
    """True when ``python -m <module>`` run from ``backend_root`` finds something to run."""
    base = backend_root.joinpath(*module.split("."))
    return base.with_suffix(".py").is_file() or (base / "__main__.py").is_file()


def _line_at(text: str, pos: int) -> tuple[int, str]:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text.count("\n", 0, pos) + 1, text[start : end if end != -1 else len(text)]


def launch_problems(
    text: str, *, path: str, roots: frozenset[str], backend_root: Path
) -> tuple[list[Problem], int]:
    """Check every project launch string in ``text``.

    Returns the problems and how many project launch strings were examined
    (flat or canonical), which feeds the scan floor.
    """
    problems: list[Problem] = []
    examined = 0
    for match in _LAUNCH.finditer(text):
        module = match["module"]
        head = module.split(".", 1)[0]
        if head != PACKAGE and head not in roots:
            continue
        examined += 1
        line_no, line = _line_at(text, match.start("module"))
        if OPT_OUT in line:
            continue
        if head == PACKAGE:
            if resolves(module, backend_root):
                continue
            rel = f"{BACKEND_REL}/{module.replace('.', '/')}"
            message = (
                f"does not resolve: no {rel}.py and no {rel}/__main__.py. The module "
                "was renamed or removed; point the launch at its current path."
            )
        else:
            canonical = f"{PACKAGE}.{module}"
            verdict = (
                "which resolves"
                if resolves(canonical, backend_root)
                else "which does NOT resolve either: find the module's current path"
            )
            message = (
                "retired flat spelling (poindexter#1046 deleted the flat roots); "
                f"use -m {canonical}, {verdict}."
            )
        problems.append(Problem(path, line_no, module, message))
    return problems, examined


def _excluded(rel: str) -> bool:
    return rel.startswith(EXCLUDED_DIRS) or rel.rsplit("/", 1)[-1] in EXCLUDED_NAMES


def list_files(root: Path) -> list[str]:
    """Tracked files plus untracked ones git does not ignore, repo-relative."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(
            f"{LINT}: could not list files with git in {root}: {exc}. "
            "This lint scans what git tracks; run it inside the checkout."
        ) from exc
    return list(dict.fromkeys(p for p in out.decode("utf-8", "replace").split("\0") if p))


def scan(root: Path) -> tuple[list[Problem], int, int]:
    """Return (problems, text files scanned, project launch strings examined)."""
    backend_root = root / BACKEND_REL
    require_dir(backend_root / PACKAGE, lint=LINT)
    roots = flat_roots(backend_root / PACKAGE)
    problems: list[Problem] = []
    scanned = examined = 0
    for rel in list_files(root):
        path = root / rel
        if _excluded(rel) or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:  # binary
            continue
        scanned += 1
        found, n = launch_problems(
            data.decode("utf-8", "replace"), path=rel, roots=roots, backend_root=backend_root
        )
        problems.extend(found)
        examined += n
    return problems, scanned, examined


def main() -> int:
    problems, scanned, examined = scan(REPO_ROOT)
    require_scanned(scanned, lint=LINT, roots=(REPO_ROOT,))
    require_scanned(examined, lint=LINT, what=f"-m {PACKAGE}.* launch strings", roots=(REPO_ROOT,))
    if problems:
        print(f"{LINT}: FAIL - {len(problems)} launch string(s) name a module that will not load\n")
        print("\n".join(p.render() for p in problems))
        print(
            "\n`python -m` resolves its module only when something launches it, so a\n"
            "stale path fails at container start or in an operator's terminal, never\n"
            "in CI. Fix the path. If a line shows a retired spelling on purpose (a\n"
            f"migration note), add `{OPT_OUT}` to that line."
        )
        return 1
    print(f"{LINT}: OK ({examined} project launch string(s) resolve across {scanned} text files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
