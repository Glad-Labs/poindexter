"""Contract tests for the `poindexter` distribution (GH-41; Glad-Labs/poindexter#1046 step 5).

``src/cofounder_agent/pyproject.toml`` is the ONE distribution -- named
``poindexter``, shipping the ``poindexter`` package and nothing else. The
standalone manifest that used to live at ``poindexter/pyproject.toml`` is gone.
These tests verify:

- the manifest's identity (name, version, description, readme, license, the
  single console script, the single package include);
- the version matches the release-please manifest, and BOTH release-please
  configs bump this file (the public mirror runs its own);
- nothing re-grows an umbrella include or a second manifest;
- the package imports and exposes the console entry point;
- the README's quick-start commands resolve against the live CLI.

They do NOT build the wheel -- that is the release workflow's clean-install
smoke job, which also asserts no retired flat root is installed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import tomllib


def _repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".release-please-manifest.json").is_file():
            return parent
    return None


REPO_ROOT = _repo_root()

pytestmark = pytest.mark.skipif(
    REPO_ROOT is None,
    reason=".release-please-manifest.json not visible — docker worker "
    "mounts only src/cofounder_agent as /app; this test runs on the host.",
)

BACKEND_DIR = (REPO_ROOT / "src" / "cofounder_agent") if REPO_ROOT else None
MANIFEST = (BACKEND_DIR / "pyproject.toml") if BACKEND_DIR else None
PKG_DIR = (BACKEND_DIR / "poindexter") if BACKEND_DIR else None
RETIRED_STANDALONE = (PKG_DIR / "pyproject.toml") if PKG_DIR else None


def _manifest() -> dict:
    with MANIFEST.open("rb") as fh:
        return tomllib.load(fh)


def test_manifest_is_the_one_distribution_named_poindexter() -> None:
    data = _manifest()
    project = data["project"]
    assert project["name"] == "poindexter"
    assert project.get("version"), "project.version must be set"
    assert project.get("description")
    assert project.get("readme") == "poindexter/README.md"
    assert (BACKEND_DIR / project["readme"]).is_file()
    assert project.get("license") == "Apache-2.0"
    assert "3.13" in project.get("requires-python", "")
    # Exactly one console script: the CLI. The old `api = cofounder_agent.main:app`
    # rode on the umbrella include and is gone with it.
    assert project.get("scripts") == {"poindexter": "poindexter.cli.app:main"}


def test_manifest_ships_only_the_poindexter_package() -> None:
    packages = _manifest()["tool"]["poetry"]["packages"]
    assert packages == [{"include": "poindexter", "from": "."}], (
        "[tool.poetry].packages must ship exactly the `poindexter` package -- an "
        "umbrella include (`cofounder_agent`) or a repo-root `brain` would put a "
        "second copy of the tree, or a retired flat root, into the wheel"
    )
    assert (PKG_DIR / "brain" / "__init__.py").is_file(), "poindexter/brain rides in the one include"
    assert (PKG_DIR / "cli" / "app.py").is_file()


def test_standalone_manifest_is_gone() -> None:
    assert not RETIRED_STANDALONE.exists(), (
        f"{RETIRED_STANDALONE} is back -- there is one distribution manifest, "
        "src/cofounder_agent/pyproject.toml (poindexter#1046 step 5)"
    )


def test_core_runtime_dependencies_declared() -> None:
    deps = _manifest()["tool"]["poetry"]["dependencies"]
    assert {"click", "asyncpg", "httpx"} <= set(deps), (
        f"the CLI's core runtime deps must stay declared: {sorted(deps)[:10]}..."
    )


def test_extras_partition_the_optional_dependencies() -> None:
    """Every `optional = true` dependency belongs to an extra (an orphan optional
    dep would never install), and every extra member is marked optional (a
    non-optional member makes the extra a no-op). The three lean extras from
    poindexter#1046 step 6 must exist."""
    data = _manifest()
    deps = data["tool"]["poetry"]["dependencies"]
    extras = data["project"]["optional-dependencies"]
    optional = {name for name, spec in deps.items() if isinstance(spec, dict) and spec.get("optional")}
    in_extras = {member for members in extras.values() for member in members}
    assert {"pipeline", "qa", "rag"} <= set(extras), sorted(extras)
    assert optional <= in_extras, f"optional deps in no extra: {sorted(optional - in_extras)}"
    assert in_extras <= optional, f"extra members not marked optional: {sorted(in_extras - optional)}"


def test_version_matches_release_manifest() -> None:
    version = _manifest()["project"]["version"]
    manifest = json.loads((REPO_ROOT / ".release-please-manifest.json").read_text(encoding="utf-8"))
    assert version == manifest["."], (
        f"release-please manifest says {manifest['.']} but src/cofounder_agent/pyproject.toml "
        f"says {version}; release-please-config.json must list this file in extra-files"
    )


@pytest.mark.parametrize(
    "config_name", ["release-please-config.json", "release-please-config.poindexter.json"]
)
def test_release_please_bumps_the_manifest_and_not_the_retired_one(config_name: str) -> None:
    """Both configs -- the stack's and the one the public mirror runs -- must bump
    ``[project].version`` of the one manifest, and neither may still point at the
    deleted standalone file (release-please would fail the release PR on it)."""
    config = json.loads((REPO_ROOT / config_name).read_text(encoding="utf-8"))
    extra_files = [e for e in config["packages"]["."]["extra-files"] if isinstance(e, dict)]
    by_path = {(e["path"], e.get("jsonpath")) for e in extra_files}
    assert ("src/cofounder_agent/pyproject.toml", "$.project.version") in by_path, by_path
    assert not [e for e in extra_files if e["path"] == "src/cofounder_agent/poindexter/pyproject.toml"]


def test_poindexter_package_importable() -> None:
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    import poindexter  # noqa: F401
    from poindexter.cli.app import main  # noqa: F401

    # Click groups expose `.commands` — the CLI must at least register
    # the subcommands we document in the README.
    expected_groups = {"setup", "memory", "tasks", "posts", "settings", "costs", "vercel", "pro"}
    assert expected_groups.issubset(set(main.commands)), (
        f"missing CLI subcommands: {expected_groups - set(main.commands)}"
    )


# ---------------------------------------------------------------------------
# README quick-start command drift (cold-clone onboarding guard)
# ---------------------------------------------------------------------------
#
# The public README's Quick start tells a brand-new operator the exact
# `poindexter` commands to run from a fresh clone. If a command group or
# subcommand is renamed in the CLI but the README isn't updated, the very
# first command a stranger runs errors out — the worst possible onboarding
# moment. This guard parses every `poindexter <group> <sub>` invocation out
# of the README's fenced code blocks and asserts it resolves against the live
# Click app, so doc/CLI drift fails CI instead of failing a new user. The
# package README (the PyPI long description) gets the same guard.

# Same-line whitespace only: a command invocation never wraps, and `\s+` would
# read `…/Glad-Labs/poindexter\ncd poindexter` in a clone fence as `poindexter cd`.
_POINDEXTER_INVOCATION = re.compile(r"poindexter[ \t]+([a-z][\w-]*)(?:[ \t]+([a-z][\w-]*))?")


def _documented_commands(readme: Path) -> list[tuple[str, str, str]]:
    """Return (raw, group, subcommand) for each poindexter command in README fences."""
    fenced_blocks = re.findall(r"```[^\n]*\n(.*?)```", readme.read_text(encoding="utf-8"), flags=re.DOTALL)
    found: list[tuple[str, str, str]] = []
    for block in fenced_blocks:
        for match in _POINDEXTER_INVOCATION.finditer(block):
            found.append((match.group(0), match.group(1), match.group(2) or ""))
    return found


@pytest.mark.parametrize("readme_rel", ["README.md", "src/cofounder_agent/poindexter/README.md"])
def test_readme_quickstart_commands_resolve(readme_rel: str) -> None:
    """Every ``poindexter …`` command in the README must resolve in the CLI.

    Catches doc drift like ``poindexter content create`` (there is no
    ``content`` group — the real command is ``poindexter tasks create``),
    which would error on the first command a fresh-clone user runs.
    """
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from poindexter.cli.app import main

    documented = _documented_commands(REPO_ROOT / readme_rel)
    assert documented, f"expected to find poindexter commands in the code fences of {readme_rel}"
    valid_groups = set(main.commands)
    failures: list[str] = []
    for raw, group, sub in documented:
        if group in {"--help", "-h"}:
            continue
        if group not in valid_groups:
            failures.append(f"`{raw}` -> no such command group '{group}'")
            continue
        subcommands = getattr(main.commands[group], "commands", None)
        if sub and subcommands is not None and sub not in subcommands:
            failures.append(f"`{raw}` -> '{group}' has no subcommand '{sub}'")
    assert not failures, (
        f"{readme_rel} documents poindexter commands that don't resolve against the CLI:\n  "
        + "\n  ".join(failures)
    )
