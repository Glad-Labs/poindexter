"""AutoEmbedPostsJob — run scripts/auto-embed.py --phase posts on a schedule.

Replaces ``IdleWorker._auto_embed_posts``. Runs every hour by default.
This is the pipeline that keeps ``posts`` in pgvector current with the
posts table — when new posts land or existing ones change, the embed
script picks them up and writes the vector to ``embeddings``.

The heavy lifting lives in auto-embed.py; this job is the wrapper.

## Config (``plugin.job.auto_embed_posts``)

- ``config.script_path`` — override the default auto-resolution
- ``config.timeout_seconds`` (default 120) — embeddings can take a
  while when many posts are new
- ``config.phase`` (default ``"posts"``) — the ``--phase`` argument
  for auto-embed.py
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
    """In-container: /opt/scripts/auto-embed.py; host: scripts/auto-embed.py."""
    if os.path.isdir("/opt/scripts"):
        return "/opt/scripts/auto-embed.py"
    return "scripts/auto-embed.py"


class AutoEmbedPostsJob:
    name = "auto_embed_posts"
    description = "Embed new/changed posts into pgvector via scripts/auto-embed.py"
    schedule = "every 1 hour"
    idempotent = True  # Script internally checks for changed rows

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        script_path = str(config.get("script_path") or _default_script_path())
        timeout_s = int(config.get("timeout_seconds", 120))
        phase = str(config.get("phase", "posts"))
        cwd = site_config.get("repo_root", "/app")

        try:
            proc = await asyncio.create_subprocess_exec(
                "python", script_path, "--phase", phase,
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
            logger.exception("AutoEmbedPostsJob: spawn failed: %s", e)
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
                detail=f"auto-embed exceeded {timeout_s}s timeout",
                changes_made=0,
            )

        output = (stdout or b"").decode("utf-8", errors="replace").strip()
        err = (stderr or b"").decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            logger.warning(
                "AutoEmbedPostsJob: script exited %d: %s",
                proc.returncode, err[:400],
            )
            return JobResult(
                ok=False,
                detail=f"script exited {proc.returncode}: {err[:200]}",
                changes_made=0,
            )

        # auto-embed.py prints running counts; the tail is the final summary.
        tail = output[-100:] if output else "done"
        logger.info("AutoEmbedPostsJob: %s", tail)
        return JobResult(
            ok=True,
            detail=f"phase={phase}: {tail}",
            changes_made=1,
        )
