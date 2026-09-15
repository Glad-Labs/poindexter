"""CI steps that run on the runner's SYSTEM interpreter must stay 3.8-safe.

Most `scripts/ci/*` lints run after `Setup Python`, so they get a modern
interpreter and may use anything. A few are deliberately **unconditional** —
they must run even on a docs-only PR where `Setup Python` is skipped — so the
workflow invokes them with bare `python3`. On the self-hosted runner that is
**Python 3.8.10**.

That combination has already failed twice in one day:

* `python scripts/ci/...` → exit 127 (the self-hosted runner has no `python`
  on PATH), fixed by switching to `python3` in #3762;
* `python3 scripts/ci/...` → `AttributeError: 'PosixPath' object has no
  attribute 'is_relative_to'`, because that API is 3.9+.

Neither failure is visible locally or on a GitHub-hosted runner, where
`python3` is 3.10+. It only appears when the job lands on the self-hosted
runner, which makes it look like a flake rather than a version floor.

This test pins the floor by scanning the affected scripts for APIs that do not
exist on 3.8. It is a source scan, not an execution: CI has no 3.8 interpreter
to run them under, which is exactly why the gap stayed invisible.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return next(
        p
        for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )


def _workflows() -> list[Path]:
    return sorted((_repo_root() / ".github" / "workflows").glob("*.yml"))


def system_python_scripts() -> set[str]:
    """Scripts a workflow invokes with bare `python3` (the system interpreter).

    `python` means `Setup Python` has put a modern interpreter on PATH, so
    those are unconstrained. `python3` is the system one.
    """
    found: set[str] = set()
    for wf in _workflows():
        for m in re.finditer(
            r"run:\s*python3\s+(scripts/ci/[\w./-]+\.py)", wf.read_text(encoding="utf-8")
        ):
            found.add(m.group(1))
    return found


# Names that simply do not exist on Python 3.8.
_FORBIDDEN_ATTRS = {
    "is_relative_to": "Path.is_relative_to is 3.9+ — use try/except ValueError around relative_to()",
    "removeprefix": "str.removeprefix is 3.9+ — slice, or use a helper",
    "removesuffix": "str.removesuffix is 3.9+ — slice, or use a helper",
}
_FORBIDDEN_IMPORTS = {
    "graphlib": "graphlib is 3.9+",
    "zoneinfo": "zoneinfo is 3.9+ — use a vendored tz or pass UTC through",
}


def _violations(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    out: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:  # 3.10 match/PEP-604 would land here on 3.8
        return [f"{path.name}:{exc.lineno}: does not parse ({exc.msg})"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRS:
            out.append(f"{path.name}:{node.lineno}: .{node.attr}() — {_FORBIDDEN_ATTRS[node.attr]}")
        elif isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root in _FORBIDDEN_IMPORTS:
                    out.append(f"{path.name}:{node.lineno}: import {root} — {_FORBIDDEN_IMPORTS[root]}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in _FORBIDDEN_IMPORTS:
                out.append(f"{path.name}:{node.lineno}: from {root} — {_FORBIDDEN_IMPORTS[root]}")
    return out


def test_scan_floor_finds_the_system_python_steps():
    """If the workflow scan breaks, every assertion below passes vacuously."""
    scripts = system_python_scripts()
    assert scripts, (
        "found no `run: python3 scripts/ci/...` steps — the extraction broke, "
        "or the steps were renamed. Either way this gate is disarmed."
    )
    assert "scripts/ci/dockerfile_copy_closure_lint.py" in scripts, (
        "the COPY-closure lint is the known unconditional system-python step; "
        "if it moved, update this test rather than deleting it"
    )


@pytest.mark.parametrize("rel", sorted(system_python_scripts()))
def test_system_python_scripts_are_38_safe(rel):
    path = _repo_root() / rel
    if not path.is_file():
        pytest.skip(f"{rel} not present")
    bad = _violations(path)
    assert not bad, (
        f"{rel} runs on the runner's SYSTEM python (3.8.10 on the self-hosted "
        "runner) and uses APIs that do not exist there:\n  "
        + "\n  ".join(bad)
        + "\n\nThis fails ONLY when the job lands on the self-hosted runner, so "
        "it reads as a flake. Either rewrite for 3.8, or make the step "
        "conditional and run it with `python` after Setup Python."
    )


def test_the_known_39_api_is_actually_detected():
    """Guards the guard: a detector that matches nothing passes everything."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sample.py"
        p.write_text(
            "from pathlib import Path\n"
            "def f(a: Path, b: Path):\n"
            "    return a.is_relative_to(b) or 'x'.removeprefix('y')\n",
            encoding="utf-8",
        )
        bad = _violations(p)
    assert len(bad) == 2, bad
    assert any("is_relative_to" in b for b in bad)
    assert any("removeprefix" in b for b in bad)
