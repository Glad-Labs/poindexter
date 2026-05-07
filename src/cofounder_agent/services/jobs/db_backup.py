"""DbBackupJob — invoke ``scripts/db-backup-local.sh`` on a schedule.

Replaces ``IdleWorker._backup_database``. Runs every 12 hours by
default. The script handles pg_dump + compression + rotation on its
own; this Job just invokes it.

Config (``plugin.job.db_backup``):
- ``config.script_path`` (default auto-resolves to ``scripts/db-backup-local.sh``)
- ``config.timeout_seconds`` (default 600)
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


def _default_script_path() -> Path | None:
    # ``./src/cofounder_agent`` is mounted at ``/app:ro`` in the worker
    # compose service, and ``./scripts`` lives next to it on the host
    # but is mounted at ``/opt/scripts:ro`` because ``/app:ro`` rejects
    # child mounts. The original auto-resolver only checked
    # ``/app/scripts``, so the default path was always ``None`` in the
    # local docker stack and the job failed every 12h with
    # "db backup script not found at None". Add ``/opt/scripts`` first
    # since it's the canonical container location, fall back to the
    # legacy paths.
    candidates = [
        Path("/opt/scripts/db-backup-local.sh"),
        Path("/app/scripts/db-backup-local.sh"),
        Path.home() / "glad-labs-website" / "scripts" / "db-backup-local.sh",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


class DbBackupJob:
    name = "db_backup"
    description = "Run pg_dump against the local brain DB via scripts/db-backup-local.sh"
    schedule = "every 12 hours"
    idempotent = True  # Safe to run back-to-back; each dump is timestamped

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        script = config.get("script_path") or _default_script_path()
        if not script or not Path(script).is_file():
            return JobResult(
                ok=False,
                detail=f"db backup script not found at {script!r}",
                changes_made=0,
            )

        timeout = int(config.get("timeout_seconds", 600))

        cmd = ["bash", str(script)]
        env = os.environ.copy()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return JobResult(
                    ok=False,
                    detail=f"db backup exceeded {timeout}s timeout",
                    changes_made=0,
                )
        except Exception as e:
            return JobResult(ok=False, detail=f"spawn failed: {e}", changes_made=0)

        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace")
            logger.error("DbBackupJob: script exited %d: %s", proc.returncode, err[:400])
            return JobResult(
                ok=False,
                detail=f"script exited {proc.returncode}",
                changes_made=0,
            )

        out = (stdout or b"").decode("utf-8", errors="replace")
        logger.info("DbBackupJob: backup complete (%d bytes of output)", len(out))
        return JobResult(ok=True, detail="backup completed", changes_made=1)
