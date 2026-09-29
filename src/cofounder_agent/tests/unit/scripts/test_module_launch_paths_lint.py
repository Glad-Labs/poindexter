"""module_launch_paths_lint: every `python -m` launch string names a module that loads.

poindexter#1046 deleted the flat roots, and four voice launch strings (two
compose commands, the voice image's CMD, the host launcher) kept the flat
spelling behind a parked compose profile. These tests pin the rules the lint
enforces and the launch forms it reads.

Fixture launch strings are assembled at run time (``DASH_M``). The real-tree
test at the bottom scans this file too, so a literal launch string here would
fail it.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
CI_DIR = REPO_ROOT / "scripts" / "ci"
LINT = CI_DIR / "module_launch_paths_lint.py"

DASH_M = "-" + "m"  # joined at run time; see the module docstring


def _load():
    spec = importlib.util.spec_from_file_location("module_launch_paths_lint", LINT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses resolve annotations through sys.modules[cls.__module__]
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _package(root: Path) -> Path:
    """A miniature backend for the lint to resolve launch strings against."""
    pkg = root / "src" / "cofounder_agent" / "poindexter"
    for rel in (
        "__init__.py",
        "__main__.py",
        "services/__init__.py",
        "services/voice.py",
        "services/bundle/__init__.py",  # a package with no __main__.py
        "brain/__init__.py",
        "brain/brain_daemon.py",
        "utils/helpers.py",  # a namespace package, like the real utils/
    ):
        path = pkg / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    (pkg / "__pycache__").mkdir()
    (pkg / "assets").mkdir()
    (pkg / "assets" / "logo.json").write_text("{}", encoding="utf-8")
    return root / "src" / "cofounder_agent"


def _check(mod, backend: Path, text: str):
    roots = mod.flat_roots(backend / "poindexter")
    return mod.launch_problems(text, path="f", roots=roots, backend_root=backend)


def _git_repo(root: Path, ignore: str = "") -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True, timeout=60)
    (root / ".gitignore").write_text(ignore, encoding="utf-8")


def _installed_lint(root: Path) -> Path:
    """Copy the lint into a fixture repo, where ``parents[2]`` makes that repo its root."""
    ci = root / "scripts" / "ci"
    ci.mkdir(parents=True)
    for name in ("module_launch_paths_lint.py", "lib_scan_floor.py"):
        shutil.copy2(CI_DIR / name, ci / name)
    return ci / "module_launch_paths_lint.py"


def test_flat_roots_are_derived_from_the_package(tmp_path):
    """Every subpackage holding Python, namespace packages included; nothing else."""
    mod = _load()
    backend = _package(tmp_path)
    assert mod.flat_roots(backend / "poindexter") == frozenset({"services", "brain", "utils"})


@pytest.mark.parametrize(
    "text",
    [
        f"python {DASH_M} services.voice --service\n",
        f'    command: ["python", "{DASH_M}", "services.voice", "--service"]\n',
        f'CMD ["python", "{DASH_M}", "services.voice", "--help"]\n',
        f"    command:\n      - python\n      - {DASH_M}\n      - services.voice\n",
        f'exec poetry run python {DASH_M} \\\n    services.voice "$@"\n',
        f"Run ``python {DASH_M} services.voice`` from the backend root.\n",
        f"start it with python {DASH_M} services.voice.\n",
    ],
    ids=[
        "shell",
        "compose-list",
        "dockerfile-cmd",
        "yaml-block-list",
        "continuation",
        "rst",
        "prose",
    ],
)
def test_flat_spelling_is_refused_in_every_launch_form(tmp_path, text):
    mod = _load()
    problems, examined = _check(mod, _package(tmp_path), text)
    assert examined == 1
    assert [p.module for p in problems] == ["services.voice"]
    assert f"use {DASH_M} poindexter.services.voice, which resolves" in problems[0].message


def test_problem_is_reported_on_the_line_holding_the_module(tmp_path):
    mod = _load()
    text = f"services:\n  bot:\n    command:\n      - python\n      - {DASH_M}\n      - services.voice\n"
    problems, _ = _check(mod, _package(tmp_path), text)
    assert [(p.line, p.module) for p in problems] == [(6, "services.voice")]


def test_canonical_launches_that_resolve_pass(tmp_path):
    mod = _load()
    text = "\n".join(
        [
            f"python {DASH_M} poindexter.services.voice",
            f"python {DASH_M} poindexter.utils.helpers",
            f'CMD ["python", "{DASH_M}", "poindexter.brain.brain_daemon"]',
            f"python {DASH_M} poindexter",  # a package: runs its __main__.py
        ]
    )
    assert _check(mod, _package(tmp_path), text) == ([], 4)


def test_canonical_launch_stranded_by_a_rename_is_refused(tmp_path):
    """The check that catches the NEXT rename: a canonical path to nothing."""
    mod = _load()
    problems, _ = _check(
        mod, _package(tmp_path), f"python {DASH_M} poindexter.services.voice_agent\n"
    )
    assert len(problems) == 1
    assert "does not resolve" in problems[0].message
    assert "poindexter/services/voice_agent.py" in problems[0].message


def test_package_without_a_main_module_is_not_runnable(tmp_path):
    mod = _load()
    problems, _ = _check(mod, _package(tmp_path), f"python {DASH_M} poindexter.services.bundle\n")
    assert len(problems) == 1 and "__main__.py" in problems[0].message


def test_flat_spelling_whose_prefixed_form_is_also_missing_says_so(tmp_path):
    """A flat `brain.daemon` never existed; prefixing it is not the fix, and the report says so."""
    mod = _load()
    problems, _ = _check(mod, _package(tmp_path), f"python {DASH_M} brain.daemon\n")
    assert len(problems) == 1 and "does NOT resolve either" in problems[0].message


@pytest.mark.parametrize(
    "text",
    [
        f"python {DASH_M} pytest tests/unit -q",
        f"python {DASH_M} http.server 8000",
        f'git commit {DASH_M} "services.voice: fix the launch path"',
        "claude -p --permission-mode dontAsk",
        f"python {DASH_M} servicesx.voice",
    ],
    ids=["pytest", "stdlib", "commit-message", "long-flag", "near-miss-root"],
)
def test_launches_that_are_not_ours_are_skipped(tmp_path, text):
    mod = _load()
    assert _check(mod, _package(tmp_path), text + "\n") == ([], 0)


def test_opt_out_marker_skips_the_line(tmp_path):
    mod = _load()
    text = f"It used to be ``python {DASH_M} services.voice``. <!-- launch-path-ok -->\n"
    assert _check(mod, _package(tmp_path), text) == ([], 1)


def test_scan_covers_untracked_files_and_skips_ignored_binary_and_dated_records(tmp_path):
    mod = _load()
    _package(tmp_path)
    _git_repo(tmp_path, ignore="build/\n")
    flat = f"python {DASH_M} services.voice\n"
    for rel in (
        "scripts/new-launcher.sh",  # untracked but not ignored: scanned before `git add`
        "build/generated.sh",  # ignored
        "docs/superpowers/plans/2026-01-01-plan.md",  # dated plan
        "CHANGELOG.md",  # release notes quote commit subjects
        "tools/CHANGELOG.md",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(flat, encoding="utf-8")
    (tmp_path / "scripts" / "blob.bin").write_bytes(b"\0" + flat.encode())

    problems, scanned, examined = mod.scan(tmp_path)

    assert [p.path for p in problems] == ["scripts/new-launcher.sh"]
    assert examined == 1 and scanned >= 1


def test_main_reports_a_flat_compose_command_and_exits_nonzero(tmp_path):
    lint = _installed_lint(tmp_path)
    _package(tmp_path)
    _git_repo(tmp_path, ignore="scripts/\n")  # keep the lint's own docstring out of the scan
    (tmp_path / "docker-compose.yml").write_text(
        f'services:\n  bot:\n    command: ["python", "{DASH_M}", "services.voice"]\n',
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(lint)], capture_output=True, text=True, cwd=tmp_path, timeout=120
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "docker-compose.yml:3" in proc.stdout
    assert f"use {DASH_M} poindexter.services.voice, which resolves" in proc.stdout


def test_main_refuses_to_pass_a_tree_with_no_project_launch_string(tmp_path):
    """Second scan floor: a pattern that stopped matching must not read as a clean tree."""
    lint = _installed_lint(tmp_path)
    _package(tmp_path)
    _git_repo(tmp_path, ignore="scripts/\n")
    (tmp_path / "README.md").write_text(f"python {DASH_M} pytest\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(lint)], capture_output=True, text=True, cwd=tmp_path, timeout=120
    )
    assert proc.returncode != 0
    assert "examined 0" in proc.stderr


def test_lint_passes_on_the_real_tree():
    proc = subprocess.run(
        [sys.executable, str(LINT)], capture_output=True, text=True, cwd=REPO_ROOT, timeout=120
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "project launch string(s) resolve" in proc.stdout
