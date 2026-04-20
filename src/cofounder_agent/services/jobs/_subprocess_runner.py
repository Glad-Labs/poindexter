"""Shared helper for Jobs that wrap a subprocess invocation.

``SyncSharedContextJob``, ``AutoEmbedPostsJob``, and future script-
driven jobs all need the same pattern:
    spawn → wait-with-timeout → check returncode → surface stderr.

Centralising it here keeps the spawn-failure / timeout / stderr
handling consistent, so any future fix lands in one place.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ScriptResult:
    """Outcome of running a wrapped subprocess.

    Shape chosen so callers can map 1:1 onto JobResult:

        res = await run_python_script(...)
        return JobResult(ok=res.ok, detail=res.detail, changes_made=1 if res.ok else 0)
    """

    ok: bool
    detail: str
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""


def resolve_scripts_dir() -> str:
    """Return the scripts directory that exists on this host.

    Container layout: ``/opt/scripts`` (scripts bind-mounted read-only
    outside /app which is also a read-only mount). Dev/host layout:
    ``scripts/`` relative to cwd.
    """
    if os.path.isdir("/opt/scripts"):
        return "/opt/scripts"
    return "scripts"


async def run_python_script(
    script_path: str,
    *extra_args: str,
    cwd: str | None = None,
    timeout_s: int = 30,
    tail_chars: int = 100,
    logger_name: str = "subprocess_runner",
) -> ScriptResult:
    """Run ``python script_path [extra_args]`` and summarise the outcome.

    Doesn't raise — every failure mode comes back as ``ScriptResult``
    with ``ok=False`` and a single-line ``detail`` suitable for
    surfacing in a JobResult.

    Args:
        script_path: Absolute or cwd-relative path to the script.
        *extra_args: Additional argv entries (e.g. ``"--phase", "posts"``).
        cwd: Working directory for the subprocess. Defaults to the
            current process cwd.
        timeout_s: How long to wait before killing the child. Must be
            ≥ 1.
        tail_chars: How many chars of stdout / stderr to embed in the
            ``detail`` string (helps surface the gist without flooding
            logs or JobResult consumers).
        logger_name: Prefix used in warning / info log lines; makes
            multi-job log streams readable.
    """
    log = logging.getLogger(logger_name)

    try:
        proc = await asyncio.create_subprocess_exec(
            "python", script_path, *extra_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
    except FileNotFoundError as e:
        return ScriptResult(
            ok=False, detail=f"python or script not found: {e}",
        )
    except Exception as e:
        log.exception("%s: spawn failed: %s", logger_name, e)
        return ScriptResult(ok=False, detail=f"spawn failed: {e}")

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ScriptResult(
            ok=False, detail=f"exceeded {timeout_s}s timeout",
            returncode=None,
        )

    stdout = (stdout_b or b"").decode("utf-8", errors="replace").strip()
    stderr = (stderr_b or b"").decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        log.warning(
            "%s: script exited %d: %s",
            logger_name, proc.returncode, stderr[:400],
        )
        return ScriptResult(
            ok=False,
            detail=f"script exited {proc.returncode}: {stderr[:tail_chars * 2]}",
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    tail = stdout[-tail_chars:] if stdout else "done"
    log.info("%s: %s", logger_name, tail)
    return ScriptResult(
        ok=True,
        detail=tail,
        returncode=0,
        stdout=stdout,
        stderr=stderr,
    )
