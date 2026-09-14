#!/usr/bin/env python3
"""Post-deploy health gate with automatic rollback (deploy-checkout-sync step 6b).

Stdlib only — runs on the host under systemd with the system python3, no venv.

Two subcommands, both driven by the deploy sync:

    snapshot --services a b …       -> JSON {service: {container, image_ref, image_id}}
        Taken BEFORE an image rebuild: the running container's image id is
        the rollback target.

    verify --snapshot pre.json --services a b … [--rollback] [--timeout N]
        Taken AFTER compose-apply. Polls each service's (new) container until
        it is healthy, or until it shows a definitive failure signal:
        ``restarting``, ``exited``/``dead``, health ``unhealthy``, or a
        ``RestartCount`` of 2+ on a container that was just created. On
        failure with ``--rollback`` and a snapshot image id, the previous
        image is re-tagged over the compose image ref and the service is
        recreated onto it; a critical alert_events row carries the
        container's last log lines either way.

Why this exists (2026-09-13): the deploy sync rebuilt the chatterbox image on
a merged change, recreated the container, logged "Pipeline now running …",
and never looked back. The container died on import and restarted 507 times
over eight hours. A deploy that rebuilds an image and walks away has not
deployed anything; it has placed a bet.

Exit codes: 0 = every gated service healthy; 2 = at least one service rolled
back; 1 = at least one service failed and could not be rolled back (or timed
out without a definitive verdict). The caller records the marker on 0 and 2
(the pass is handled) and withholds it on 1 only for genuine gate errors.

Settings (app_settings, read via psql; defaults apply when unreadable):
    deploy_health_gate_seconds          how long to wait for healthy (300)
    deploy_health_gate_settle_seconds   no-healthcheck services must run this long (30)
    deploy_rollback_on_unhealthy        'true' to roll back, 'false' to page only
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

DEFAULT_TIMEOUT = 300
DEFAULT_SETTLE = 30
POLL_SECONDS = 5
LOG_TAIL = 20
PG_CONTAINER = "poindexter-postgres-local"

Runner = Callable[[list[str]], tuple[int, str, str]]


def _run(argv: list[str], timeout: int = 120) -> tuple[int, str, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)  # noqa: S603
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(argv[:3])}: timed out after {timeout}s"


# ---------------------------------------------------------------------------
# settings (DB-first; psql through the postgres container like the heartbeat)
# ---------------------------------------------------------------------------

def _psql(sql: str, run: Runner = _run, **variables: str) -> tuple[int, str, str]:
    """Run one statement through the postgres container's psql.

    Values travel as psql variables (``-v name=value``) and are interpolated
    with ``:'name'``, which psql quotes as a proper SQL literal — so no value
    is ever spliced into SQL text here, and the JSON payloads' quotes and
    dollar signs cannot break out of their literal.
    """
    argv = ["docker", "exec", PG_CONTAINER, "psql", "-U", "poindexter", "-d", "poindexter_brain", "-tA"]
    for name, value in variables.items():
        argv += ["-v", f"{name}={value}"]
    argv += ["-c", sql]
    return run(argv)


def read_setting(key: str, default: str, run: Runner = _run) -> str:
    rc, out, _ = _psql("SELECT value FROM app_settings WHERE key = :'k'", run, k=key)
    val = out.strip() if rc == 0 else ""
    return val or default


def read_int_setting(key: str, default: int, run: Runner = _run) -> int:
    try:
        return int(read_setting(key, str(default), run))
    except ValueError:
        return default


def read_bool_setting(key: str, default: bool, run: Runner = _run) -> bool:
    val = read_setting(key, "true" if default else "false", run).strip().lower()
    return val in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# docker
# ---------------------------------------------------------------------------

CONTAINER_PREFIX = "container:"  # a unit named "container:<name>" is verified by container name, never rolled back


def find_container(service: str, run: Runner = _run) -> str | None:
    """The container compose created for ``service`` (label-based, name-agnostic).

    A unit spelled ``container:<name>`` (the deploy's bounce-restarted bind-mount
    containers, addressed by name) resolves to that name directly.
    """
    if service.startswith(CONTAINER_PREFIX):
        return service[len(CONTAINER_PREFIX):] or None
    rc, out, _ = run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.service={service}",
                      "--format", "{{.Names}}"])
    names = [n.strip() for n in out.splitlines() if n.strip()] if rc == 0 else []
    return names[0] if names else None


def inspect(container: str, run: Runner = _run) -> dict[str, Any] | None:
    rc, out, _ = run(["docker", "inspect", container])
    if rc != 0:
        return None
    try:
        body = json.loads(out or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(body, list) or not body or not isinstance(body[0], dict):
        return None
    c = body[0]
    state = c.get("State") or {}
    return {
        "status": str(state.get("Status") or ""),
        "restarting": bool(state.get("Restarting")),
        "restart_count": int(c.get("RestartCount") or 0),
        "health": (state.get("Health") or {}).get("Status"),
        "has_healthcheck": bool((c.get("Config") or {}).get("Healthcheck")),
        "started_at": state.get("StartedAt"),
        "exit_code": state.get("ExitCode"),
        "image_ref": str((c.get("Config") or {}).get("Image") or ""),
        "image_id": str(c.get("Image") or ""),
    }


def log_tail(container: str, run: Runner = _run, lines: int = LOG_TAIL) -> str:
    rc, out, err = run(["docker", "logs", "--tail", str(lines), container])
    text = "\n".join(line.rstrip() for line in (out + err).splitlines() if line.strip())
    return text[-2500:] if text else "(no log output)"


def snapshot(services: list[str], run: Runner = _run) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for svc in services:
        container = find_container(svc, run)
        info = inspect(container, run) if container else None
        out[svc] = {
            "container": container or "",
            "image_ref": (info or {}).get("image_ref", ""),
            "image_id": (info or {}).get("image_id", ""),
        }
    return out


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------

def verdict(info: dict[str, Any] | None, *, running_for: float, settle: int) -> str:
    """``healthy`` | ``failed:<why>`` | ``pending``."""
    if info is None:
        return "pending"
    if info["restarting"]:
        return f"failed:restarting (exit {info.get('exit_code')})"
    if info["status"] in ("exited", "dead"):
        return f"failed:{info['status']} (exit {info.get('exit_code')})"
    if info["health"] == "unhealthy":
        return "failed:unhealthy"
    if info["restart_count"] >= 2:
        return f"failed:restarted {info['restart_count']}x since recreate"
    if info["has_healthcheck"]:
        return "healthy" if info["health"] == "healthy" else "pending"
    if info["status"] == "running" and running_for >= settle and info["restart_count"] == 0:
        return "healthy"
    return "pending"


def wait_for(
    service: str, *, timeout: int, settle: int, run: Runner = _run,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, str | None, dict[str, Any] | None]:
    """Poll one service until healthy/failed/timeout. Returns (verdict, container, last info)."""
    start = clock()
    first_running: float | None = None
    container: str | None = None
    info: dict[str, Any] | None = None
    while True:
        container = container or find_container(service, run)
        info = inspect(container, run) if container else None
        now = clock()
        if info and info["status"] == "running":
            first_running = first_running if first_running is not None else now
        else:
            first_running = None
        v = verdict(info, running_for=(now - first_running) if first_running is not None else 0.0, settle=settle)
        if v != "pending":
            return v, container, info
        if now - start >= timeout:
            return "timeout", container, info
        sleep(POLL_SECONDS)


def rollback(service: str, snap: dict[str, str], stack_cmd: list[str], run: Runner = _run) -> tuple[bool, str]:
    """Re-tag the previous image over the compose ref and recreate the service onto it."""
    image_id, image_ref = snap.get("image_id", ""), snap.get("image_ref", "")
    if not image_id or not image_ref:
        return False, "no previous image recorded in the snapshot"
    rc, _, err = run(["docker", "tag", image_id, image_ref])
    if rc != 0:
        return False, f"docker tag failed: {err.strip()[:200]}"
    rc, _, err = run([*stack_cmd, "up", "-d", "--no-build", "--force-recreate", service])
    if rc != 0:
        return False, f"recreate failed: {err.strip()[:200]}"
    return True, f"re-tagged {image_ref} -> {image_id[:19]} and recreated"


def write_alert(*, service: str, sha: str, severity: str, title: str, body: str, run: Runner = _run) -> None:
    labels = json.dumps({"probe": "deploy_health_gate", "service": service, "sha": sha})
    annotations = json.dumps({"summary": title, "description": body})
    _psql(
        "INSERT INTO alert_events (alertname, status, severity, category, labels, annotations, fingerprint) "
        "VALUES ('deploy_health_gate', 'firing', :'sev', 'infrastructure', :'labels'::jsonb, :'ann'::jsonb, :'fp')",
        run,
        sev=severity, labels=labels, ann=annotations, fp=f"deploy_health_gate:{service}:{sha}",
    )


def verify(
    services: list[str], snap: dict[str, dict[str, str]], *, sha: str, timeout: int, settle: int,
    do_rollback: bool, stack_cmd: list[str], run: Runner = _run,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for svc in services:
        v, container, info = wait_for(svc, timeout=timeout, settle=settle, run=run, clock=clock, sleep=sleep)
        entry: dict[str, Any] = {"verdict": v, "container": container or "", "rolled_back": False}
        if v == "healthy":
            results[svc] = entry
            continue
        tail = log_tail(container, run) if container else "(container not found)"
        if v.startswith("failed") and do_rollback and not svc.startswith(CONTAINER_PREFIX):
            ok, note = rollback(svc, snap.get(svc, {}), stack_cmd, run)
            entry["rolled_back"] = ok
            entry["rollback_note"] = note
            if ok:
                v2, _, _ = wait_for(svc, timeout=min(timeout, 120), settle=settle, run=run, clock=clock, sleep=sleep)
                entry["after_rollback"] = v2
            write_alert(
                service=svc, sha=sha, severity="critical",
                title=f"deploy {sha[:9]}: {svc} {v}; " + ("rolled back to the previous image" if ok else f"rollback FAILED ({note})"),
                body=(
                    f"The deploy sync rebuilt and recreated `{svc}` at {sha[:9]} and the new container failed its "
                    f"health gate ({v}). " + (f"Rolled back: {note}. The fix must merge as a new commit; this sha will not be "
                    f"rebuilt again for this service." if ok else f"Rollback failed: {note}. The service is DOWN.")
                    + f"\n\nLast {LOG_TAIL} log lines of the failed container:\n```\n{tail}\n```"
                ), run=run,
            )
        else:
            severity = "critical" if v.startswith("failed") else "warning"
            write_alert(
                service=svc, sha=sha, severity=severity,
                title=f"deploy {sha[:9]}: {svc} {v}" + ("" if v.startswith("failed") else " (no verdict within the gate window)"),
                body=(
                    f"`{svc}` did not come up healthy after the deploy sync at {sha[:9]}: {v}. "
                    + ("Rollback is disabled (deploy_rollback_on_unhealthy=false) or this is a bind-mount service; "
                       "the fix is a code revert or a pinned deploy clone." if v.startswith("failed")
                       else "It may still be starting; if the next cycle does not clear this, treat it as down.")
                    + f"\n\nLast {LOG_TAIL} log lines:\n```\n{tail}\n```"
                ), run=run,
            )
        results[svc] = entry
    return results


def exit_code(results: dict[str, Any]) -> int:
    if any(r.get("rolled_back") for r in results.values()):
        return 2
    if any(r["verdict"] != "healthy" for r in results.values()):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--services", nargs="+", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--services", nargs="+", required=True)
    v.add_argument("--snapshot", default="")
    v.add_argument("--sha", default="unknown")
    v.add_argument("--rollback", action="store_true")
    v.add_argument("--no-rollback", action="store_true")
    v.add_argument("--timeout", type=int, default=None)
    v.add_argument("--settle", type=int, default=None)
    v.add_argument("--stack-cmd", default="", help="command prefix that runs docker compose for the stack")
    args = ap.parse_args(argv)
    if args.cmd == "snapshot":
        print(json.dumps(snapshot(args.services)))
        return 0
    snap: dict[str, dict[str, str]] = {}
    if args.snapshot and os.path.isfile(args.snapshot):
        with open(args.snapshot, encoding="utf-8") as fh:
            snap = json.load(fh)
    timeout = args.timeout if args.timeout is not None else read_int_setting("deploy_health_gate_seconds", DEFAULT_TIMEOUT)
    settle = args.settle if args.settle is not None else read_int_setting("deploy_health_gate_settle_seconds", DEFAULT_SETTLE)
    do_rollback = (not args.no_rollback) and (args.rollback or read_bool_setting("deploy_rollback_on_unhealthy", True))
    stack_cmd = args.stack_cmd.split() if args.stack_cmd else ["docker", "compose"]
    results = verify(args.services, snap, sha=args.sha, timeout=timeout, settle=settle,
                     do_rollback=do_rollback, stack_cmd=stack_cmd)
    print(json.dumps(results))
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
