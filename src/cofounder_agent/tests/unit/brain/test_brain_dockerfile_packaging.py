"""Brain image packaging guard.

Captured 2026-07-02: ``data_freshness_probe.py`` shipped (PR #2043) with
its ``_HAS_*`` availability entry, but ``brain/Dockerfile``'s explicit
``COPY`` file list wasn't updated — the rebuilt image booted with
``_HAS_DATA_FRESHNESS_PROBE=False`` and the runtime PACKAGING REGRESSION
guard fired. The runtime guard is the safety net; this test moves the
same check to PR time.

Broadened 2026-07-08 after ``clock_skew_probe.py`` (#2212) shipped missing
from BOTH the flat COPY line and the cp-mirror RUN block — silently, via
a try/except ImportError guard, and invisible to the original version of
this test because that version only checked files hand-added to
``brain_daemon.py``'s ``_BRAIN_REQUIRED_MODULES`` table (fixed in #2215).
The checks below instead use every real ``brain/<file>.py`` on disk as
ground truth, so a new module can't slip through by never being wired
into any hand-maintained list in the first place.
"""

from __future__ import annotations

import re
from pathlib import Path

# Walk up from this test file to the repo root (the dir containing brain/).
_HERE = Path(__file__).resolve()
for _p in _HERE.parents:
    if (_p / "brain" / "Dockerfile").is_file():
        REPO_ROOT = _p
        break
else:  # pragma: no cover
    raise AssertionError("could not locate repo root containing brain/Dockerfile")


def _brain_source_py_files() -> set[str]:
    """Top-level ``brain/<file>.py`` filenames that actually exist on disk.

    Ground truth for the completeness checks below — deliberately NOT
    derived from brain_daemon.py's hand-maintained availability table (a
    new module wired via a bare try/except, without ever being added to
    that table, is exactly how ``clock_skew_probe.py`` (#2212) slipped
    through). Excludes ``__init__.py`` (a package marker synthesized
    separately in the container via ``touch``) and doesn't descend into
    ``remediation/`` — that subpackage is directory-COPY'd and already
    covered by ``test_remediation_subpackage_is_copied_into_the_image``
    below.
    """
    return {p.name for p in (REPO_ROOT / "brain").glob("*.py") if p.name != "__init__.py"}


def _dockerfile_copied_files() -> set[str]:
    """Filenames on the brain Dockerfile's app-code COPY line."""
    dockerfile = (REPO_ROOT / "brain" / "Dockerfile").read_text(encoding="utf-8")
    for line in dockerfile.splitlines():
        if line.startswith("COPY brain_daemon.py"):
            parts = line.removeprefix("COPY").split()
            return {p for p in parts if p.endswith(".py")}
    raise AssertionError("brain/Dockerfile app-code COPY line not found")


def _dockerfile_mirror_copied_files() -> set[str]:
    """Filenames mirrored into ``/app/brain/`` by the cp-mirror RUN block."""
    dockerfile = (REPO_ROOT / "brain" / "Dockerfile").read_text(encoding="utf-8")
    return set(re.findall(r"cp /app/([a-z0-9_]+\.py) /app/brain/\1\b", dockerfile))


def test_every_brain_source_file_is_flat_copied_into_the_image():
    """Every real brain/<file>.py must be on the Dockerfile's flat COPY line.

    Unlike the availability-table check this replaces, this doesn't care
    whether anything ever wired the file into a ``_HAS_*`` flag — if the
    file exists in ``brain/``, the built image needs it at ``/app/``.
    """
    missing = sorted(_brain_source_py_files() - _dockerfile_copied_files())
    assert not missing, (
        f"brain/Dockerfile's flat COPY line is missing {missing} — these "
        f"file(s) exist in brain/ but won't be present at /app/ in the "
        f"built image. Add them to the `COPY brain_daemon.py ... ./` line."
    )


def test_every_brain_source_file_is_mirrored_for_package_imports():
    """Every brain/<file>.py except brain_daemon.py must also be mirrored
    into /app/brain/<file>.py so `from brain.<file> import ...` resolves.

    brain_daemon.py itself is excluded: it's the entrypoint
    (``CMD ["python", "brain_daemon.py"]``), and the only places that reach
    for ``from brain.brain_daemon import`` / ``from brain import
    brain_daemon`` (alert_sync.py, alert_dispatcher.py,
    remediation/registry.py) are guarded fallbacks that never fire in
    practice — the flat ``from brain_daemon import ...`` always resolves
    first. Mirroring it would be harmless but isn't required.
    """
    required = _brain_source_py_files() - {"brain_daemon.py"}
    missing = sorted(required - _dockerfile_mirror_copied_files())
    assert not missing, (
        f"brain/Dockerfile's cp-mirror RUN block is missing {missing} — "
        f"`from brain.<module> import ...` package-qualified imports for "
        f"these file(s) will fail inside the container, often silently if "
        f"the importer wraps it in try/except ImportError. Add a "
        f"`cp /app/<file> /app/brain/<file>` line for each in the "
        f"`RUN mkdir -p /app/brain && ...` block."
    )


def test_remediation_subpackage_is_copied_into_the_image():
    """The firefighter lives in the ``brain/remediation/`` SUBPACKAGE, not a
    flat ``.py``, so the flat COPY line above misses it. Without an explicit
    directory COPY, ``from brain.remediation.engine import ...`` in
    ``alert_dispatcher.py`` fails inside the container and the firefighter
    hooks silently degrade to no-op stubs (dead, not merely inert). Guard the
    directory COPY at PR time.
    """
    dockerfile = (REPO_ROOT / "brain" / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"COPY\s+remediation/\s+/app/brain/remediation/", dockerfile), (
        "brain/Dockerfile must `COPY remediation/ /app/brain/remediation/` so the "
        "firefighter package resolves as `brain.remediation.*` in the container "
        "(matches the `from brain.bootstrap` convention). Missing it makes the "
        "dispatcher's firefighter hooks fall back to no-ops."
    )
    # The package itself must be present on disk with its entry modules.
    pkg = REPO_ROOT / "brain" / "remediation"
    for f in ("__init__.py", "engine.py", "registry.py", "rules.py"):
        assert (pkg / f).is_file(), f"brain/remediation/{f} missing from the source tree"
