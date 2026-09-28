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

# Services that COPY nothing from this repo (voice-agent pulls node from a
# donor stage; voice-bot is not in the local compose), so no source change can
# make them stale. Their Dockerfile still can, and
# test_every_built_services_dockerfile_is_matched_by_its_regex holds them to
# that like every other built service.
_NO_REPO_SOURCE = {"voice-agent-livekit", "voice-agent-claude-code"}

# There is deliberately no exemption for profile-gated services, nor for ones
# that run another service's image. demo-recorder was exempted twice: first
# because the recreate step started every service it named (stack#4144), then
# because it runs the worker's image tag, so the worker's build refreshes it
# (stack#4153). Both are true; neither is a reason to leave it out. 6a-bis never
# names a service that is not running (pinned in
# test_deploy_checkout_sync_recreate_scope.py), so an entry costs one cached
# build of the shared tag, and every built service's Dockerfile has an entry
# naming it with no exceptions to keep honest. The same rule caught
# voice-agent-claude-code, which builds voice-agent-livekit's Dockerfile into
# the same image and was never named.


def _rebuild_map() -> dict[str, str]:
    """Parse the bash associative array into {regex: services}."""
    text = SYNC.read_text(encoding="utf-8")
    block = text.split("declare -A REBUILD_MAP=(", 1)[1].split("\n)", 1)[0]
    out = {}
    for m in re.finditer(r"^\s*\['([^']+)'\]=\"([^\"]+)\"", block, re.M):
        out[m.group(1)] = m.group(2)
    return out


def _service_stanzas() -> dict[str, str]:
    """{compose service: its stanza text}.

    Boundaries come from the 2-space service keys rather than a fixed window —
    a window bleeds into the next service and mis-attributes its Dockerfile
    (the first draft credited chatterbox's to `speaches`).
    """
    text = COMPOSE.read_text(encoding="utf-8")
    marks = [(m.group(1), m.start()) for m in
             re.finditer(r"^  ([a-z0-9][a-z0-9._-]*):\s*$", text, re.M)]
    return {
        name: text[start:marks[i + 1][1] if i + 1 < len(marks) else len(text)]
        for i, (name, start) in enumerate(marks)
    }


def _built_services() -> dict[str, tuple[str, str]]:
    """{compose service: (build context, dockerfile)} for services with build:."""
    out = {}
    for name, body in _service_stanzas().items():
        if not re.search(r"^\s*build:", body, re.M):
            continue
        ctx = re.search(r"^\s*context:\s*(\S+)", body, re.M)
        df = re.search(r"^\s*dockerfile:\s*(\S+)", body, re.M)
        if df:
            out[name] = ((ctx.group(1) if ctx else ".").lstrip("./"), df.group(1))
    return out


def _dockerfile(context: str, dockerfile: str) -> Path:
    """The Dockerfile compose actually builds: `dockerfile:` is relative to the
    build CONTEXT, not to scripts/.

    The first version resolved a slash-less name under scripts/, which only
    happened to be right for the sidecars whose context IS scripts/. For every
    service built from src/cofounder_agent (worker, prefect-worker,
    pipeline-bot, demo-recorder, brain-daemon) it named a file that does not
    exist — and a missing Dockerfile read as "bakes nothing", so all five were
    skipped by every check below while this test stayed green. pipeline-bot sat
    a week behind its poetry.lock behind exactly that skip (2026-09-28).
    """
    return REPO / context / dockerfile if context else REPO / dockerfile


def _copied_paths(context: str, dockerfile: str) -> list[str]:
    """Repo-relative sources a Dockerfile COPYs, resolved against its CONTEXT.

    The context is read from compose, never assumed: `Dockerfile.backup` sits
    in scripts/ but builds from the repo root, so prefixing "scripts/" invented
    `scripts/scripts/backup/run.sh`. A whole-context `COPY . .` comes back as
    the context itself. A Dockerfile that cannot be found is a failure, never
    an empty list — an empty list is how this test went blind.
    """
    path = _dockerfile(context, dockerfile)
    assert path.exists(), (
        f"{path.relative_to(REPO)} does not exist — compose resolves "
        f"`dockerfile: {dockerfile}` against `context: {context or '.'}`"
    )
    prefix = f"{context}/" if context else ""
    paths = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("COPY") or "--from=" in line:
            continue  # multi-stage donor is not repo source
        parts = [p for p in line.split() if not p.startswith("--")][1:]
        for srcpath in parts[:-1]:
            joined = f"{prefix}{srcpath}".replace("//", "/")
            paths.append(joined[:-2] if joined.endswith("/.") else joined)
    return paths


def _context_is_bind_mounted(service: str, context: str, dockerfile: str) -> bool:
    """True when the service mounts its own build context over the Dockerfile's
    WORKDIR — `src/cofounder_agent:/app` for the Dockerfile.worker services.

    Then the baked whole-context `COPY . .` is shadowed at runtime and a source
    edit needs only the restart deploy-sync already does. What stays baked is
    everything a build step made from explicitly COPYed files — the poetry
    dependency layer — plus the Dockerfile itself, and the checks below still
    hold those to REBUILD_MAP. Derived from compose + the Dockerfile rather than
    listed, so a service that drops its mount loses the allowance by itself.
    """
    workdirs = re.findall(r"^WORKDIR\s+(\S+)", _dockerfile(context, dockerfile)
                          .read_text(encoding="utf-8"), re.M)
    if not workdirs or not context:
        return False
    body = _service_stanzas()[service]
    mounts = re.findall(r"^\s*-\s*(\S+?):(/\S+?)(?::ro|:rw)?\s*$", body, re.M)
    return any(host.rstrip("/").endswith(f"/{context}") and target == workdirs[-1]
               for host, target in mounts)


def _baked_paths(service: str, context: str, dockerfile: str) -> list[str]:
    """What the image bakes that a repo change can make stale: every COPY
    source, minus a whole-context copy the service shadows with a bind mount."""
    shadowed = _context_is_bind_mounted(service, context, dockerfile)
    return [p for p in _copied_paths(context, dockerfile)
            if not (shadowed and p == context)]


def _checked_services() -> dict[str, tuple[str, str]]:
    return {s: v for s, v in _built_services().items() if s not in _NO_REPO_SOURCE}


def _matched(service: str, path: str, rmap: dict) -> bool:
    probe = path.rstrip("/") + ("/x" if (REPO / path).is_dir() else "")
    return any(service in svcs and rx.search(probe) for rx, svcs in rmap.items())


@pytest.mark.unit
def test_there_are_baked_services_to_check():
    """Guard the guard — an empty derivation would vacuously pass."""
    built = _built_services()
    assert len(built) >= 8, f"only found {len(built)} built services; parser broke"
    assert _rebuild_map(), "REBUILD_MAP parsed empty"


@pytest.mark.unit
def test_every_built_services_dockerfile_resolves():
    """The blind spot itself: resolve each `dockerfile:` the way compose does.
    A name that does not exist here was silently read as "bakes nothing"."""
    missing = [f"{s}: {_dockerfile(ctx, df).relative_to(REPO)}"
               for s, (ctx, df) in _built_services().items()
               if not _dockerfile(ctx, df).exists()]
    assert not missing, "Dockerfile(s) not found:\n  " + "\n  ".join(missing)


@pytest.mark.unit
def test_every_checked_service_bakes_something_from_this_repo():
    """"Bakes nothing" must be a decision recorded in _NO_REPO_SOURCE, never
    a fall-through: the fall-through is what hid five services."""
    empty = [s for s, (ctx, df) in _checked_services().items()
             if not _copied_paths(ctx, df)]
    assert not empty, (
        "service(s) whose Dockerfile COPYs nothing from this repo — list them in "
        "_NO_REPO_SOURCE if that is really so:\n  " + "\n  ".join(empty)
    )


@pytest.mark.unit
def test_the_worker_image_services_are_checked():
    """Pin the population the old resolver skipped, so a future parser change
    cannot quietly drop them from every check again."""
    checked = _checked_services()
    for service in ("worker", "prefect-worker", "pipeline-bot", "demo-recorder", "brain-daemon"):
        assert service in checked, f"{service} fell out of the checked set"
        ctx, df = checked[service]
        assert _baked_paths(service, ctx, df), f"{service} reads as baking nothing"


@pytest.mark.unit
def test_every_baked_service_has_a_rebuild_entry():
    """The regression: a service that COPYs source but is never rebuilt runs
    stale forever, because compose-apply uses --no-build."""
    rmap = _rebuild_map()
    covered = {svc for services in rmap.values() for svc in services.split()}
    missing = []
    for service, (ctx, df) in _checked_services().items():
        baked = _baked_paths(service, ctx, df)
        if service not in covered:
            missing.append(f"{service} (bakes {', '.join(baked[:3])})")
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
    unmatched = [f"{service}: {path}"
                 for service, (ctx, df) in _checked_services().items()
                 for path in _baked_paths(service, ctx, df)
                 if not _matched(service, path, rmap)]
    assert not unmatched, (
        "baked source path(s) not matched by their service's REBUILD_MAP "
        "regex — the entry exists but would never fire:\n  " + "\n  ".join(unmatched)
    )


@pytest.mark.unit
def test_every_built_services_dockerfile_is_matched_by_its_regex():
    """Editing the Dockerfile changes the image as surely as editing what it
    COPYs. The worker-image entry named scripts/Dockerfile.worker — a path that
    does not exist — so a Dockerfile.worker edit rebuilt none of its services.

    Every built service, not only the checked ones: for a _NO_REPO_SOURCE
    service the Dockerfile is its ONLY repo input, and voice-agent-claude-code
    builds the same Dockerfile as voice-agent-livekit but was never named."""
    rmap = {re.compile(k): v.split() for k, v in _rebuild_map().items()}
    unmatched = []
    for service, (ctx, df) in _built_services().items():
        rel = str(_dockerfile(ctx, df).relative_to(REPO))
        if not _matched(service, rel, rmap):
            unmatched.append(f"{service}: {rel}")
    assert not unmatched, (
        "service Dockerfile(s) not matched by a REBUILD_MAP entry naming the "
        "service:\n  " + "\n  ".join(unmatched)
    )
