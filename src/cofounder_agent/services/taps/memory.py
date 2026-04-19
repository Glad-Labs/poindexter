"""MemoryFilesTap — ingest memory files under every Claude project scope.

Replaces Phase 1 of ``scripts/auto-embed.py``. Preserves every fix that
landed in the pre-refactor pipeline:

- **Multi-scope discovery** (commit ``c84a9032``): scans every
  ``~/.claude/projects/C--*/memory/`` directory, not just a single
  hardcoded scope. Prevents the 2026-04-18 incident where files
  written from a ``C:\\WINDOWS\\system32`` cwd were silently skipped.
- **Scope-aware source_id** (commit ``59fcbdde``): each file gets
  ``claude-code/<scope>/<relpath>`` as its source_id so same-named
  files across scopes (e.g. ``MEMORY.md`` in two scopes) don't collide
  on the ``embeddings`` unique constraint.
- **Heading-aware chunking** (commit ``8b26041f``): files larger than
  ``MAX_CHARS`` split at markdown heading boundaries.

Config (``plugin.tap.memory`` in ``app_settings``):

- ``enabled`` (default ``true``)
- ``interval_seconds`` (default ``3600``) — hourly by default
- ``config.claude_projects_dir`` — overrides the default
  ``~/.claude/projects`` path. Useful in containers that bind-mount
  the projects tree to a fixed location.
- ``config.openclaw_memory_dir`` — overrides the default
  ``~/.openclaw/workspace/memory`` path.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator, List, Tuple

from plugins.tap import Document
from services.taps._chunking import classify_file

logger = logging.getLogger(__name__)


_SENTINEL_SKIP = "__skip__"


def _discover_memory_dirs(
    claude_projects_dir: str | None = None,
    openclaw_memory_dir: str | None = None,
    shared_context_dir: str | None = None,
) -> List[Tuple[Path, str, str]]:
    """Return the list of ``(path, origin, scope)`` tuples to scan.

    - ``origin`` is the ``writer`` label stored on each Document
      (``claude-code`` / ``shared-context`` / ``openclaw``).
    - ``scope`` is the sub-namespace within an origin. For ``claude-code``
      it's the project-scope directory name (e.g. ``C--WINDOWS-system32``).
      For other origins, an empty string — they don't have scopes.

    Env vars ``CLAUDE_PROJECTS_DIR`` / ``OPENCLAW_MEMORY_DIR`` /
    ``SHARED_CONTEXT_DIR`` override the defaults; config args override
    env vars. Passing the sentinel ``"__skip__"`` disables that source
    entirely — useful for tests that shouldn't touch real home dirs.
    """

    def _resolve(cfg_value, env_var, default):
        if cfg_value == _SENTINEL_SKIP:
            return None
        return Path(cfg_value or os.getenv(env_var) or default)

    projects_root = _resolve(
        claude_projects_dir,
        "CLAUDE_PROJECTS_DIR",
        Path.home() / ".claude" / "projects",
    )
    openclaw_root = _resolve(
        openclaw_memory_dir,
        "OPENCLAW_MEMORY_DIR",
        Path.home() / ".openclaw" / "workspace" / "memory",
    )
    shared_root = _resolve(
        shared_context_dir,
        "SHARED_CONTEXT_DIR",
        Path.home() / "glad-labs-website" / ".shared-context",
    )

    dirs: List[Tuple[Path, str, str]] = []

    if projects_root and projects_root.is_dir():
        for scope_dir in sorted(projects_root.glob("C--*")):
            mem = scope_dir / "memory"
            if mem.is_dir():
                dirs.append((mem, "claude-code", scope_dir.name))

    if shared_root and shared_root.is_dir():
        dirs.append((shared_root, "shared-context", ""))

    if openclaw_root and openclaw_root.is_dir():
        dirs.append((openclaw_root, "openclaw", ""))

    return dirs


def _build_source_id(origin: str, scope: str, rel_path: str) -> str:
    """Construct the canonical source_id for a memory file.

    For ``claude-code`` origin, includes the scope directory name so
    same-filename-across-scopes doesn't collide on the embeddings unique
    constraint. For other origins, just ``<origin>/<rel_path>``.
    """
    if origin == "claude-code" and scope:
        return f"{origin}/{scope}/{rel_path}"
    return f"{origin}/{rel_path}"


class MemoryFilesTap:
    """Ingest every ``.md`` file under every Claude / shared-context /
    OpenClaw memory directory.

    Yields one Document per chunk per file. For a small single-chunk
    file that's one Document; for a large file split into N chunks,
    N Documents.
    """

    name = "memory"
    interval_seconds = 3600

    async def extract(
        self,
        pool: Any,  # asyncpg.Pool — unused; this Tap reads the filesystem
        config: dict[str, Any],
    ) -> AsyncIterator[Document]:
        del pool  # kept for Protocol compatibility; filesystem Tap doesn't need it

        memory_dirs = _discover_memory_dirs(
            claude_projects_dir=config.get("claude_projects_dir"),
            openclaw_memory_dir=config.get("openclaw_memory_dir"),
            shared_context_dir=config.get("shared_context_dir"),
        )

        total_files = 0
        for dir_path, origin, scope in memory_dirs:
            for filepath in sorted(dir_path.rglob("*.md")):
                total_files += 1
                try:
                    text = filepath.read_text(encoding="utf-8")
                except Exception as e:
                    logger.error("MemoryFilesTap: read failed for %s: %s", filepath, e)
                    continue
                if not text.strip():
                    continue

                rel = filepath.relative_to(dir_path).as_posix()
                source_id = _build_source_id(origin, scope, rel)
                file_type = classify_file(filepath.name)

                # One Document per file. The runner handles chunking based
                # on text length — that keeps every Tap's contract simple
                # and lets the chunking policy change in one place.
                yield Document(
                    source_id=source_id,
                    source_table="memory",
                    text=text,
                    metadata={
                        "filename": filepath.name,
                        "type": file_type,
                        "chars": len(text),
                        "origin_path": str(filepath),
                    },
                    writer=origin,
                )

        logger.info(
            "MemoryFilesTap: scanned %d memory file(s) across %d directories",
            total_files, len(memory_dirs),
        )
