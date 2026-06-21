#!/usr/bin/env python3
"""Poindexter Recovery Agent — host-side HTTP endpoint for container-initiated
service recovery.

Runs on the host at ``0.0.0.0:9841`` (bound to all interfaces so Docker
containers can reach it via ``host.docker.internal`` even from inside the
Docker network). Accepts authenticated ``POST /recover`` requests and runs
recovery actions that require host-level access — things a containerised
brain genuinely cannot do itself.

Two action kinds, dispatched by the ``service`` field of the POST body:

  - ``"mcp-http"``        → restart the MCP HTTP Scheduled Task
                            (consumer: ``brain/mcp_http_probe.py``).
  - ``"compose-reapply"`` → run ``start-stack.sh up -d --no-build``, which
                            reconciles drifted containers back to the compose
                            spec (consumer: ``brain/compose_drift_probe.py``).

Why compose-reapply lives HERE and not in the brain: a Linux brain container
running ``docker compose up`` mangles Windows ``C:\\`` bind-mount sources into
``/app/C:\\...`` (the daemon then auto-creates them as empty dirs and wipes
the service's real config). Running ``start-stack.sh`` on the host resolves
the binds correctly, so the brain DETECTS drift and delegates the ACT to this
agent.

Authentication: ``Authorization: Bearer <token>`` where the token comes from
the ``POINDEXTER_RECOVERY_TOKEN`` env var or the ``poindexter_recovery_token``
key in ``~/.poindexter/bootstrap.toml``. The same token is stored in
``app_settings`` (currently under ``mcp_http_probe_recovery_token``) so the
brain probes can read it.

Usage:
    python recovery-agent.py
"""
from __future__ import annotations

import glob
import json
import logging
import os
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("poindexter-recovery-agent")

# ---------------------------------------------------------------------------
# Action registry. Each service name maps to a recovery action:
#   {"kind": "task",    "task": "<Scheduled Task name>"}  → Start-ScheduledTask
#   {"kind": "compose"}                                   → start-stack reapply
# Add a new recoverable surface = add a row here (+ register the caller on the
# brain side). New consumers POST {"service": "<name>"}.
# ---------------------------------------------------------------------------
SERVICES: dict[str, dict[str, str]] = {
    "mcp-http": {"kind": "task", "task": "Poindexter MCP HTTP"},
    "compose-reapply": {"kind": "compose"},
}

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 9841

# A Scheduled-Task restart is fast and we wait on it. A compose reapply is
# fire-and-forget (see _compose_reapply), so it has no wait timeout here.
TASK_TIMEOUT_SECONDS = 30

# CREATE_NO_WINDOW keeps recovery subprocesses from popping a console window
# (the operator's "no popup windows" rule). 0 on POSIX where it doesn't exist.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _load_token() -> str:
    """Read the recovery token from env var or ~/.poindexter/bootstrap.toml."""
    token = os.environ.get("POINDEXTER_RECOVERY_TOKEN", "").strip()
    if token:
        return token

    bootstrap_path = Path.home() / ".poindexter" / "bootstrap.toml"
    if bootstrap_path.is_file():
        try:
            import tomllib  # Python 3.11+ stdlib

            with open(bootstrap_path, "rb") as fh:
                data = tomllib.load(fh)
            token = (data.get("poindexter_recovery_token") or "").strip()
        except Exception as exc:
            logger.error("Failed to read bootstrap.toml: %s", exc)

    return token


# ---------------------------------------------------------------------------
# Host-tool resolution (Git Bash + start-stack.sh), public-mirror-safe — no
# operator-specific repo/dir names baked in.
# ---------------------------------------------------------------------------


def _resolve_git_bash() -> str:
    """Resolve Git Bash explicitly.

    The PATH ``bash`` on Windows is usually WSL's ``bash``, which runs in a
    separate filesystem namespace and cannot see Docker or the ``C:\\`` bind
    paths the stack uses. Derive Git Bash from ``git``'s own location
    (``<GitRoot>\\cmd\\git.exe`` → ``<GitRoot>\\bin\\bash.exe``), mirroring
    ``scripts/docker-watchdog.ps1::Resolve-GitBash``, with install-path
    fallbacks. Last resort is bare ``bash`` — a failed launch surfaces loudly
    if that turns out to be WSL.
    """
    git = shutil.which("git")
    if git:
        cand = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if cand.is_file():
            return str(cand)
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Git" / "bin" / "bash.exe",
    ]
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates.append(Path(local) / "Programs" / "Git" / "bin" / "bash.exe")
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return "bash"


def _resolve_start_stack() -> str | None:
    """Locate ``start-stack.sh`` without hard-coding the repo/clone name.

    Priority:
      1. ``POINDEXTER_START_STACK`` env var (absolute path), if it exists.
      2. The auto-synced deploy clone:
         ``~/.poindexter/deploy/<anything>/scripts/start-stack.sh`` (glob, so
         the clone's directory name is never spelled out here).
      3. ``~/.poindexter/scripts/start-stack.sh`` (legacy host copy).

    Returns the path string, or ``None`` if nothing resolves (caller errors
    loudly rather than guessing).
    """
    env_path = os.environ.get("POINDEXTER_START_STACK", "").strip()
    if env_path and Path(env_path).is_file():
        return env_path

    home = Path.home()
    matches = sorted(
        glob.glob(str(home / ".poindexter" / "deploy" / "*" / "scripts" / "start-stack.sh"))
    )
    if len(matches) > 1:
        logger.warning(
            "Multiple start-stack.sh under ~/.poindexter/deploy/ (%s); using %s. "
            "Pin POINDEXTER_START_STACK to disambiguate.",
            matches, matches[0],
        )
    if matches:
        return matches[0]

    legacy = home / ".poindexter" / "scripts" / "start-stack.sh"
    if legacy.is_file():
        return str(legacy)
    return None


# ---------------------------------------------------------------------------
# Recovery actions. Each returns (ok, human-readable detail) and never raises.
# ---------------------------------------------------------------------------


def _restart_task(task_name: str) -> tuple[bool, str]:
    """Restart a host Scheduled Task via ``Start-ScheduledTask``."""
    safe_name = task_name.replace("'", "''")
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive",
                "-Command", f"Start-ScheduledTask -TaskName '{safe_name}'",
            ],
            capture_output=True,
            text=True,
            timeout=TASK_TIMEOUT_SECONDS,
            creationflags=_NO_WINDOW,
        )
    except Exception as exc:
        logger.error("Task restart subprocess error: %s", exc)
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode == 0:
        return True, f"Started task: {task_name}"
    return False, (result.stderr or result.stdout or "unknown error").strip()


def _compose_reapply() -> tuple[bool, str]:
    """Reconcile drifted containers to the compose spec via ``start-stack.sh``.

    Runs ``start-stack.sh up -d --no-build`` through Git Bash: ``up -d`` makes
    compose recreate only the services whose config-hash changed (= the
    drifted ones) and leaves healthy containers — postgres, the brain making
    this call — alone; ``--no-build`` blocks surprise image builds on what is
    meant to be a fast reconcile.

    Fire-and-forget: a reapply that recreates several services can take well
    over a minute, so we ``Popen`` it and return immediately rather than
    blocking the brain's POST past any reasonable HTTP timeout. The child
    inherits our stdout/stderr (→ recovery-agent.log for forensics). The brain
    confirms whether drift actually cleared on its next 5-min probe cycle, and
    its rolling cap escalates if a failing reapply leaves the drift in place.
    """
    start_stack = _resolve_start_stack()
    if not start_stack:
        return False, (
            "start-stack.sh not found — set POINDEXTER_START_STACK or ensure "
            "~/.poindexter/deploy/<clone>/scripts/start-stack.sh exists"
        )
    bash = _resolve_git_bash()
    try:
        subprocess.Popen(  # noqa: S603 — fixed argv, resolved paths
            [bash, start_stack, "up", "-d", "--no-build"],
            creationflags=_NO_WINDOW,
            close_fds=True,
        )
    except Exception as exc:
        logger.error("compose reapply spawn error: %s", exc)
        return False, f"{type(exc).__name__}: {exc}"
    return True, f"compose reapply dispatched via {Path(start_stack).name}"


def dispatch_recovery(
    service: str,
    *,
    task_fn=_restart_task,
    compose_fn=_compose_reapply,
) -> tuple[int, dict]:
    """Pure dispatch from a service name to an action. Returns (http_status, body).

    The action runners are injectable so this is unit-testable without a real
    socket, Scheduled Task, or docker daemon.
    """
    spec = SERVICES.get(service)
    if spec is None:
        return 400, {"ok": False, "error": f"unknown service: {service!r}"}

    kind = spec.get("kind")
    if kind == "task":
        ok, detail = task_fn(spec["task"])
    elif kind == "compose":
        ok, detail = compose_fn()
    else:  # registry typo — fail loud, don't silently 200
        return 400, {"ok": False, "error": f"unknown action kind: {kind!r}"}

    logger.info("Recovery %s service=%r → %s", "ok" if ok else "FAILED", service, detail)
    return (200 if ok else 500), {"ok": ok, "service": service, "detail": detail}


def authorized(auth_header: str, token: str) -> tuple[bool, str]:
    """Validate the Bearer header against the configured token.

    Empty configured token = agent not configured → reject (a reachable 401 is
    more useful to the brain than a silent accept).
    """
    if not token:
        return False, "recovery agent not configured (no token)"
    if not auth_header.startswith("Bearer "):
        return False, "missing bearer token"
    if auth_header[len("Bearer "):].strip() != token:
        return False, "invalid token"
    return True, ""


class RecoveryHandler(BaseHTTPRequestHandler):
    """HTTP handler — POST /recover (authenticated) + GET /healthz."""

    # Set at startup; empty string means auth is disabled (dev only).
    _token: str = ""

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/recover":
            self.send_error(404, "Not found")
            return

        ok_auth, auth_err = authorized(self.headers.get("Authorization", ""), self._token)
        if not ok_auth:
            self._send_json(401, {"ok": False, "error": auth_err})
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            body: dict = json.loads(self.rfile.read(length)) if length else {}
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "invalid JSON"})
            return

        status, payload = dispatch_recovery(str(body.get("service", "")))
        self._send_json(status, payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/healthz", "/health"}:
            self._send_json(200, {"ok": True, "service": "poindexter-recovery-agent"})
        else:
            self.send_error(404)

    def _send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        logger.info(fmt, *args)


def main() -> None:
    token = _load_token()
    if not token:
        logger.error(
            "poindexter_recovery_token not set in bootstrap.toml or "
            "POINDEXTER_RECOVERY_TOKEN env var — requests will be rejected. "
            "Set the token and restart."
        )
        # Don't exit — still start the server so the brain probe gets a 401
        # (server reachable) rather than a ConnectionError (server absent).

    RecoveryHandler._token = token

    host = os.environ.get("POINDEXTER_RECOVERY_HOST", DEFAULT_HOST)
    port = int(os.environ.get("POINDEXTER_RECOVERY_PORT", str(DEFAULT_PORT)))

    logger.info("Recovery agent starting on %s:%d", host, port)
    server = ThreadingHTTPServer((host, port), RecoveryHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
