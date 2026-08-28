# scan-floor-exempt: the scan-floor helper itself
"""Scan-floor guard shared by the ``scripts/ci`` ratchet lints.

A lint that reports "clean" because it LOOKED AT NOTHING is not passing — it
has been silently disarmed, and it will keep reporting clean forever. That is
strictly worse than going red, because a red job is at least a signal.

Found by the poindexter#1029 follow-on audit (2026-08-28). Every lint under
``scripts/ci`` was copied into an empty tree so its hardcoded scan root
resolved to nothing. Ten of twelve printed a cheerful "clean" and exited 0;
only ``cli_audit_sink_lint`` failed loud. Three of them —
``lint_http_detail_leak``, ``lint_exception_cause`` and
``lint_secret_ciphertext_footgun`` — printed no count at all, so nothing in
the output distinguished a real scan from an empty one.

This is not a hypothetical for this repo. ``modules/content/`` arrived as a
physical code move, ``writer_core.py`` relocated out of the stages layer, and
the May 2026 cleanup wave deleted whole trees. The next rename of
``src/cofounder_agent`` or ``brain/`` would turn ten gates permanently green.

Two guards, both fail-loud:

``require_dir(root, lint=...)``
    The scan root must exist. Use when a lint has exactly one root and cannot
    do anything useful without it.

``require_scanned(count, lint=..., what=..., roots=...)``
    At least one item must actually have been examined. Use at the end of a
    lint, immediately before it declares itself clean — it is the guard that
    catches a root which exists but has been emptied, and a glob that stopped
    matching.

Both print the roots they were looking at, because "0 files scanned" is only
actionable if you can see WHERE it looked.

Usage (the ``sys.path`` line is the convention already used by
``grafana_threshold_ramp_lint.py`` for ``lib_grafana_panels``)::

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lib_scan_floor import require_dir, require_scanned

    def main() -> int:
        require_dir(SCAN_ROOT, lint="my_lint")
        ...
        require_scanned(len(examined), lint="my_lint", roots=(SCAN_ROOT,))
        print("my_lint: clean")
        return 0

``tests/unit/scripts/test_ci_lint_scan_floor.py`` executes every lint in an
empty tree and asserts a non-zero exit, so a NEW lint that forgets its floor
is caught the moment it is added. A lint that genuinely does not scan a source
tree opts out by declaring ``# scan-floor-exempt: <reason>`` in its own source
-- the exemption lives with the code it describes, not in a list in the test.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

__all__ = ["require_dir", "require_scanned", "ScanFloorError"]


class ScanFloorError(SystemExit):
    """Raised (as a SystemExit) when a lint would pass without scanning."""

    def __init__(self, message: str) -> None:
        print(message, file=sys.stderr)
        super().__init__(1)


def _render_roots(roots: Iterable[Path]) -> str:
    listed = [str(r) for r in roots]
    if not listed:
        return ""
    return "\n  looked in:\n" + "\n".join(f"    {r}" for r in listed)


def require_dir(root: Path, *, lint: str) -> Path:
    """Fail loudly when ``root`` is missing, instead of scanning nothing.

    Returns ``root`` so it can be used inline.
    """
    if not root.is_dir():
        raise ScanFloorError(
            f"{lint}: scan root does not exist: {root}\n"
            "  This lint cannot pass by finding nothing in a tree it never read. "
            "If the directory legitimately moved, update the lint's root; do not "
            "let it keep reporting clean."
        )
    return root


def require_scanned(
    count: int,
    *,
    lint: str,
    what: str = "files",
    roots: Iterable[Path] = (),
) -> int:
    """Fail loudly when a lint examined zero items.

    Call immediately before declaring the tree clean. Returns ``count`` so it
    can be used inline in the success message.
    """
    if count <= 0:
        raise ScanFloorError(
            f"{lint}: examined 0 {what} — refusing to report clean.\n"
            "  A lint that scanned nothing has been disarmed, not satisfied. "
            "Check whether the tree moved or a glob stopped matching." + _render_roots(roots)
        )
    return count
