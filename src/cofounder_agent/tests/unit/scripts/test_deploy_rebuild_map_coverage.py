"""Every image-baked service must have a REBUILD_MAP entry (deploy-sync).

`deploy-checkout-sync.sh` applies compose with `--no-build`, so a service whose
Dockerfile COPYs source is only updated when `REBUILD_MAP` names it. A missing
entry means a merged change is live in the repo and **dead in the container**,
with nothing saying so.

Verified 2026-08-31, and it was not hypothetical:

- `poindexter-auto-embed` was running stale `services/` code — it bakes a
  hand-picked subset of the backend tree and had no entry at all.
- The image-gen in-flight guard (poindexter#1024) sat merged-but-inert until
  the image was rebuilt by hand; `/app/server.py` had zero occurrences of the
  new code while the repo had it.

The expected set is DERIVED from the Dockerfiles rather than listed here, so a
newly-added sidecar fails this test the moment it appears — the point is that
the gap cannot be re-opened by omission, which is exactly how it opened.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = next(
    p for p in Path(__file__).resolve().parents
    if (p / "scripts" / "linux" / "deploy-checkout-sync.sh").exists()
)
SYNC = REPO / "scripts" / "linux" / "deploy-checkout-sync.sh"
COMPOSE = REPO / "docker-compose.local.yml"

# Services that legitimately need no entry: they COPY nothing from this repo
# (voice-agent pulls node from a donor stage; voice-bot is not in the local
# compose), so no repo change can make them stale.
_NO_REPO_SOURCE = {"voice-agent-livekit", "voice-agent-claude-code"}


def _rebuild_map() -> dict[str, str]:
    """Parse the bash associative array into {regex: services}."""
    text = SYNC.read_text(encoding="utf-8")
    block = text.split("declare -A REBUILD_MAP=(", 1)[1].split("\n)", 1)[0]
    out = {}
    for m in re.finditer(r"^\s*\['([^']+)'\]=\"([^\"]+)\"", block, re.M):
        out[m.group(1)] = m.group(2)
    return out


def _built_services() -> dict[str, tuple[str, str]]:
    """{compose service: (build context, dockerfile)} for services with build:.

    Boundaries come from the 2-space service keys rather than a fixed window —
    a window bleeds into the next service and mis-attributes its Dockerfile
    (the first draft credited chatterbox's to `speaches`).
    """
    text = COMPOSE.read_text(encoding="utf-8")
    marks = [(m.group(1), m.start()) for m in
             re.finditer(r"^  ([a-z0-9][a-z0-9._-]*):\s*$", text, re.M)]
    out = {}
    for i, (name, start) in enumerate(marks):
        body = text[start:marks[i + 1][1] if i + 1 < len(marks) else len(text)]
        if not re.search(r"^\s*build:", body, re.M):
            continue
        ctx = re.search(r"^\s*context:\s*(\S+)", body, re.M)
        df = re.search(r"^\s*dockerfile:\s*(\S+)", body, re.M)
        if df:
            out[name] = ((ctx.group(1) if ctx else ".").lstrip("./"), df.group(1))
    return out


def _copied_paths(context: str, dockerfile: str) -> list[str]:
    """Repo-relative sources a Dockerfile COPYs, resolved against its CONTEXT.

    The context is read from compose, never assumed: `Dockerfile.backup` sits
    in scripts/ but builds from the repo root, so prefixing "scripts/" invented
    `scripts/scripts/backup/run.sh`.
    """
    path = REPO / dockerfile if "/" in dockerfile else REPO / "scripts" / dockerfile
    if not path.exists():
        return []
    prefix = f"{context}/" if context else ""
    paths = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("COPY") or "--from=" in line:
            continue  # multi-stage donor is not repo source
        parts = [p for p in line.split() if not p.startswith("--")][1:]
        for srcpath in parts[:-1]:
            paths.append(f"{prefix}{srcpath}".replace("//", "/"))
    return paths


@pytest.mark.unit
def test_there_are_baked_services_to_check():
    """Guard the guard — an empty derivation would vacuously pass."""
    built = _built_services()
    assert len(built) >= 8, f"only found {len(built)} built services; parser broke"
    assert _rebuild_map(), "REBUILD_MAP parsed empty"


@pytest.mark.unit
def test_every_baked_service_has_a_rebuild_entry():
    """The regression: a service that COPYs source but is never rebuilt runs
    stale forever, because compose-apply uses --no-build."""
    rmap = _rebuild_map()
    covered = {svc for services in rmap.values() for svc in services.split()}
    missing = []
    for service, (ctx, df) in _built_services().items():
        if service in _NO_REPO_SOURCE:
            continue
        copied = _copied_paths(ctx, df)
        if not copied:
            continue  # bakes nothing from this repo
        if service not in covered:
            missing.append(f"{service} (bakes {', '.join(copied[:3])})")
    assert not missing, (
        "image-baked service(s) with no REBUILD_MAP entry — a merged change to "
        "their source would be live in the repo and DEAD in the container:\n  "
        + "\n  ".join(missing)
    )


@pytest.mark.unit
def test_each_baked_path_is_actually_matched_by_its_regex():
    """An entry naming the service is not enough — its regex must match the
    paths the Dockerfile actually COPYs, or the rebuild never triggers."""
    rmap = {re.compile(k): v.split() for k, v in _rebuild_map().items()}
    unmatched = []
    for service, (ctx, df) in _built_services().items():
        if service in _NO_REPO_SOURCE:
            continue
        for copied in _copied_paths(ctx, df):
            probe = copied.rstrip("/") + ("/x" if (REPO / copied).is_dir() else "")
            if not any(service in svcs and rx.search(probe) for rx, svcs in rmap.items()):
                unmatched.append(f"{service}: {copied}")
    assert not unmatched, (
        "baked source path(s) not matched by their service's REBUILD_MAP "
        "regex — the entry exists but would never fire:\n  " + "\n  ".join(unmatched)
    )
