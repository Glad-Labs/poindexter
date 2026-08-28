#!/usr/bin/env python3
# scan-floor-exempt: single-file check on compose YAML
"""Fail a net-new bare ``./`` bind mount of repo-shipped code or config.

Why
===

A bare ``./foo`` in a compose ``volumes:`` entry resolves to the compose
**project directory**, not to ``${POINDEXTER_DEPLOY_ROOT:-.}``. On an operator
host where the deploy clone is separate from the working checkout, those
containers read from the *working checkout* — so merged changes never reach the
running stack, silently and with no error anywhere.

That bit twice on 2026-07-26 (poindexter#922 / #923):

- ``gpu-exporter`` mounted ``./scripts/nvidia-smi-exporter.py`` while its own
  sibling mount one line below was correctly anchored, so a merged fix to the
  exporter could never actually run — rebuilding the image doesn't help when a
  bind mount shadows the baked copy with stale code.
- ``grafana`` mounted ``./infrastructure/grafana/dashboards``, so dashboard JSON
  merged to main stayed dark until someone pulled the operator checkout.

``${POINDEXTER_DEPLOY_ROOT:-.}/foo`` is a no-op wherever the variable is unset
(consumer installs, CI, fresh clones), so anchoring costs nothing.

Scope
=====

Only paths under ``GUARDED_PREFIXES`` — repo-shipped code and config that must
follow the deployed tree. Runtime-WRITTEN paths are intentionally exempt and
listed in ``RUNTIME_WRITTEN_EXEMPT`` with a reason: relocating their root
changes which process owns the files, which is a real operational decision and
not a mechanical find-and-replace. Keep that list short and justified.

This is a ratchet, not an auditor: it guards the prefixes below, not every
conceivable mount.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

COMPOSE_FILES = ("docker-compose.local.yml", "docker-compose.consumer.yml")

# Repo-shipped code/config: a stale copy here means merged work doesn't run.
GUARDED_PREFIXES = (
    "./scripts",
    "./src",
    "./brain",
    "./infrastructure/grafana/dashboards",
    "./infrastructure/prometheus/config",
    "./infrastructure/prometheus/alerts",
    "./infrastructure/loki",
    "./infrastructure/promtail",
    "./infrastructure/tempo",
    "./infrastructure/pgadmin",
)

# Written at runtime, not shipped by the repo. Anchoring these changes file
# ownership across processes — see the compose comments at each site.
RUNTIME_WRITTEN_EXEMPT = {
    "./infrastructure/prometheus/secrets": (
        "written by the brain daemon; prometheus + alertmanager + brain must "
        "agree on one root (root-owned files here broke a DR backup 2026-07-23)"
    ),
    "./infrastructure/grafana/provisioning": (
        "mounted rw; alerting/ subtree is written at runtime by Grafana and "
        "the worker"
    ),
    "./infrastructure/grafana/provisioning/alerting": (
        "mounted rw; written at runtime by the worker"
    ),
}

_MOUNT_RE = re.compile(r"^\s+-\s+(\./[^:\s]+)(:.*)?$")


def violations(path: Path) -> list[tuple[int, str]]:
    """Return (line_no, source_path) for each guarded bare-./ mount."""
    found: list[tuple[int, str]] = []
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        m = _MOUNT_RE.match(line)
        if not m:
            continue
        src = m.group(1)
        if src in RUNTIME_WRITTEN_EXEMPT:
            continue
        # A compose file self-mount (brain's drift probe reads the file it was
        # started from) is inherently project-local.
        if src.lstrip("./") in COMPOSE_FILES:
            continue
        if any(
            src == p or src.startswith(p + "/") for p in GUARDED_PREFIXES
        ):
            found.append((i, src))
    return found


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    bad: list[str] = []
    checked = 0
    for name in COMPOSE_FILES:
        f = root / name
        if not f.exists():
            continue
        checked += 1
        for line_no, src in violations(f):
            bad.append(f"  {name}:{line_no}  {src}")

    if bad:
        print("compose-mount-deploy-root: FAIL — bare './' mount of repo-shipped code/config\n")
        print("\n".join(bad))
        print(
            "\nThese resolve to the compose project dir, so on an operator host "
            "with a separate\ndeploy clone the container runs code from the "
            "working checkout and merged\nchanges never deploy.\n\n"
            "Fix: '${POINDEXTER_DEPLOY_ROOT:-.}/<path>' (a no-op when the var "
            "is unset).\n"
            "If the path is genuinely written at runtime, add it to "
            "RUNTIME_WRITTEN_EXEMPT\nin this script WITH a reason, and comment "
            "the compose site."
        )
        return 1

    print(f"compose-mount-deploy-root: OK ({checked} compose file(s) checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
