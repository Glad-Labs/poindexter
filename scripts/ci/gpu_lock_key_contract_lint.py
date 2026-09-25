#!/usr/bin/env python3
"""Fail a net-new hardcoded copy of the GPU advisory-lock key or holder tag.

Why
===

``pg_advisory_lock(7_777_777_777)`` is not a scheduler private — it is a
**cross-component contract**, and three of its parties live in a different
Python tree that cannot import the first:

- ``services/gpu_scheduler.py`` — defines it, acquires/releases it.
- ``poindexter/brain/health_probes.py`` — **takes** it (``pg_try_advisory_lock``) so the
  writer-model probe never loads the ~19 GB writer into VRAM mid-render.
- ``poindexter/brain/sidecar_ram_watch.py`` — **reads** it as an idle gate before
  recycling a model sidecar.
- ``poindexter/brain/ollama_runner_ram_watch.py`` — **reads** it, and the derived
  key of the judge's own card, as an idle gate before recycling the judge's runner.

The brain runs stdlib + asyncpg only, so those three duplicate the value BY HAND.
That is deliberate and documented — but it means the contract is held together
by agreement, not by an import, and agreement rots silently. A diverged key
does not raise: the health probe simply stops seeing render sessions, and the
sidecar probe reads "GPU idle" in the middle of a render and recycles a live
model server.

The cross-tree equality is pinned by tests (``test_brain_health_probes``,
``test_sidecar_ram_watch`` and ``test_ollama_runner_ram_watch`` all assert
against the worker constant). This lint
guards the other half: that no SIXTH copy appears somewhere nothing pins.

Scope
=====

A ratchet, not an auditor. It fails on the literal appearing outside the three
sanctioned modules and their tests. Import the constant, or — if you are in the
brain tree and cannot — add the file here *and* add a test pinning it against
``services.gpu_scheduler.GPU_ADVISORY_LOCK_KEY``.

The derived DEVICE keys are the same contract, one step further
====================================================================

Device scoping (2026-08-31) made the lock a set: a caller pinned to a card
takes the base key shared plus ``GPU_ADVISORY_LOCK_KEY + 1 + crc32("<node>:<card>")``
for its card. A reader that only knows the base key reads "any GPU work
anywhere" — which is how a GPU-0 render deferred the GPU-1 judge's RAM recycle
for 2.5 h on 2026-09-25. ``brain/ollama_runner_ram_watch.py`` now re-derives the
card keys by hand, pinned to ``gpu_scheduler.device_lock_key`` by its tests. So
the derivation is ratcheted like the literal: a copy anywhere else fails here,
and the fix is the same (import it, or sanction it AND pin it with a test).

The holder TAG is the same contract, one layer up
============================================================

``application_name`` is stamped as ``poindexter-gpu:<owner>:<phase>[:<task>]:pid<N>``
by the process holding the lock, and parsed back by
``gpu_scheduler.parse_holder_tag_fields`` so any other process can name the
holder. The brain writes that shape by hand for the same reason it duplicates
the key, and it fails the same way: nothing raises, the holder just reads as
"an untagged session" again — which is the exact symptom the tag was added to
cure. So the prefix is ratcheted alongside the key, and each sanctioned copy
must be round-tripped against the real parser by a test, not compared to a
literal (a literal-vs-literal assertion passes happily while the trees drift).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_dir, require_scanned  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("src/cofounder_agent", "scripts", "mcp-server")  # brain is under src/ since #1046 step 2

#: Files allowed to spell the key literally. Each duplicate outside
#: gpu_scheduler exists because the brain cannot import the worker package,
#: and each is pinned to the worker constant by a test.
SANCTIONED = {
    "src/cofounder_agent/poindexter/services/gpu_scheduler.py",   # the definition
    "src/cofounder_agent/poindexter/brain/health_probes.py",                          # takes the lock
    "src/cofounder_agent/poindexter/brain/sidecar_ram_watch.py",                      # reads the lock
    "src/cofounder_agent/poindexter/brain/ollama_runner_ram_watch.py",                # reads the lock (#3441), scoped to the target's card
    "scripts/ci/gpu_lock_key_contract_lint.py",        # this file
}

#: Underscored and bare spellings of the same int64.
KEY_RE = re.compile(r"\b7_?777_?777_?777\b")

#: The device-key derivation's distinctive shape: base key + 1 + a card digest.
DERIVE_RE = re.compile(r"\bGPU_ADVISORY_LOCK_KEY\s*\+\s*1\b")

#: Files allowed to spell the derivation. Each copy outside gpu_scheduler is
#: pinned to ``gpu_scheduler.device_lock_key`` by a test.
DERIVE_SANCTIONED = {
    "src/cofounder_agent/poindexter/services/gpu_scheduler.py",       # the definition
    "src/cofounder_agent/poindexter/brain/ollama_runner_ram_watch.py",  # reads the judge's card key
    "scripts/ci/gpu_lock_key_contract_lint.py",                       # this file
}

#: The holder-tag prefix, in any quoting. Same contract, same failure mode.
TAG_RE = re.compile(r"[\"']poindexter-gpu(?:[:\"'])")

#: Files allowed to spell the holder-tag prefix literally.
TAG_SANCTIONED = {
    "src/cofounder_agent/poindexter/services/gpu_scheduler.py",  # defines + parses it
    "src/cofounder_agent/poindexter/brain/health_probes.py",     # writes it (probe locks)
    "scripts/ci/gpu_lock_key_contract_lint.py",                  # this file
}


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
    tag_offenders: list[tuple[str, int, str]] = []
    derive_offenders: list[tuple[str, int, str]] = []
    for root in roots:
        for path in root.rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" in rel or rel.startswith("tests/"):
                continue  # tests SHOULD pin the literal; that is the point
            scanned += 1
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            check_key = rel not in SANCTIONED
            check_tag = rel not in TAG_SANCTIONED
            check_derive = rel not in DERIVE_SANCTIONED
            if not (check_key or check_tag or check_derive):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if check_key and KEY_RE.search(line):
                    offenders.append((rel, n, line.strip()[:100]))
                elif check_tag and TAG_RE.search(line):
                    tag_offenders.append((rel, n, line.strip()[:100]))
                elif check_derive and DERIVE_RE.search(line):
                    derive_offenders.append((rel, n, line.strip()[:100]))

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

    if tag_offenders:
        print("\nGPU holder-tag prefix hardcoded outside the sanctioned modules:\n")
        for rel, n, line in tag_offenders:
            print(f"  {rel}:{n}: {line}")
        print(
            "\nUse services.gpu_scheduler._holder_tag / parse_holder_tag_fields. "
            "If you are in the brain tree and cannot import the worker package, "
            "add the file to TAG_SANCTIONED here AND add a test that ROUND-TRIPS "
            "your tag through parse_holder_tag_fields — a drifted shape does not "
            "raise, it just makes the holder anonymous again."
        )

    if derive_offenders:
        print("\nGPU device-key derivation copied outside the sanctioned modules:\n")
        for rel, n, line in derive_offenders:
            print(f"  {rel}:{n}: {line}")
        print(
            "\nUse services.gpu_scheduler.device_lock_key / resolve_lock_keys. If "
            "you are in the brain tree and cannot import the worker package, add "
            "the file to DERIVE_SANCTIONED here AND add a test asserting your "
            "keys equal gpu_scheduler.device_lock_key's for the same node and "
            "card — a drifted derivation reads a key no caller takes."
        )

    if offenders or tag_offenders or derive_offenders:
        return 1

    print(f"gpu_lock_key_contract_lint: clean ({scanned} python files scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
