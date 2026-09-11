"""The brain image must ship the WHOLE ``poindexter.brain`` package, once.

History: the image used to COPY every ``brain/*.py`` flat into ``/app`` AND
mirror them into ``/app/brain/`` so both the bare and the ``brain.`` spellings
resolved -- two module objects per file, and two hand-maintained file lists
that drifted twice (``data_freshness_probe.py`` 2026-07-02,
``clock_skew_probe.py`` 2026-07-08: shipped on disk, absent from the image,
silently, behind try/except ImportError). Step 2 of Glad-Labs/poindexter#1046
replaced both lists with ONE directory COPY and a ``-m`` launch, so the
container imports exactly the modules the tests import.

What must hold now:

* the Dockerfile copies the package directory, not a file list;
* it also copies the two root files that make ``brain.`` an alias of
  ``poindexter.brain`` (``poindexter/__init__.py`` installs the finder);
* the entrypoint runs the daemon as a module of that package;
* no flat copy and no mirror block survives;
* nothing under the package imports a sibling by bare name any more -- a bare
  import would only resolve in a layout that no longer exists.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_HERE = Path(__file__).resolve()
for _p in _HERE.parents:
    if (_p / "src" / "cofounder_agent" / "poindexter" / "brain" / "Dockerfile").is_file():
        REPO_ROOT = _p
        break
else:  # pragma: no cover
    raise AssertionError("could not locate the repo root containing poindexter/brain/Dockerfile")

BRAIN_DIR = REPO_ROOT / "src" / "cofounder_agent" / "poindexter" / "brain"
DOCKERFILE = (BRAIN_DIR / "Dockerfile").read_text(encoding="utf-8")


def _copy_lines() -> list[str]:
    return [line.strip() for line in DOCKERFILE.splitlines() if line.startswith("COPY")]


def test_the_package_directory_is_copied_whole():
    assert any(
        re.match(r"COPY poindexter/brain/ /app/poindexter/brain/?$", line) for line in _copy_lines()
    ), f"expected `COPY poindexter/brain/ /app/poindexter/brain/`; COPY lines: {_copy_lines()}"


def test_the_alias_root_files_are_copied():
    """``from brain.x import`` inside the package resolves through the finder that
    ``poindexter/__init__.py`` installs; without these two files the container
    would fail on its first sibling import."""
    joined = " ".join(_copy_lines())
    assert "poindexter/__init__.py" in joined and "poindexter/_flat_imports.py" in joined, _copy_lines()


def test_entrypoint_runs_the_daemon_as_a_package_module():
    assert '"-m", "poindexter.brain.brain_daemon"' in DOCKERFILE, "CMD must be python -m poindexter.brain.brain_daemon"
    assert 'CMD ["python", "brain_daemon.py"]' not in DOCKERFILE


def test_no_flat_copy_or_mirror_block_survives():
    assert not re.search(r"^COPY brain_daemon\.py", DOCKERFILE, re.M), "flat file-list COPY is back"
    assert "cp /app/" not in DOCKERFILE and "mkdir -p /app/brain" not in DOCKERFILE, "the cp-mirror block is back"
    assert "COPY seed_app_settings.json /app/" not in DOCKERFILE


def test_no_module_imports_a_sibling_by_bare_name():
    """A bare ``from operator_notifier import`` resolved only when the flat copies
    sat in /app; there is no such layout now, so any surviving bare import would
    be a latent ImportError hidden behind a try/except."""
    siblings = {p.stem for p in BRAIN_DIR.glob("*.py")} | {"remediation"}
    offenders: list[str] = []
    for py in sorted(BRAIN_DIR.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".")[0] in siblings:
                    offenders.append(f"{py.relative_to(BRAIN_DIR)}:{node.lineno}: from {node.module} import ...")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in siblings:
                        offenders.append(f"{py.relative_to(BRAIN_DIR)}:{node.lineno}: import {alias.name}")
    assert not offenders, "bare sibling imports in poindexter/brain:\n  " + "\n  ".join(offenders)


def test_required_module_labels_point_into_the_package():
    """``_BRAIN_REQUIRED_MODULES`` names files for the boot audit's message; a
    stale ``brain/x.py`` label would send an operator to a directory that is now
    a one-file stub."""
    src = (BRAIN_DIR / "brain_daemon.py").read_text(encoding="utf-8")
    block = src[src.index("_BRAIN_REQUIRED_MODULES"):]
    block = block[: block.index("\n)\n")]
    labels = re.findall(r'"((?:poindexter/)?brain/[a-z_]+\.py)"', block)
    assert labels, "no module labels found in _BRAIN_REQUIRED_MODULES"
    stale = [lab for lab in labels if not lab.startswith("poindexter/brain/")]
    assert not stale, stale
    missing = [lab for lab in labels if not (REPO_ROOT / "src" / "cofounder_agent" / lab).is_file()]
    assert not missing, missing
