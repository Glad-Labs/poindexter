#!/usr/bin/env python3
"""voice_brain_host — host-side brain for the always-on voice room (#1006).

The voice-agent *container* owns the audio pipeline (LiveKit + Whisper +
Kokoro), but `claude -p` inside that container is a read-only slice of the
repo (``/app`` is mounted ``:ro``, no ``.git``, no toolchain) — it can read
and talk but cannot *do* dev work. This daemon runs `claude -p` on the
**host** instead, where it has the full repo, write access, git, the
toolchain, and every host MCP server (Poindexter KB included) — exactly
what an interactive Claude Code session has. The container POSTs each voice
turn here over ``host.docker.internal`` and speaks back whatever claude
returns.

## This is a voice -> host-RCE endpoint. It is treated as one:

* **Bearer token** on every request, constant-time compared. No token in
  logs, ever.
* **No shell.** ``subprocess`` is invoked with a list argv (``shell=False``);
  the user's prompt text is fed on **stdin**, never the command line.
* **Validated args.** ``session_id`` must be a UUID; ``permission_mode`` is
  all-listed; ``extra_args`` is accepted only as a list of strings.
* The port is host-local + docker-network only (not forwarded externally);
  the real entry surface is the LiveKit room, which is Tailscale-gated.

The daemon is **stateless** — it runs exactly what each call specifies. The
container keeps all session state, auto-reset, and the create/resume
recovery logic.

Run (started hidden by the launcher):

    VOICE_BRAIN_TOKEN=<secret> VOICE_BRAIN_CWD=C:\\Users\\mattm\\glad-labs-website \\
        python scripts/voice_brain_host.py

Env:
    VOICE_BRAIN_TOKEN   (required) shared bearer token
    VOICE_BRAIN_CWD     (required) repo root the host claude runs in
    VOICE_BRAIN_PORT    (default 8123)
    VOICE_BRAIN_BIND    (default 0.0.0.0 — must be reachable from the
                         container via host.docker.internal)
    VOICE_BRAIN_CLAUDE  (default: resolved from PATH) claude binary/path
    VOICE_BRAIN_TIMEOUT (default 120) per-turn subprocess timeout, seconds
    VOICE_BRAIN_LOG     (optional) file to mirror logs to — set by the
                         persistence launcher since the hidden ``pythonw``
                         daemon has no console/stderr. No token is ever logged.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [voice-brain-host] %(levelname)s %(message)s",
)
logger = logging.getLogger("voice_brain_host")

# Windows: spawn the per-turn ``claude`` subprocess with NO console window, so a
# voice turn doesn't pop a terminal on the host. The daemon itself runs hidden
# under ``pythonw``, but a child console app (``claude.cmd`` via ``cmd /c``)
# would otherwise get a fresh console allocated and flash a window every turn.
# 0 on POSIX (no such flag there; ``creationflags`` is simply ignored).
# See feedback_no_popups: background jobs run hidden.
_NO_WINDOW_FLAGS = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
)

# claude's --permission-mode accepts a small fixed vocabulary; allow-list it
# so a malformed/hostile body can't smuggle an arbitrary flag value through.
_PERMISSION_MODES = frozenset(
    {"dontAsk", "acceptEdits", "default", "plan", "bypassPermissions"},
)
# Reject anything that isn't a bare UUID as the session id — this value lands
# in the argv next to --session-id / --resume, so it must not be attacker
# shaped even though we never use a shell.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
)


class _Config:
    """Validated daemon config, loaded once at startup (fail-loud)."""

    def __init__(self) -> None:
        # Token: env wins; otherwise read ``~/.poindexter/voice_brain_token``.
        # The file fallback lets the persistence launcher keep the secret OUT
        # of the scheduled-task definition / registry / process env — the task
        # just runs the daemon and it picks the token up from the operator's
        # local state dir (same trust boundary as bootstrap.toml).
        self.token = os.environ.get("VOICE_BRAIN_TOKEN", "").strip()
        if not self.token:
            self.token = self._read_token_file()
        # CWD: env wins; otherwise default to the repo root (this file lives at
        # ``<repo>/scripts/voice_brain_host.py``), so a bare ``pythonw
        # voice_brain_host.py`` from the scheduled task needs no env wiring.
        self.cwd = os.environ.get("VOICE_BRAIN_CWD", "").strip()
        if not self.cwd:
            self.cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.port = int(os.environ.get("VOICE_BRAIN_PORT", "8123"))
        self.bind = os.environ.get("VOICE_BRAIN_BIND", "0.0.0.0").strip()
        self.timeout = float(os.environ.get("VOICE_BRAIN_TIMEOUT", "120"))
        claude = os.environ.get("VOICE_BRAIN_CLAUDE", "").strip() or "claude"
        resolved = claude if os.path.isabs(claude) else shutil.which(claude)
        # Empty string (not None) when unresolved, so the type stays ``str``
        # and the fail-loud check below (``not os.path.exists("")``) still trips.
        self.claude: str = resolved or ""

        problems = []
        if not self.token:
            problems.append("VOICE_BRAIN_TOKEN is required")
        if len(self.token) < 16:
            problems.append("VOICE_BRAIN_TOKEN must be >= 16 chars")
        if not self.cwd or not os.path.isdir(self.cwd):
            problems.append(f"VOICE_BRAIN_CWD must be an existing dir (got {self.cwd!r})")
        if not self.claude or not os.path.exists(self.claude):
            problems.append(
                f"claude binary not found (VOICE_BRAIN_CLAUDE={claude!r}); "
                "set VOICE_BRAIN_CLAUDE to its full path",
            )
        if problems:
            for p in problems:
                logger.error("config error: %s", p)
            sys.exit(2)

    @staticmethod
    def _read_token_file() -> str:
        """Read the bearer token from ``~/.poindexter/voice_brain_token``.

        Returns ``""`` if the file is absent/unreadable — the caller's
        fail-loud length check then reports the missing-token problem.
        """
        path = os.path.join(_default_poindexter_dir(), "voice_brain_token")
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    def exec_prefix(self) -> list[str]:
        """Base argv prefix for invoking claude.

        On Windows the CLI is often a ``claude.cmd`` shim, which
        ``CreateProcess`` can't launch directly (and Python 3.12+ refuses to
        run ``.cmd``/``.bat`` without a shell). Wrap those via ``cmd /c`` —
        our own args are still passed as a list, and the prompt text only
        ever travels on stdin, so there is no shell-injection surface.
        """
        if os.name == "nt" and self.claude.lower().endswith((".cmd", ".bat")):
            return ["cmd", "/c", self.claude]
        return [self.claude]


CFG: _Config | None = None  # set in main()


def _build_argv(body: dict) -> list[str]:
    """Translate a /turn body into the exact claude argv to run.

    Mirrors the container's ClaudeCodeBridgeLLMService._build_argv so the
    host behaves identically — first turn CREATEs (--session-id), later turns
    RESUME (--resume). Raises ValueError on any invalid field.
    """
    assert CFG is not None
    session_id = body.get("session_id")
    if not isinstance(session_id, str) or not _UUID_RE.match(session_id):
        raise ValueError("session_id must be a UUID")
    mode = body.get("permission_mode", "dontAsk")
    if mode not in _PERMISSION_MODES:
        raise ValueError(f"permission_mode {mode!r} not allowed")
    extra = body.get("extra_args") or []
    if not isinstance(extra, list) or not all(isinstance(a, str) for a in extra):
        raise ValueError("extra_args must be a list of strings")
    first_turn = bool(body.get("first_turn"))

    argv = CFG.exec_prefix() + [
        "-p",
        "--output-format", "json",
        "--permission-mode", mode,
    ]
    argv += ["--session-id", session_id] if first_turn else ["--resume", session_id]
    argv += extra
    return argv


def _run_turn(body: dict) -> dict:
    """Run one claude turn on the host; return {returncode, stdout, stderr}."""
    assert CFG is not None
    text = body.get("text")
    if not isinstance(text, str) or not text:
        raise ValueError("text is required")
    argv = _build_argv(body)
    logger.info(
        "turn session=%s first=%s text_len=%d",
        body.get("session_id"), bool(body.get("first_turn")), len(text),
    )
    try:
        proc = subprocess.run(
            argv,
            input=text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=CFG.cwd,
            timeout=CFG.timeout,
            shell=False,
            # No console-window popup on Windows (hidden background daemon).
            creationflags=_NO_WINDOW_FLAGS,
        )
    except subprocess.TimeoutExpired:
        return {
            "returncode": 124,
            "stdout": "",
            "stderr": f"claude -p timed out after {CFG.timeout}s (host)",
        }
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout.decode("utf-8", errors="replace"),
        "stderr": proc.stderr.decode("utf-8", errors="replace"),
    }


class _Handler(BaseHTTPRequestHandler):
    server_version = "voice-brain-host/1.0"

    def _send(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed(self) -> bool:
        assert CFG is not None
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        # Constant-time compare so a wrong token can't be timing-probed.
        return hmac.compare_digest(header[len(prefix):], CFG.token)

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path == "/healthz":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/turn":
            self._send(404, {"error": "not found"})
            return
        if not self._authed():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "bad json body"})
            return
        try:
            result = _run_turn(body)
        except ValueError as e:
            self._send(400, {"error": str(e)})
            return
        except Exception as e:  # noqa: BLE001 — never 500 with a stack to the client
            logger.exception("turn failed")
            self._send(500, {"error": f"{type(e).__name__}: {e}"})
            return
        self._send(200, result)

    def log_message(self, fmt: str, *args) -> None:
        # Route the stdlib access log through our logger; it never contains
        # the token (Authorization headers aren't logged by BaseHTTPRequestHandler).
        logger.info("%s - %s", self.address_string(), fmt % args)


def _default_poindexter_dir() -> str:
    """``~/.poindexter`` — the operator's local state dir (no hardcoded path)."""
    return os.path.join(os.path.expanduser("~"), ".poindexter")


def _attach_file_log() -> None:
    """Mirror logs to a file when run windowless (or when explicitly asked).

    The persistence launcher (`scripts/voice-brain-host.ps1`) runs this daemon
    under ``pythonw`` so it has NO console window (per the hidden-background-job
    policy) — which means stderr (where ``basicConfig`` sends logs) is
    discarded. Resolution:

      1. ``VOICE_BRAIN_LOG`` (explicit) — always honoured.
      2. else if there is no usable stderr (``pythonw`` sets ``sys.stderr`` to
         ``None``) — default to ``~/.poindexter/voice_brain_host.log`` so the
         hidden daemon is never silently logless.
      3. else (interactive ``python``) — stderr only, no file.

    The token is never logged, so the file carries no secret.
    """
    path = os.environ.get("VOICE_BRAIN_LOG", "").strip()
    if not path and sys.stderr is None:
        path = os.path.join(_default_poindexter_dir(), "voice_brain_host.log")
    if not path:
        return
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [voice-brain-host] %(levelname)s %(message)s",
            ),
        )
        logging.getLogger().addHandler(handler)
        logger.info("file logging enabled -> %s", path)
    except OSError as e:
        # A bad log path must not take the daemon down — it still serves and
        # logs to stderr (visible when run interactively).
        logger.warning("could not open VOICE_BRAIN_LOG=%r: %s", path, e)


def main() -> int:
    global CFG
    _attach_file_log()
    CFG = _Config()
    httpd = ThreadingHTTPServer((CFG.bind, CFG.port), _Handler)
    logger.info(
        "listening on %s:%d  cwd=%s  claude=%s  (token len=%d)",
        CFG.bind, CFG.port, CFG.cwd, CFG.claude, len(CFG.token),
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
