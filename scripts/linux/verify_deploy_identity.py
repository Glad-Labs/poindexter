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


@dataclass
class Service:
    name: str
    container: str
    dockerfile: Path | None
    bind_sources: list[Path]


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
                base = (repo / ctx.group(1)).resolve()
                dockerfile = (
                    (repo / dfm.group(1)).resolve()
                    if dfm and "/" in dfm.group(1)
                    else (base / (dfm.group(1) if dfm else "Dockerfile")).resolve()
                )
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
    return sh("git", "-C", str(repo), "log", "-1", "--format=%cI", "--", *rel)[:19]


def check(repo: Path, svc: Service) -> dict:
    state = sh("docker", "inspect", svc.container, "--format", "{{.State.Running}}")
    if state != "true":
        return {"service": svc.name, "container": svc.container, "status": "not-running"}

    result = {"service": svc.name, "container": svc.container, "status": "current", "notes": []}

    # --- bind-mounted code: did the PROCESS start after the code changed? ---
    started = sh("docker", "inspect", svc.container, "--format", "{{.State.StartedAt}}")[:19]
    mounted_code = [b for b in svc.bind_sources if b.is_dir()]
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
    if svc.dockerfile and svc.dockerfile.is_file():
        created = sh("docker", "inspect", svc.container, "--format", "{{.Created}}")[:19]
        files = closure_for(repo, svc.dockerfile)
        narrowed = bool(mounted_code) or len(files) > CLOSURE_FILE_CAP
        if narrowed:
            # Code arrives via the mount (or the closure is the whole tree), so
            # only a dependency/Dockerfile change can make the IMAGE stale.
            files = [f for f in files if f.name in DEP_MANIFESTS] + [svc.dockerfile]
            result["notes"].append("image checked for dependency staleness only")
        changed = last_change(repo, files)
        if created and changed and changed > created:
            result["status"] = "stale-image"
            result["notes"].append(
                f"image built {created} but baked files changed {changed} — rebuild "
                f"and recreate {svc.name}"
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
        bad = [r for r in results if r["status"] in ("stale-image", "needs-restart")]
        for r in sorted(results, key=lambda r: r["service"]):
            if r["status"] == "current":
                continue
            print(f"  {r['status']:14s} {r['service']}")
            for n in r.get("notes", []):
                print(f"                 {n}")
        ok = len(examined) - len(bad)
        print(
            f"\n{TOOL}: {ok}/{len(examined)} running container(s) match the checkout"
            + (f" — {len(bad)} STALE" if bad else "")
        )
        if bad:
            print(
                "\n  A stale container is healthy and wrong: it passes the health gate,\n"
                "  the restart-loop probe and `docker ps` while running code you did\n"
                "  not deploy. Rebuild baked images through scripts/start-stack.sh\n"
                "  (plain `docker compose build` cannot interpolate the config)."
            )
    return 1 if any(r["status"] in ("stale-image", "needs-restart") for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
