"""AutoEmbedPostsJob — run scripts/auto-embed.py --phase posts on a schedule.

Replaces ``IdleWorker._auto_embed_posts``. Runs every hour by default.
This is the pipeline that keeps ``posts`` in pgvector current with the
posts table — when new posts land or existing ones change, the embed
script picks them up and writes the vector to ``embeddings``.

Wraps the shared ``_subprocess_runner.run_python_script`` helper so
spawn / timeout / exit-code handling stays consistent with other
script-driven jobs.

## Config (``plugin.job.auto_embed_posts``)

- ``config.script_path`` — override the default auto-resolution
- ``config.timeout_seconds`` (default 120) — embeddings can take a
  while when many posts are new
- ``config.phase`` (default ``"posts"``) — the ``--phase`` argument
  for auto-embed.py
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult

from ._subprocess_runner import resolve_scripts_dir, run_python_script

logger = logging.getLogger(__name__)


def _default_script_path() -> str:
    return f"{resolve_scripts_dir()}/auto-embed.py"


class AutoEmbedPostsJob:
    name = "auto_embed_posts"
    description = "Embed new/changed posts into pgvector via scripts/auto-embed.py"
    schedule = "every 1 hour"
    idempotent = True  # Script internally checks for changed rows

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        script_path = str(config.get("script_path") or _default_script_path())
        timeout_s = int(config.get("timeout_seconds", 120))
        phase = str(config.get("phase", "posts"))
        # DI seam (glad-labs-stack#330)
        sc = config.get("_site_config")
        cwd = sc.get("repo_root", "/app") if sc is not None else "/app"

        result = await run_python_script(
            script_path,
            "--phase", phase,
            cwd=cwd,
            timeout_s=timeout_s,
            logger_name="AutoEmbedPostsJob",
        )
        return JobResult(
            ok=result.ok,
            detail=f"phase={phase}: {result.detail}",
            changes_made=1 if result.ok else 0,
        )
