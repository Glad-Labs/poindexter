"""MemoryFilesTap — ingest memory files under every Claude project scope.

Replaces Phase 1 of ``scripts/auto-embed.py``. Preserves every fix that
landed in the pre-refactor pipeline:

- **Multi-scope discovery** (commit ``c84a9032``): scans the ``memory/``
  subdirectory of every ``~/.claude/projects/<scope>/`` directory, not
  just a single hardcoded scope. Prevents the 2026-04-18 incident where
  files written from a ``C:\\WINDOWS\\system32`` cwd were silently skipped.

  Scope discovery is **platform-neutral**: any subdirectory holding a
  ``memory/`` folder is a scope. It used to glob ``C--*``, which encoded
  the Windows project-scope naming (``C--Users-<you>-...``). The Pop!_OS
  migration re-keyed scopes to the Linux checkout path
  (``-home-<you>-<project>``), so that glob matched **zero**
  directories and the tap silently ingested nothing for 17 days — every
  memory file written after 2026-07-20 was invisible to semantic recall.
  Discovery must never assume a host-specific scope-naming convention.
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
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from plugins.tap import Document
from services.taps._chunking import classify_file

logger = logging.getLogger(__name__)


_SENTINEL_SKIP = "__skip__"


def _is_readable_dir(path: Path) -> bool:
    """``path.is_dir()`` that treats "permission denied" as "not a dir".

    ``Path.is_dir()`` only swallows ENOENT/ENOTDIR/EBADF/ELOOP —
    ``EACCES`` propagates. A single unreadable scope directory would
    therefore abort discovery for *every* scope. Docker bind-mounts of
    ``~/.claude/projects`` hit this constantly: Claude Code creates
    per-session scope dirs mode ``0700``, so a container running under a
    different uid cannot stat them.

    Logged at WARNING, never swallowed silently — an unreadable scope is
    a real coverage gap the operator needs to see (fix the uid mismatch),
    not a condition to paper over.
    """
    try:
        return path.is_dir()
    except PermissionError:
        logger.warning(
            "MemoryFilesTap: permission denied reading %s — skipping this scope. "
            "The embedding container's uid likely differs from the owner of "
            "~/.claude/projects; scope dirs are mode 0700.",
            path,
        )
        return False
    except OSError as exc:
        logger.warning("MemoryFilesTap: cannot stat %s (%s) — skipping.", path, exc)
        return False


def _iter_scope_dirs(projects_root: Path) -> list[Path]:
    """Every project scope directory holding a ``memory/`` subdirectory.

    Platform-neutral by construction: a scope is identified by *having*
    a ``memory/`` folder, not by matching a naming convention. Windows
    (``C--Users-alice``) and Linux (``-home-alice-project``) scopes are
    both discovered, so this is back-compatible with pre-migration hosts.
    """
    try:
        entries = sorted(projects_root.iterdir())
    except OSError as exc:  # includes PermissionError
        logger.warning(
            "MemoryFilesTap: cannot list project scopes under %s (%s).",
            projects_root,
            exc,
        )
        return []
    return [d for d in entries if _is_readable_dir(d) and _is_readable_dir(d / "memory")]


def _discover_memory_dirs(
    claude_projects_dir: str | None = None,
    openclaw_memory_dir: str | None = None,
    shared_context_dir: str | None = None,
    *,
    site_config: Any = None,
    scope_allowlist: str = "",
) -> list[tuple[Path, str, str]]:
    """Return the list of ``(path, origin, scope)`` tuples to scan.

    - ``origin`` is the ``writer`` label stored on each Document
      (``claude-code`` / ``shared-context`` / ``openclaw``).
    - ``scope`` is the sub-namespace within an origin. For ``claude-code``
      it's the project-scope directory name (e.g. ``C--WINDOWS-system32``).
      For other origins, an empty string — they don't have scopes.

    site_config keys ``claude_projects_dir`` / ``openclaw_memory_dir`` /
    ``shared_context_dir`` override the defaults; config args override
    site_config. Passing the sentinel ``"__skip__"`` disables that source
    entirely — useful for tests that shouldn't touch real home dirs.

    ``scope_allowlist`` is a comma-separated list of ``claude-code`` project
    scopes (the scope directory names) to ingest; when set, every other
    scope is skipped. Empty means "all scopes" (back-compat). Matching is
    case-insensitive because Docker bind mounts can lowercase Windows dir
    names. This is the dedup guard for the ``C--Users-alice`` ⇄
    ``C--Users-alice-myproject`` junction: the latter is a Windows
    Junction to the former, so on the host (where the reparse point
    resolves) both scopes would otherwise embed the same files twice.

    An allowlist that matches **no** scope on disk is logged at WARNING —
    it means every scope was filtered out and the tap will ingest nothing.
    That is exactly how the stale Windows-era ``C--Users-<you>`` allowlist
    survived the Pop!_OS migration unnoticed; a silent empty result here
    is indistinguishable from "no memory files exist".

    site_config is the DI seam (glad-labs-stack#330) — passed in by the
    tap dispatcher rather than imported as a module-level singleton.
    """
    _sc = site_config

    def _resolve(cfg_value: Any, sc_key: str, default: Path) -> Path | None:
        if cfg_value == _SENTINEL_SKIP:
            return None
        if cfg_value:
            return Path(cfg_value)
        if _sc is not None:
            sc_val = _sc.get(sc_key, "")
            if sc_val:
                return Path(sc_val)
        return Path(default)

    projects_root = _resolve(
        claude_projects_dir,
        "claude_projects_dir",
        Path.home() / ".claude" / "projects",
    )
    openclaw_root = _resolve(
        openclaw_memory_dir,
        "openclaw_memory_dir",
        Path.home() / ".openclaw" / "workspace" / "memory",
    )
    shared_root = _resolve(
        shared_context_dir,
        "shared_context_dir",
        Path.home() / "glad-labs-website" / ".shared-context",
    )

    dirs: list[tuple[Path, str, str]] = []

    allow = {s.strip().lower() for s in scope_allowlist.split(",") if s.strip()}

    if projects_root and projects_root.is_dir():
        scope_dirs = _iter_scope_dirs(projects_root)
        matched = [d for d in scope_dirs if not allow or d.name.lower() in allow]
        for scope_dir in matched:
            dirs.append((scope_dir / "memory", "claude-code", scope_dir.name))

        # Fail loud rather than return an empty list that reads as "no
        # memory files exist". Both branches below are silent-ingest-nothing
        # states that previously looked identical to a healthy empty run.
        if scope_dirs and not matched:
            logger.warning(
                "MemoryFilesTap: scope allowlist %s matched none of the %d scope(s) "
                "under %s — ingesting nothing. Scopes on disk: %s",
                sorted(allow),
                len(scope_dirs),
                projects_root,
                sorted(d.name for d in scope_dirs)[:10],
            )
        elif not scope_dirs:
            logger.warning(
                "MemoryFilesTap: no project scopes with a memory/ subdirectory "
                "found under %s — ingesting nothing.",
                projects_root,
            )

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
            # DI seam (glad-labs-stack#330) — taps receive `_site_config`
            # from the dispatcher per CLAUDE.md.
            site_config=config.get("_site_config"),
            scope_allowlist=config.get("memory_scope_allowlist", "") or "",
        )

        total_files = 0
        for dir_path, origin, scope in memory_dirs:
            for filepath in sorted(dir_path.rglob("*.md")):
                total_files += 1
                try:
                    text = filepath.read_text(encoding="utf-8")
                except Exception as e:
                    logger.exception("MemoryFilesTap: read failed for %s: %s", filepath, e)
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
