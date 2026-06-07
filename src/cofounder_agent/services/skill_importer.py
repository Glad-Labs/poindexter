"""Skill importer service (poindexter#529).

Fetches, validates, writes, and catalogs SKILL.md files from URLs or local
paths.  No module-level singletons — all state is passed via function args
(DI everywhere, per project conventions).

Public API
----------
import_skill(source, *, pack, pool, site_config, force, http_client) -> dict
list_skills(*, pool) -> list[dict]
remove_skill(name, *, pool) -> dict
SkillImportError
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_DEFAULT_ALLOWED_LICENSES = [
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
]

# Skills directory relative to this file: services/ -> skills/
_SKILLS_DIR = Path(__file__).parent.parent / "skills"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SkillImportError(Exception):
    """Raised for user-fixable import problems.

    Examples: bad license, malformed frontmatter, slug-unsafe name,
    collision without --force.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _skills_dir() -> Path:
    """Return the absolute path to the skills/ directory."""
    return _SKILLS_DIR


def _convert_github_blob_url(url: str) -> str:
    """Auto-convert GitHub blob URLs to raw.githubusercontent.com.

    ``https://github.com/user/repo/blob/main/path/to/SKILL.md``
    → ``https://raw.githubusercontent.com/user/repo/main/path/to/SKILL.md``
    """
    pattern = re.compile(
        r"^https://github\.com/([^/]+/[^/]+)/blob/(.+)$",
    )
    m = pattern.match(url)
    if m:
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}"
    return url


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a SKILL.md string.

    Returns ``(meta_dict, body_string)`` where ``body_string`` is the
    markdown content after the closing ``---`` delimiter.

    Raises ``SkillImportError`` if frontmatter is missing or malformed.
    """
    import yaml  # lazy import — not always needed

    if not raw.startswith("---"):
        raise SkillImportError(
            "SKILL.md must start with a YAML frontmatter block "
            "(first line must be '---')."
        )

    # Find the closing ---
    end = raw.find("\n---", 3)
    if end == -1:
        raise SkillImportError(
            "SKILL.md frontmatter block is not closed (missing second '---')."
        )

    fm_text = raw[3:end].strip()
    body = raw[end + 4 :]  # skip '\n---'

    try:
        meta = yaml.safe_load(fm_text)
    except Exception as exc:
        raise SkillImportError(f"SKILL.md frontmatter YAML is invalid: {exc}") from exc

    if not isinstance(meta, dict):
        raise SkillImportError("SKILL.md frontmatter must be a YAML mapping.")

    return meta, body


def _validate_meta(meta: dict) -> None:
    """Validate required frontmatter fields.

    Raises ``SkillImportError`` with a human-readable message on the first
    validation failure encountered.
    """
    # Required scalar fields
    for field in ("name", "description", "license"):
        if not meta.get(field):
            raise SkillImportError(
                f"SKILL.md frontmatter is missing required field '{field}'."
            )

    name = meta["name"]
    if not _SLUG_RE.match(name):
        raise SkillImportError(
            f"SKILL.md name '{name}' is not slug-safe. "
            "Use only lowercase letters, digits, and hyphens "
            "(e.g. 'my-skill', not 'My Skill')."
        )

    # metadata.prompts
    metadata = meta.get("metadata")
    if not isinstance(metadata, dict):
        raise SkillImportError(
            "SKILL.md frontmatter must contain a 'metadata' mapping."
        )

    prompts = metadata.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        raise SkillImportError(
            "SKILL.md frontmatter 'metadata.prompts' must be a non-empty list."
        )

    for i, prompt in enumerate(prompts):
        if not isinstance(prompt, dict) or not prompt.get("key"):
            raise SkillImportError(
                f"SKILL.md metadata.prompts[{i}] must be a mapping with a 'key' field."
            )


def _resolve_allowed_licenses(site_config: Any | None) -> list[str]:
    """Return the list of allowed SPDX identifiers.

    If ``site_config`` is ``None`` or the key is missing, falls back to the
    built-in default list.
    """
    if site_config is None:
        return _DEFAULT_ALLOWED_LICENSES

    raw = site_config.get("skill_importer_allowed_licenses", "")
    if not raw:
        return _DEFAULT_ALLOWED_LICENSES

    return [s.strip() for s in raw.split(",") if s.strip()]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def import_skill(
    source: str,
    *,
    pack: str = "imported",
    pool: Any = None,
    site_config: Any = None,
    force: bool = False,
    http_client: Any = None,
) -> dict:
    """Fetch, validate, write, and catalog a SKILL.md.

    Parameters
    ----------
    source:
        URL (``https://``) or local file path.  GitHub blob URLs are
        auto-converted to raw.githubusercontent.com.
    pack:
        Skill pack subdirectory (default ``"imported"``).
    pool:
        asyncpg pool.  When provided the skill is upserted into
        ``skill_catalog``; omit for disk-only mode.
    site_config:
        ``SiteConfig`` instance used to read ``skill_importer_allowed_licenses``.
        Falls back to the built-in allow-list when ``None``.
    force:
        Overwrite an already-installed skill.  Without this flag a
        collision raises ``SkillImportError``.
    http_client:
        ``httpx.AsyncClient`` to use for URL fetches.  A temporary
        client is created (and closed) when ``None``.

    Returns
    -------
    dict with keys: ok, name, pack, path, license, prompt_count,
    installed, updated.

    Raises
    ------
    SkillImportError
        Validation failure, license rejection, or collision without ``force``.
    RuntimeError
        Network or IO failure.
    """
    # ------------------------------------------------------------------
    # Fetch raw content
    # ------------------------------------------------------------------
    if source.startswith("https://"):
        raw_bytes = await _fetch_url(source, http_client=http_client)
    else:
        raw_bytes = await _read_local(source)

    raw_str = raw_bytes.decode("utf-8", errors="replace")
    import_hash = hashlib.sha256(raw_bytes).hexdigest()

    # ------------------------------------------------------------------
    # Parse & validate
    # ------------------------------------------------------------------
    meta, _ = _parse_frontmatter(raw_str)
    _validate_meta(meta)

    name: str = meta["name"]
    license_id: str = meta["license"]
    description: str = meta.get("description", "")
    prompts: list = meta["metadata"]["prompts"]
    prompt_count = len(prompts)

    # License check
    allowed = _resolve_allowed_licenses(site_config)
    if license_id not in allowed:
        raise SkillImportError(
            f"License '{license_id}' is not in the allowed list "
            f"({', '.join(allowed)}). "
            "Add it via: poindexter settings set skill_importer_allowed_licenses "
            f"<current_list>,{license_id}"
        )

    # ------------------------------------------------------------------
    # Collision check
    # ------------------------------------------------------------------
    dest_dir = _skills_dir() / pack / name
    dest_file = dest_dir / "SKILL.md"

    if dest_file.exists() and not force:
        raise SkillImportError(
            f"Skill '{name}' is already installed at {dest_file}. "
            "Use --force to overwrite."
        )

    # ------------------------------------------------------------------
    # Write to disk
    # ------------------------------------------------------------------
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file.write_bytes(raw_bytes)
    logger.info("Skill '%s' written to %s", name, dest_file)

    # ------------------------------------------------------------------
    # Determine install vs update
    # ------------------------------------------------------------------
    # After writing, check if this was a re-install (force overwrite) or fresh.
    was_existing = force and dest_file.exists()  # approximate

    # ------------------------------------------------------------------
    # DB catalog upsert
    # ------------------------------------------------------------------
    source_url: str | None = source if source.startswith("https://") else None

    if pool is not None:
        await _upsert_catalog(
            pool=pool,
            name=name,
            pack=pack,
            source_url=source_url,
            license_id=license_id,
            description=description,
            import_hash=import_hash,
            prompt_count=prompt_count,
        )

    return {
        "ok": True,
        "name": name,
        "pack": pack,
        "path": str(dest_file),
        "license": license_id,
        "prompt_count": prompt_count,
        "installed": not was_existing,
        "updated": was_existing,
    }


async def list_skills(*, pool: Any = None) -> list[dict]:
    """Return all skills from the DB catalog, or from a disk scan.

    When ``pool`` is ``None``, walks the ``skills/`` directory and parses
    frontmatter from each ``SKILL.md`` it finds.
    """
    if pool is not None:
        return await _list_from_db(pool)
    return _list_from_disk()


async def remove_skill(name: str, *, pool: Any = None) -> dict:
    """Delete a skill's file and catalog row.

    Returns ``{ok, name, path}``.  Raises ``SkillImportError`` when the
    skill is not installed.
    """
    # Find the skill on disk (search all packs)
    skill_file: Path | None = None
    for candidate in _skills_dir().rglob("SKILL.md"):
        # Parse name from frontmatter to match
        try:
            raw = candidate.read_bytes()
            meta, _ = _parse_frontmatter(raw.decode("utf-8", errors="replace"))
            if meta.get("name") == name:
                skill_file = candidate
                break
        except Exception:  # noqa: BLE001
            continue

    if skill_file is None:
        raise SkillImportError(
            f"Skill '{name}' is not installed (no matching SKILL.md found)."
        )

    skill_dir = skill_file.parent
    skill_file.unlink()

    # Remove the parent directory if it's now empty
    try:
        skill_dir.rmdir()
    except OSError:
        pass  # not empty or already gone

    if pool is not None:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM skill_catalog WHERE name = $1",
                name,
            )

    logger.info("Skill '%s' removed from %s", name, skill_file)
    return {"ok": True, "name": name, "path": str(skill_file)}


# ---------------------------------------------------------------------------
# Private helpers — fetch
# ---------------------------------------------------------------------------


async def _fetch_url(url: str, *, http_client: Any = None) -> bytes:
    """Fetch raw bytes from a URL.

    Auto-converts GitHub blob URLs.  Creates a temporary ``httpx.AsyncClient``
    when ``http_client`` is ``None``.
    """
    import httpx  # lazy import

    url = _convert_github_blob_url(url)

    async def _get(client: httpx.AsyncClient) -> bytes:
        try:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Failed to fetch SKILL.md from {url}: "
                f"HTTP {exc.response.status_code}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Network error fetching SKILL.md from {url}: {exc}"
            ) from exc

    if http_client is not None:
        return await _get(http_client)

    async with httpx.AsyncClient(timeout=30.0) as client:
        return await _get(client)


async def _read_local(path: str) -> bytes:
    """Read raw bytes from a local file path."""
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"Local SKILL.md not found: {path}")
    try:
        return p.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"Could not read {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Private helpers — DB
# ---------------------------------------------------------------------------


async def _upsert_catalog(
    *,
    pool: Any,
    name: str,
    pack: str,
    source_url: str | None,
    license_id: str,
    description: str,
    import_hash: str,
    prompt_count: int,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO skill_catalog
                (name, pack, source_url, license, description,
                 import_hash, prompt_count, installed_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), NOW())
            ON CONFLICT (name) DO UPDATE
                SET pack         = EXCLUDED.pack,
                    source_url   = EXCLUDED.source_url,
                    license      = EXCLUDED.license,
                    description  = EXCLUDED.description,
                    import_hash  = EXCLUDED.import_hash,
                    prompt_count = EXCLUDED.prompt_count,
                    updated_at   = NOW()
            """,
            name,
            pack,
            source_url,
            license_id,
            description,
            import_hash,
            prompt_count,
        )


async def _list_from_db(pool: Any) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, name, pack, source_url, license, description,
                   import_hash, prompt_count,
                   installed_at, updated_at
              FROM skill_catalog
             ORDER BY pack, name
            """
        )
    return [dict(r) for r in rows]


def _list_from_disk() -> list[dict]:
    """Walk skills/ and parse each SKILL.md's frontmatter."""
    results = []
    for skill_file in sorted(_skills_dir().rglob("SKILL.md")):
        try:
            raw = skill_file.read_bytes()
            meta, _ = _parse_frontmatter(raw.decode("utf-8", errors="replace"))
            pack = skill_file.parent.parent.name
            results.append(
                {
                    "name": meta.get("name", skill_file.parent.name),
                    "pack": pack,
                    "license": meta.get("license", ""),
                    "description": meta.get("description", ""),
                    "prompt_count": len(
                        (meta.get("metadata") or {}).get("prompts") or []
                    ),
                    "path": str(skill_file),
                    "source_url": None,
                    "import_hash": hashlib.sha256(raw).hexdigest(),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not parse %s: %s", skill_file, exc)
    return results
