"""SyncSharedContextJob — invoke scripts/sync-shared-context.py on a schedule.

Replaces ``IdleWorker._sync_shared_context``. Runs every 30 minutes by
default. This is the bridge that keeps the claude-code shared-context
directory in sync with what the pipeline has decided / learned, so
future Claude Code sessions have up-to-date state.

The heavy lifting lives in the script itself — this job just invokes
it and surfaces the exit status as a JobResult via the shared
``_subprocess_runner.run_python_script`` helper.

## Config (``plugin.job.sync_shared_context``)

- ``config.script_path`` (default auto-resolves to
  ``{scripts_dir}/sync-shared-context.py``)
- ``config.timeout_seconds`` (default 30)
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.site_config import site_config

from ._subprocess_runner import resolve_scripts_dir, run_python_script

logger = logging.getLogger(__name__)


def _default_script_path() -> str:
    return f"{resolve_scripts_dir()}/sync-shared-context.py"


class SyncSharedContextJob:
    name = "sync_shared_context"
    description = "Refresh claude-code shared-context directory by running scripts/sync-shared-context.py"
    schedule = "every 30 minutes"
    idempotent = True  # The script handles re-runs as overwrites

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        script_path = str(config.get("script_path") or _default_script_path())
        timeout_s = int(config.get("timeout_seconds", 30))
        cwd = site_config.get("repo_root", "/app")

        result = await run_python_script(
            script_path,
            cwd=cwd,
            timeout_s=timeout_s,
            logger_name="SyncSharedContextJob",
        )
        return JobResult(
            ok=result.ok,
            detail=f"synced: {result.detail}" if result.ok else result.detail,
            changes_made=1 if result.ok else 0,
        )
