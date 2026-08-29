#!/usr/bin/env python3
"""Fail a net-new hardcoded copy of the GPU advisory-lock key.

Why
===

``pg_advisory_lock(7_777_777_777)`` is not a scheduler private — it is a
**cross-component contract** with five parties, and two of them live in a
different Python tree that cannot import the first:

- ``services/gpu_scheduler.py`` — defines it, acquires/releases it.
- ``brain/health_probes.py`` — **takes** it (``pg_try_advisory_lock``) so the
  writer-model probe never loads the ~19 GB writer into VRAM mid-render.
- ``brain/sidecar_ram_watch.py`` — **reads** it as an idle gate before
  recycling a model sidecar.

The brain runs stdlib + asyncpg only, so those two duplicate the value BY HAND.
That is deliberate and documented — but it means the contract is held together
by agreement, not by an import, and agreement rots silently. A diverged key
does not raise: the health probe simply stops seeing render sessions, and the
sidecar probe reads "GPU idle" in the middle of a render and recycles a live
model server.

The cross-tree equality is pinned by tests (``test_brain_health_probes`` and
``test_sidecar_ram_watch`` both assert against the worker constant). This lint
guards the other half: that no SIXTH copy appears somewhere nothing pins.

Scope
=====

A ratchet, not an auditor. It fails on the literal appearing outside the three
sanctioned modules and their tests. Import the constant, or — if you are in the
brain tree and cannot — add the file here *and* add a test pinning it against
``services.gpu_scheduler.GPU_ADVISORY_LOCK_KEY``.

Planned follow-up (2026-08-28 device-scoping spec): when the key becomes a
*derived set* rather than one constant, this lint is what stops a consumer
being left behind on the old single key.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_dir, require_scanned  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("src/cofounder_agent", "brain", "scripts", "mcp-server")

#: Files allowed to spell the key literally. Each duplicate outside
#: gpu_scheduler exists because the brain cannot import the worker package,
#: and each is pinned to the worker constant by a test.
SANCTIONED = {
    "src/cofounder_agent/services/gpu_scheduler.py",   # the definition
    "brain/health_probes.py",                          # takes the lock
    "brain/sidecar_ram_watch.py",                      # reads the lock
    "brain/ollama_runner_ram_watch.py",                # reads the lock (#3441)
    "scripts/ci/gpu_lock_key_contract_lint.py",        # this file
}

#: Underscored and bare spellings of the same int64.
KEY_RE = re.compile(r"\b7_?777_?777_?777\b")


def main() -> int:
    roots = []
    for rel in SCAN_ROOTS:
        root = REPO / rel
        if root.is_dir():
            roots.append(root)
    # At least one root must exist; a rename that empties them all must go red
    # rather than report clean (poindexter#1029 — see lib_scan_floor).
    require_dir(REPO / "src/cofounder_agent", lint="gpu_lock_key_contract_lint")

    scanned = 0
    offenders: list[tuple[str, int, str]] = []
    for root in roots:
        for path in root.rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" in rel or rel.startswith("tests/"):
                continue  # tests SHOULD pin the literal; that is the point
            scanned += 1
            if rel in SANCTIONED:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if KEY_RE.search(line):
                    offenders.append((rel, n, line.strip()[:100]))

    require_scanned(
        scanned,
        lint="gpu_lock_key_contract_lint",
        what="python files",
        roots=[str(r) for r in roots],
    )

    if offenders:
        print("GPU advisory-lock key hardcoded outside the sanctioned modules:\n")
        for rel, n, line in offenders:
            print(f"  {rel}:{n}: {line}")
        print(
            "\nImport it from services.gpu_scheduler. If you are in the brain "
            "tree and cannot import the worker package, add the file to "
            "SANCTIONED here AND add a test asserting equality with "
            "services.gpu_scheduler.GPU_ADVISORY_LOCK_KEY — an unpinned "
            "duplicate diverges silently and disarms a probe."
        )
        return 1

    print(f"gpu_lock_key_contract_lint: clean ({scanned} python files scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
