"""Brain image packaging guard.

Captured 2026-07-02: ``data_freshness_probe.py`` shipped (PR #2043) with
its ``_HAS_*`` availability entry, but ``brain/Dockerfile``'s explicit
``COPY`` file list wasn't updated — the rebuilt image booted with
``_HAS_DATA_FRESHNESS_PROBE=False`` and the runtime PACKAGING REGRESSION
guard fired. The runtime guard is the safety net; this test moves the
same check to PR time: every ``brain/<file>.py`` referenced in
``brain_daemon.py``'s availability table must appear in the Dockerfile's
COPY line.
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


def _availability_table_files() -> list[str]:
    """brain/<file>.py paths from brain_daemon.py's availability table."""
    src = (REPO_ROOT / "brain" / "brain_daemon.py").read_text(encoding="utf-8")
    files = re.findall(r'\("_HAS_[A-Z_]+",\s*"brain/([a-z0-9_]+\.py)"', src)
    assert files, "availability table not found — did its shape change?"
    return files


def _dockerfile_copied_files() -> set[str]:
    """Filenames on the brain Dockerfile's app-code COPY line."""
    dockerfile = (REPO_ROOT / "brain" / "Dockerfile").read_text(encoding="utf-8")
    for line in dockerfile.splitlines():
        if line.startswith("COPY brain_daemon.py"):
            parts = line.removeprefix("COPY").split()
            return {p for p in parts if p.endswith(".py")}
    raise AssertionError("brain/Dockerfile app-code COPY line not found")


def test_every_availability_table_module_is_copied_into_the_image():
    copied = _dockerfile_copied_files()
    missing = [f for f in _availability_table_files() if f not in copied]
    assert not missing, (
        f"brain/Dockerfile COPY line is missing {missing} — the image will "
        f"boot with the corresponding _HAS_* flag False and the feature "
        f"silently offline (runtime guard pages, but catch it at PR time). "
        f"Add the file(s) to the COPY list in brain/Dockerfile."
    )
