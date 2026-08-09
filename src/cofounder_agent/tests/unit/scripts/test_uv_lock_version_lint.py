"""Unit + contract tests for ``scripts/ci/uv_lock_version_lint.py``.

The lint guards the half of the release-please fix that can fail silently:
the TOML updater *warns* and continues when its JSONPath matches nothing, so
a lock could quietly stop being bumped exactly the way ``extra-files`` once
sat frozen for ~100 releases. This suite pins both layers:

1. **Repo contract** — run the lint over the live tree and assert exit 0.
   Fails the moment a real lock drifts from its pyproject.
2. **Unit** — the two parsers and the ``main`` exit-code contract against
   synthetic trees, including the shapes that must be *skipped* rather than
   failed (a stripped directory, a lock with no editable root).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# scripts/ci is a flat directory (no __init__.py); import the linter by file
# path. Same pattern as test_ports_lint.py / test_grafana_panels_lint.py.
REPO_ROOT = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / "scripts" / "ci" / "uv_lock_version_lint.py").exists()
)
LINTER_PATH = REPO_ROOT / "scripts" / "ci" / "uv_lock_version_lint.py"


def _load_linter():
    spec = importlib.util.spec_from_file_location("uv_lock_version_lint", LINTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LOCK_TEMPLATE = """\
version = 1

[[package]]
name = "httpx"
version = "0.27.0"
source = {{ registry = "https://pypi.org/simple" }}

[[package]]
name = "{pkg}"
version = "{locked}"
source = {{ editable = "." }}
"""


def _write_pair(root: Path, name: str, pkg: str, declared: str, locked: str) -> Path:
    """Lay down a <name>/ dir holding a pyproject + uv.lock pair."""
    d = root / name
    d.mkdir()
    (d / "pyproject.toml").write_text(
        f'[project]\nname = "{pkg}"\nversion = "{declared}"\n', encoding="utf-8"
    )
    (d / "uv.lock").write_text(
        _LOCK_TEMPLATE.format(pkg=pkg, locked=locked), encoding="utf-8"
    )
    return d


# ---------------------------------------------------------------------------
# Repo contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_live_tree_locks_match_their_pyprojects():
    """The real mcp-server* locks agree with their pyproject versions.

    This is the drift the lint exists to catch: release-please bumps both
    files in one commit, and anything else means the updater stopped firing.
    """
    linter = _load_linter()
    assert linter.main() == 0


@pytest.mark.unit
def test_every_locked_dir_is_registered_with_release_please():
    """A lockfile the lint watches must also be one release-please bumps.

    Otherwise the guard goes red on the next release with no mechanism to
    fix it — the lint and the config have to describe the same set.
    """
    import json

    config = json.loads(
        (REPO_ROOT / "release-please-config.json").read_text(encoding="utf-8")
    )
    registered = {
        entry["path"]
        for entry in config["packages"]["."]["extra-files"]
        if isinstance(entry, dict) and entry.get("path", "").endswith("uv.lock")
    }
    on_disk = {
        str(lock.relative_to(REPO_ROOT))
        for lock in REPO_ROOT.glob("*/uv.lock")
        if lock.with_name("pyproject.toml").is_file()
    }
    assert on_disk <= registered, (
        f"lockfile(s) not registered in release-please-config.json: "
        f"{sorted(on_disk - registered)}"
    )


@pytest.mark.unit
def test_registered_jsonpaths_name_the_actual_root_package():
    """The JSONPath filters on a package name — pin it to the real one.

    release-please only *warns* when a JSONPath matches nothing, so a
    renamed package would silently stop bumping. This asserts the name in
    the config is the name in the lock.
    """
    import json
    import re

    import tomllib

    config = json.loads(
        (REPO_ROOT / "release-please-config.json").read_text(encoding="utf-8")
    )
    checked = 0
    for entry in config["packages"]["."]["extra-files"]:
        if not (isinstance(entry, dict) and entry.get("path", "").endswith("uv.lock")):
            continue
        lock = REPO_ROOT / entry["path"]
        if not lock.is_file():  # stripped on the public mirror
            continue
        named = re.search(r"@\.name\.value\s*==\s*'([^']+)'", entry["jsonpath"])
        assert named, f"{entry['path']}: JSONPath must filter on a package name"
        data = tomllib.loads(lock.read_text(encoding="utf-8"))
        editable = [
            pkg["name"]
            for pkg in data.get("package", [])
            if isinstance(pkg.get("source"), dict) and "editable" in pkg["source"]
        ]
        assert named.group(1) in editable, (
            f"{entry['path']}: config names {named.group(1)!r} but the lock's "
            f"editable root package(s) are {editable}"
        )
        checked += 1
    assert checked, "no uv.lock extra-files entries found to verify"


# ---------------------------------------------------------------------------
# Unit — parsers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reads_pep621_name_and_version(tmp_path: Path):
    linter = _load_linter()
    p = tmp_path / "pyproject.toml"
    p.write_text('[project]\nname = "thing"\nversion = "1.2.3"\n', encoding="utf-8")
    assert linter._project_name_and_version(p) == ("thing", "1.2.3")


@pytest.mark.unit
def test_falls_back_to_poetry_table(tmp_path: Path):
    """brain/ declares its version under [tool.poetry], not [project]."""
    linter = _load_linter()
    p = tmp_path / "pyproject.toml"
    p.write_text(
        '[tool.poetry]\nname = "brainy"\nversion = "4.5.6"\n', encoding="utf-8"
    )
    assert linter._project_name_and_version(p) == ("brainy", "4.5.6")


@pytest.mark.unit
def test_locked_root_version_ignores_registry_packages(tmp_path: Path):
    """Only the editable root counts — a dependency's version is not ours."""
    linter = _load_linter()
    lock = tmp_path / "uv.lock"
    lock.write_text(
        'version = 1\n\n'
        '[[package]]\nname = "httpx"\nversion = "0.27.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n\n'
        '[[package]]\nname = "mine"\nversion = "9.9.9"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )
    assert linter._locked_root_version(lock, "mine") == "9.9.9"


@pytest.mark.unit
def test_locked_root_version_is_none_without_an_editable_entry(tmp_path: Path):
    linter = _load_linter()
    lock = tmp_path / "uv.lock"
    lock.write_text(
        'version = 1\n\n[[package]]\nname = "httpx"\nversion = "0.27.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n',
        encoding="utf-8",
    )
    assert linter._locked_root_version(lock, "httpx") is None


# ---------------------------------------------------------------------------
# Unit — main() exit-code contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_passes_when_versions_agree(tmp_path: Path, monkeypatch, capsys):
    linter = _load_linter()
    _write_pair(tmp_path, "svc", "svc-pkg", "1.0.0", "1.0.0")
    monkeypatch.setattr(linter, "REPO_ROOT", tmp_path)

    assert linter.main() == 0
    assert "1 lockfile(s) match" in capsys.readouterr().out


@pytest.mark.unit
def test_main_fails_and_names_the_drifted_lock(tmp_path: Path, monkeypatch, capsys):
    linter = _load_linter()
    _write_pair(tmp_path, "svc", "svc-pkg", "1.1.0", "1.0.0")
    monkeypatch.setattr(linter, "REPO_ROOT", tmp_path)

    assert linter.main() == 1
    out = capsys.readouterr().out
    assert "svc/uv.lock" in out
    assert "'1.0.0'" in out and "'1.1.0'" in out
    # The remediation has to be actionable, not just a complaint.
    assert "uv lock" in out


@pytest.mark.unit
def test_main_skips_a_lock_with_no_sibling_pyproject(tmp_path: Path, monkeypatch, capsys):
    """A stripped directory is absent, not broken — the public mirror drops
    mcp-server-gladlabs/ and still runs CI."""
    linter = _load_linter()
    orphan = tmp_path / "orphan"
    orphan.mkdir()
    (orphan / "uv.lock").write_text(
        '[[package]]\nname = "x"\nversion = "1.0.0"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(linter, "REPO_ROOT", tmp_path)

    assert linter.main() == 0
    assert "0 lockfile(s)" in capsys.readouterr().out
