#!/usr/bin/env python3
"""Is every container actually RUNNING the code that is on disk?

The deploy chain already has three safety layers and all three check **health**:
the Dockerfile COPY-closure lint (prevent), the deploy health gate with image
rollback (contain), and the container restart-loop probe (detect). None of them
checks **identity**. A container running month-old code is perfectly healthy.

That gap is not hypothetical. On 2026-09-20 a change to
``poindexter/brain/health_probes.py`` was merged, the deploy checkout was
pulled, and the brain daemon kept running the old code — the brain image is
BAKED, so a pull moves no code into it. `docker ps` said healthy, the health
gate passed, the restart-loop probe passed, and the running daemon reported
``ollama_vision_models registered: False``. It was caught by remembering, not
by a check.

Two questions, because the stack deploys two ways
-------------------------------------------------
**Baked images** (brain, sidecars) get their code at BUILD time, so the
question is: *was the image built after the files it bakes last changed?*

**Bind-mounted services** (worker, prefect-worker, pipeline-bot) read code
from the host at import time, so image age is irrelevant and the question is:
*did the process start after the code it imports last changed?*

Why the obvious version of the first check does not work
--------------------------------------------------------
Comparing image age against "did anything in the build context change" flags
**every image every time** — measured 16 of 16 on a live stack. The contexts
are ``.`` and ``./src/cofounder_agent``, so any commit anywhere in the repo
makes every image look stale. A check that fires on everything distinguishes
nothing, and this repo has already paid for that lesson once (91 bandit issues,
every one a false positive).

So the comparison is against the Dockerfile's **COPY closure** — only the files
that actually end up in the image. Reusing ``dockerfile_copy_closure_lint``'s
parser rather than writing a second one, because two parsers of the same
Dockerfiles would eventually disagree and the disagreement would be silent.

A service that is BOTH built and bind-mounted over its own code (the worker)
is checked for **dependency** staleness only — its Python comes from the mount,
so flagging its image because a ``.py`` changed would be the false positive
above wearing a different hat.

Exit codes: 0 all current · 1 something is stale · 2 could not check.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
from dockerfile_copy_closure_lint import collect_images  # noqa: E402

TOOL = "verify-deploy-identity"
COMPOSE_FILES = ("docker-compose.local.yml", "docker-compose.consumer.yml")
# Files whose change means the IMAGE is stale even when code is bind-mounted.
#
# `pyproject.toml` is deliberately NOT here. release-please rewrites its
# `version = ` line on every release, which made this tool report every
# bind-mounted service stale after every release — measured on 0.143.0, where
# the sole diff was `-version = "0.142.0" / +version = "0.143.0"`. A tool that
# cries stale on every release gets muted, and then it is worth nothing.
# `poetry.lock` is the accurate signal: it changes if and only if the RESOLVED
# dependency set changes, which is what the image actually bakes.
DEP_MANIFESTS = ("poetry.lock", "requirements.txt", "Dockerfile")
# Above this many files in a COPY closure, `git log -- <files>` stops being a
# sensible call and the closure is effectively "the whole tree" anyway.
CLOSURE_FILE_CAP = 400


def _image_code_dests(repo: Path, dockerfile: Path | None) -> list[str]:
    """Container paths this image COPYs code to (e.g. ``/app``)."""
    if dockerfile is None or not dockerfile.is_file():
        return []
    dests: set[str] = set()
    for img in collect_images(repo):
        if img.dockerfile != dockerfile:
            continue
        for host_file, image_path in img.files.items():
            if host_file.suffix == ".py":
                dests.add(str(Path(image_path).parent))
    return sorted(dests)


def _container_code_mounts(container: str, copy_dests: list[str]) -> list[Path]:
    """Bind mounts whose DESTINATION covers a path the image copies code to.

    Read from the running container rather than compose, because compose spells
    mounts several ways (``./x:``, ``${VAR}:``, absolute) and missing one makes
    a bind-mounted service look baked.
    """
    if not copy_dests:
        return []
    raw = sh("docker", "inspect", container, "--format", "{{json .Mounts}}")
    try:
        mounts = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    out: list[Path] = []
    for m in mounts:
        if m.get("Type") != "bind":
            continue
        dest = str(m.get("Destination") or "")
        if any(dest == d or d.startswith(dest.rstrip("/") + "/") for d in copy_dests):
            out.append(Path(str(m.get("Source") or "")))
    return out


def _install_manifests(files: list[Path], dockerfile: Path, repo: Path) -> list[Path]:
    """Dependency manifests this image actually installs from.

    Scoped to the Dockerfile's own directory or the build-context root. A
    `COPY . .` image bakes EVERY lock file in the tree, so an unscoped match
    let the brain's `poetry.lock` bump flag the worker — which installs from a
    different lock entirely.
    """
    anchors = {dockerfile.parent}
    for img in collect_images(repo):
        if img.dockerfile == dockerfile:
            anchors.add(img.context)
    return [f for f in files if f.name in DEP_MANIFESTS and f.parent in anchors]


@dataclass
class Service:
    name: str
    container: str
    dockerfile: Path | None
    bind_sources: list[Path]


def _instant(stamp: str) -> datetime | None:
    """Parse a docker/git ISO-8601 timestamp into an aware datetime.

    The two sources disagree about zone, and a string compare cannot see it:
    `docker inspect` emits UTC (`...Z`) while `git %cI` emits the committer's
    offset (`-04:00`). The first cut truncated both to 19 characters — which
    DROPS the offset — and compared the remainders, so a commit made at 08:36
    EDT (12:36 UTC) read as "08:36" against a UTC image time. A four-hour
    error, in the direction that makes an image look newer than it is and so
    hides real staleness.

    Returns None when the stamp is missing or carries no zone; the caller then
    declines to judge rather than guessing.
    """
    if not stamp:
        return None
    text = stamp.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # docker emits nanoseconds; fromisoformat takes at most microseconds.
    match = re.match(r"^(.*\.\d{1,6})\d*([+-]\d{2}:\d{2})?$", text)
    if match:
        text = match.group(1) + (match.group(2) or "")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def sh(*args: str) -> str:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def parse_services(repo: Path) -> list[Service]:
    """Compose services that name a container. Regex, matching the sibling lint."""
    services: list[Service] = []
    for fname in COMPOSE_FILES:
        path = repo / fname
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        headers = list(re.finditer(r"^  ([A-Za-z0-9][A-Za-z0-9._-]*):\s*$", text, re.M))
        for i, m in enumerate(headers):
            end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
            body = text[m.end() : end]
            cname = re.search(r"^\s*container_name:\s*(\S+)", body, re.M)
            if not cname:
                continue
            ctx = re.search(r"^\s*context:\s*(\S+)", body, re.M)
            dfm = re.search(r"^\s*dockerfile:\s*(\S+)", body, re.M)
            short = re.search(r"^\s*build:\s*(\S+)\s*$", body, re.M)
            dockerfile: Path | None = None
            if ctx:
                # `dockerfile:` is relative to the BUILD CONTEXT, always —
                # including when it contains a slash. Resolving a slashed path
                # against the repo root instead pointed at a file that does not
                # exist, so `is_file()` was False and the image check was
                # skipped in SILENCE. That hid brain-daemon — the one service
                # this tool was written for — on its first real use: it reported
                # 45/48 current while the brain image predated the very commit
                # it was supposed to contain.
                base = (repo / ctx.group(1)).resolve()
                dockerfile = (base / (dfm.group(1) if dfm else "Dockerfile")).resolve()
            elif short:
                dockerfile = (repo / short.group(1) / "Dockerfile").resolve()
            binds = [(repo / v).resolve() for v in re.findall(r"^\s*-\s*\./([^:\s]+):", body, re.M)]
            # Dedup by CONTAINER, not service name: the same container is
            # declared in both compose files, and comparing a service name
            # against a container name never matches — which silently double-
            # counted every shared service and inflated the total.
            if any(s.container == cname.group(1) for s in services):
                continue
            services.append(
                Service(m.group(1), cname.group(1), dockerfile, [b for b in binds if b.is_dir()])
            )
    return services


def closure_for(repo: Path, dockerfile: Path) -> list[Path]:
    for img in collect_images(repo):
        if img.dockerfile == dockerfile:
            return sorted(img.files)
    return []


def last_change(repo: Path, paths: list[Path]) -> str:
    """Newest commit time across ``paths``. Empty string when git can't say."""
    if not paths:
        return ""
    rel = []
    for p in paths:
        try:
            rel.append(str(p.resolve().relative_to(repo)))
        except ValueError:
            continue
    if not rel:
        return ""
    return sh("git", "-C", str(repo), "log", "-1", "--format=%cI", "--", *rel)


def check(repo: Path, svc: Service) -> dict:
    state = sh("docker", "inspect", svc.container, "--format", "{{.State.Running}}")
    if state != "true":
        return {"service": svc.name, "container": svc.container, "status": "not-running"}

    result = {"service": svc.name, "container": svc.container, "status": "current", "notes": []}

    # --- bind-mounted code: did the PROCESS start after the code changed? ---
    started = sh("docker", "inspect", svc.container, "--format", "{{.State.StartedAt}}")[:19]
    # A mount only counts as CODE when its container destination covers where
    # this image copies code to. Treating any bind mount as code was wrong in
    # both directions: brain mounts `infrastructure/prometheus/secrets`, which
    # made the tool check it for dependency staleness only — so a change to
    # `poindexter/brain/health_probes.py`, the exact case this tool exists for,
    # would not have flagged it. It only flagged on an unrelated protobuf bump.
    copy_dests = _image_code_dests(repo, svc.dockerfile)
    mounted_code = [b for b in _container_code_mounts(svc.container, copy_dests) if b.is_dir()]
    if mounted_code and started:
        newer = sh(
            "find",
            *[str(b) for b in mounted_code],
            "-name",
            "*.py",
            "-newermt",
            started,
            "-print",
            "-quit",
        )
        if newer:
            result["status"] = "needs-restart"
            result["notes"].append(
                f"process started {started} but {Path(newer).name} is newer — "
                f"imports are cached, so restart {svc.container}"
            )

    # --- baked image: was it built after the files it BAKES changed? ---
    if svc.dockerfile is not None:
        if not svc.dockerfile.is_file():
            # Loud, never silent. A service that declares a build but whose
            # Dockerfile we cannot find is UNCHECKED, and an unchecked service
            # reported as current is the failure this tool exists to prevent.
            result["status"] = "unchecked"
            result["notes"].append(
                f"declares build but Dockerfile not found at {svc.dockerfile} — "
                "image staleness was NOT checked for this service"
            )
            return result

        created = _instant(sh("docker", "inspect", svc.container, "--format", "{{.Created}}"))
        files = closure_for(repo, svc.dockerfile)
        narrowed = bool(mounted_code) or len(files) > CLOSURE_FILE_CAP
        if narrowed:
            # Code arrives via the mount (or the closure is the whole tree), so
            # only a dependency/Dockerfile change can make the IMAGE stale.
            files = _install_manifests(files, svc.dockerfile, repo) + [svc.dockerfile]
            result["notes"].append("image checked for dependency staleness only")
        changed = _instant(last_change(repo, files))
        if created and changed and changed > created:
            result["status"] = "stale-image"
            result["notes"].append(
                f"image built {created:%Y-%m-%dT%H:%M:%S%z} but baked files changed "
                f"{changed:%Y-%m-%dT%H:%M:%S%z} — rebuild and recreate {svc.name}"
            )
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=None, help="deploy checkout (default: cwd's repo root)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repo = (
        Path(args.repo).resolve()
        if args.repo
        else Path(sh("git", "rev-parse", "--show-toplevel") or ".").resolve()
    )
    if not (repo / "docker-compose.local.yml").is_file():
        print(f"{TOOL}: no compose file under {repo} — wrong --repo?", file=sys.stderr)
        return 2
    if not sh("docker", "version", "--format", "{{.Server.Version}}"):
        print(f"{TOOL}: docker is not reachable; cannot verify identity", file=sys.stderr)
        return 2

    services = parse_services(repo)
    results = [check(repo, s) for s in services]
    examined = [r for r in results if r["status"] != "not-running"]

    # Scan floor: a run that inspected nothing has not verified anything.
    if not examined:
        print(
            f"{TOOL}: inspected 0 running containers out of {len(services)} compose "
            f"service(s). This cannot pass by finding nothing — is the stack up, "
            f"and is --repo the deploy checkout?",
            file=sys.stderr,
        )
        return 2

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        bad = [r for r in results if r["status"] in ("stale-image", "needs-restart", "unchecked")]
        for r in sorted(results, key=lambda r: r["service"]):
            if r["status"] == "current":
                continue
            print(f"  {r['status']:14s} {r['service']}")
            for n in r.get("notes", []):
                print(f"                 {n}")
        ok = len(examined) - len(bad)
        print(
            f"\n{TOOL}: {ok}/{len(examined)} running container(s) match the checkout"
            + (f" — {len(bad)} NEEDING ATTENTION" if bad else "")
        )
        if bad:
            print(
                "\n  A stale container is healthy and wrong: it passes the health gate,\n"
                "  the restart-loop probe and `docker ps` while running code you did\n"
                "  not deploy. Rebuild baked images through scripts/start-stack.sh\n"
                "  (plain `docker compose build` cannot interpolate the config)."
            )
    return (
        1
        if any(r["status"] in ("stale-image", "needs-restart", "unchecked") for r in results)
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
