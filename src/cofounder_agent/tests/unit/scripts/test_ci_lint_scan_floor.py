"""Every scripts/ci ratchet lint must FAIL when it scans nothing.

A lint that reports "clean" because it looked at nothing has been silently
disarmed, and it will keep reporting clean forever. That is strictly worse
than going red: a red job is at least a signal.

This was real, not theoretical. The poindexter#1029 follow-on audit
(2026-08-28) copied every lint under ``scripts/ci`` into an empty tree so each
one's hardcoded scan root resolved to nothing. Ten of twelve printed a
cheerful "clean" and exited 0 -- only ``cli_audit_sink_lint`` failed. Three of
them printed no count at all, so nothing in the output distinguished a real
scan from an empty one.

The trigger is ordinary refactoring, not neglect: ``modules/content/`` arrived
as a physical code move, ``writer_core.py`` relocated out of the stages layer,
and the May 2026 cleanup wave deleted whole trees. The next rename of
``src/cofounder_agent`` or ``brain/`` would have turned ten gates permanently
green with nothing in CI to say so.

This test is the ratchet on the ratchets. A NEW lint added to ``scripts/ci``
is picked up automatically. A lint that genuinely does not scan a source tree
opts out by declaring ``# scan-floor-exempt: <reason>`` in its OWN source.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[5] / "scripts" / "ci"

# Which lints are exempt is declared BY EACH LINT, in its own source, as a
# `# scan-floor-exempt: <reason>` marker rather than as a list of filenames
# here. Two reasons. The mirror strips some operator-only lints, and a shipping
# test that NAMES a stripped script trips the stripped-script guard (this test's
# first draft did exactly that, turning public-mirror-safety red). And a marker
# keeps each lint's status next to its code, so adding a lint never needs an
# edit here.
EXEMPT_MARKER = "# scan-floor-exempt:"


def _is_exempt(path: Path) -> bool:
    """True when the lint declares itself out of scope for the floor."""
    head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    return EXEMPT_MARKER in head


def _tree_scanning_lints() -> list[Path]:
    return sorted(
        p for p in CI_DIR.glob("*.py") if not p.name.startswith("test_") and not _is_exempt(p)
    )


def test_there_are_tree_scanning_lints_to_check() -> None:
    """Guard the guard: an empty parametrization would vacuously pass."""
    found = _tree_scanning_lints()
    assert len(found) >= 10, (
        f"expected the scripts/ci ratchet wall, found {len(found)}: "
        f"{[p.name for p in found]}. If lints moved, fix this path rather than "
        "letting the floor test silently cover nothing -- which is the exact "
        "bug it exists to prevent."
    )


@pytest.mark.parametrize("lint", _tree_scanning_lints(), ids=lambda p: p.name)
def test_lint_fails_when_it_scans_nothing(lint: Path, tmp_path: Path) -> None:
    """Run the lint in a tree with no source in it; it must exit non-zero.

    The empty tree is built so ``Path(__file__).resolve().parents[2]`` (the
    repo-root idiom every lint uses) lands on an empty directory, which is
    exactly what a renamed ``src/cofounder_agent`` or ``brain/`` looks like.
    """
    fake_ci = tmp_path / "scripts" / "ci"
    fake_ci.mkdir(parents=True)
    for src in CI_DIR.iterdir():
        if src.is_file() and src.suffix in {".py", ".json"}:
            shutil.copy2(src, fake_ci / src.name)

    proc = subprocess.run(
        [sys.executable, str(fake_ci / lint.name)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode != 0, (
        f"{lint.name} exited 0 with nothing to scan -- it would report 'clean' "
        f"forever if its tree were renamed.\n"
        f"stdout: {proc.stdout.strip()[:400]}\n"
        f"stderr: {proc.stderr.strip()[:400]}\n\n"
        "Fix: import require_dir / require_scanned from scripts/ci/lib_scan_floor "
        "and call it before the lint declares itself clean."
    )
