"""SyncSharedContextJob — invoke scripts/sync-shared-context.py on a schedule.

Replaces ``IdleWorker._sync_shared_context``. Runs every 30 minutes by
default. This is the bridge that keeps the claude-code shared-context
directory in sync with what the pipeline has decided / learned, so
future Claude Code sessions have up-to-date state.

The heavy lifting lives in the script itself — this job just invokes
it and surfaces the exit status as a JobResult.

## Config (``plugin.job.sync_shared_context``)

- ``config.script_path`` (default auto-resolves to
  ``/opt/scripts/sync-shared-context.py`` in-container, else
  ``scripts/sync-shared-context.py`` on the host)
- ``config.timeout_seconds`` (default 30)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from plugins.job import JobResult
from services.site_config import site_config

logger = logging.getLogger(__name__)


def _default_script_path() -> str:
    """Find sync-shared-context.py at the expected location.

    In-container the scripts dir is bind-mounted at ``/opt/scripts``;
    on the host it lives at ``scripts/`` relative to the repo root.
    """
    if os.path.isdir("/opt/scripts"):
        return "/opt/scripts/sync-shared-context.py"
    return "scripts/sync-shared-context.py"


class SyncSharedContextJob:
    name = "sync_shared_context"
    description = "Refresh claude-code shared-context directory by running scripts/sync-shared-context.py"
    schedule = "every 30 minutes"
    idempotent = True  # The script handles re-runs as overwrites

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        script_path = str(config.get("script_path") or _default_script_path())
        timeout_s = int(config.get("timeout_seconds", 30))
        cwd = site_config.get("repo_root", "/app")

        try:
            proc = await asyncio.create_subprocess_exec(
                "python", script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
        except FileNotFoundError as e:
            return JobResult(
                ok=False,
                detail=f"python or script not found: {e}",
                changes_made=0,
            )
        except Exception as e:
            logger.exception("SyncSharedContextJob: spawn failed: %s", e)
            return JobResult(ok=False, detail=f"spawn failed: {e}", changes_made=0)

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return JobResult(
                ok=False,
                detail=f"sync-shared-context script exceeded {timeout_s}s timeout",
                changes_made=0,
            )

        output = (stdout or b"").decode("utf-8", errors="replace").strip()
        err = (stderr or b"").decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            logger.warning(
                "SyncSharedContextJob: script exited %d: %s",
                proc.returncode, err[:400],
            )
            return JobResult(
                ok=False,
                detail=f"script exited {proc.returncode}: {err[:200]}",
                changes_made=0,
            )

        logger.info("SyncSharedContextJob: synced — %s", output[:80])
        return JobResult(
            ok=True,
            detail=f"synced: {output[:100]}" if output else "synced",
            # Script doesn't tell us how many files changed; count the run.
            changes_made=1,
        )
