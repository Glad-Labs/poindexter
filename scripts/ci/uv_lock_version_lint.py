#!/usr/bin/env python3
"""CI lint: every ``uv.lock`` agrees with its ``pyproject.toml`` version.

A uv lockfile embeds the version of its own root package::

    [[package]]
    name = "poindexter-mcp"
    version = "0.120.0"
    source = { editable = "." }

release-please bumps ``<dir>/pyproject.toml`` through its ``extra-files``
list. Until 2026-08-09 the matching ``uv.lock`` was NOT on that list, so the
two drifted apart on every release and stayed apart until somebody ran the
test suite — ``uv run`` silently re-locks, and an unrelated one-line diff
appeared in whatever PR they were working on. mcp-server was three releases
behind (0.116.0 vs 0.119.0) when this was found; the two sibling servers were
four behind.

Fixing the config is only half the job. release-please's TOML updater
**warns and continues** when its JSONPath matches nothing
(``No entries modified in $.package[...]``) — it does not fail the release.
This repo has already been burned by exactly that silence: a stray
``release-type`` input put the action in bootstrap mode, ``extra-files``
never fired, and every registered version file sat frozen for ~100 releases
(see the comment block in ``.github/workflows/release-please.yml``). So the
config change needs a guard that turns "the updater quietly stopped matching"
into a red check, and that is this lint.

What it checks
--------------
For every ``<dir>/uv.lock`` with a sibling ``<dir>/pyproject.toml``: the
version of the lock's editable root package equals the project version. A
directory missing from the checkout is skipped, not failed — the public
mirror strips ``mcp-server-gladlabs/``, and CI runs there too.

Run:
    python scripts/ci/uv_lock_version_lint.py

Exit 0 = every lock matches, exit 1 = at least one drifted.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _project_name_and_version(pyproject: Path) -> tuple[str | None, str | None]:
    """Read (name, version) from a PEP 621 or poetry ``pyproject.toml``."""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project") or {}
    if project.get("version"):
        return project.get("name"), project.get("version")
    poetry = (data.get("tool") or {}).get("poetry") or {}
    return poetry.get("name"), poetry.get("version")


def _locked_root_version(lock: Path, name: str | None) -> str | None:
    """Version of the lock's editable root package, or None if absent.

    A workspace lock can hold several editable entries, so prefer the one
    whose name matches the pyproject; fall back to a lone editable entry.
    """
    data = tomllib.loads(lock.read_text(encoding="utf-8"))
    editable = [
        pkg
        for pkg in data.get("package", [])
        if isinstance(pkg.get("source"), dict) and "editable" in pkg["source"]
    ]
    if not editable:
        return None
    for pkg in editable:
        if name and pkg.get("name") == name:
            return pkg.get("version")
    return editable[0].get("version") if len(editable) == 1 else None


def main() -> int:
    drifted: list[str] = []
    checked = 0

    for lock in sorted(REPO_ROOT.glob("*/uv.lock")):
        pyproject = lock.with_name("pyproject.toml")
        if not pyproject.is_file():
            continue

        name, declared = _project_name_and_version(pyproject)
        if not declared:
            continue

        locked = _locked_root_version(lock, name)
        if locked is None:
            continue

        checked += 1
        if locked != declared:
            rel = lock.relative_to(REPO_ROOT)
            drifted.append(
                f"  {rel}: locked {locked!r} != pyproject {declared!r} "
                f"(package {name!r})"
            )

    if drifted:
        print("uv.lock is out of sync with its pyproject.toml version:\n")
        print("\n".join(drifted))
        print(
            "\nEach of these directories is registered in "
            "release-please-config.json so BOTH files bump together. A "
            "mismatch means either the release-please TOML updater stopped "
            "matching (check the release PR for 'No entries modified in "
            "$.package[...]' — it warns rather than failing), or someone "
            "hand-edited a version.\n"
            "\nTo resolve: run `uv lock` in the directory to re-sync the "
            "lock, then confirm the JSONPath in release-please-config.json "
            "still names that package."
        )
        return 1

    print(f"uv.lock version lint: {checked} lockfile(s) match their pyproject.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
