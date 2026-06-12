"""``poindexter skills`` — operator commands for the skill importer.

Wraps ``services.skill_importer`` so the operator can:

- ``poindexter skills import <source> [--pack imported] [--force]``
  — fetch, validate, and install a SKILL.md
- ``poindexter skills list [--json]``
  — show all installed skills (DB catalog or disk scan)
- ``poindexter skills remove <name>``
  — delete a skill with confirmation
- ``poindexter skills update <name>``
  — re-fetch from the source_url recorded in the catalog

All commands work without a DB pool (disk-only mode); the DB upsert /
delete is silently skipped when the DSN is unavailable and ``--no-db``
is not required — just ``pool=None`` flows to disk-only paths.
"""

from __future__ import annotations

import asyncio
import json as _json

import click

from poindexter.cli._bootstrap import resolve_dsn as _dsn

# ---------------------------------------------------------------------------
# Group root
# ---------------------------------------------------------------------------


@click.group(
    name="skills",
    help="Operator commands for the skill importer (poindexter#529).",
)
def skills_group() -> None:
    pass


# ---------------------------------------------------------------------------
# skills import
# ---------------------------------------------------------------------------


@skills_group.command("import")
@click.argument("source")
@click.option("--pack", default="imported", show_default=True, help="Pack subdirectory.")
@click.option("--force", is_flag=True, default=False, help="Overwrite if already installed.")
def import_skill_cmd(source: str, pack: str, force: bool) -> None:
    """Install a SKILL.md from a URL or local path.

    SOURCE may be:

    \b
      - A local file path:        /path/to/SKILL.md
      - An HTTPS URL:             https://example.com/SKILL.md
      - A GitHub blob URL:        https://github.com/user/repo/blob/main/SKILL.md
        (auto-converted to raw.githubusercontent.com)
    """

    async def _impl() -> None:
        import asyncpg

        from services.skill_importer import SkillImportError, import_skill

        pool = None
        site_config = None
        try:
            dsn = _dsn()
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
            from poindexter.cli._lifecycle import container_for_cli

            async with container_for_cli(pool) as container:
                site_config = container.site_config
        except Exception:  # noqa: BLE001
            # Disk-only mode — pool + site_config stay None
            pass

        try:
            result = await import_skill(
                source,
                pack=pack,
                pool=pool,
                site_config=site_config,
                force=force,
            )
        except SkillImportError as exc:
            click.echo(f"Error: {exc}", err=True)
            raise SystemExit(1) from exc
        except RuntimeError as exc:
            click.echo(f"Error: {exc}", err=True)
            raise SystemExit(1) from exc
        finally:
            if pool is not None:
                await pool.close()

        action = "Updated" if result["updated"] else "Installed"
        click.echo(f"{action}: {result['name']}")
        click.echo(f"  Pack:     {result['pack']}")
        click.echo(f"  License:  {result['license']}")
        click.echo(f"  Prompts:  {result['prompt_count']}")
        click.echo(f"  Path:     {result['path']}")

    asyncio.run(_impl())


# ---------------------------------------------------------------------------
# skills list
# ---------------------------------------------------------------------------


@skills_group.command("list")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON.")
def list_skills_cmd(as_json: bool) -> None:
    """List all installed skills."""

    async def _impl() -> None:
        import asyncpg

        from services.skill_importer import list_skills

        pool = None
        try:
            dsn = _dsn()
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        except Exception:  # noqa: BLE001
            pass

        try:
            skills = await list_skills(pool=pool)
        finally:
            if pool is not None:
                await pool.close()

        if as_json:
            click.echo(_json.dumps(skills, indent=2, default=str))
            return

        if not skills:
            click.echo("No skills installed.")
            return

        click.echo(f"{'Name':<30} {'Pack':<12} {'License':<14} {'Prompts':>7}")
        click.echo("-" * 66)
        for s in skills:
            click.echo(
                f"{s['name']:<30} {s['pack']:<12} {s.get('license', ''):<14} "
                f"{s.get('prompt_count', 0):>7}"
            )

    asyncio.run(_impl())


# ---------------------------------------------------------------------------
# skills remove
# ---------------------------------------------------------------------------


@skills_group.command("remove")
@click.argument("name")
@click.confirmation_option(prompt="Are you sure you want to remove this skill?")
def remove_skill_cmd(name: str) -> None:
    """Remove an installed skill by name."""

    async def _impl() -> None:
        import asyncpg

        from services.skill_importer import SkillImportError, remove_skill

        pool = None
        try:
            dsn = _dsn()
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        except Exception:  # noqa: BLE001
            pass

        try:
            result = await remove_skill(name, pool=pool)
        except SkillImportError as exc:
            click.echo(f"Error: {exc}", err=True)
            raise SystemExit(1) from exc
        finally:
            if pool is not None:
                await pool.close()

        click.echo(f"Removed: {result['name']}")
        click.echo(f"  Path: {result['path']}")

    asyncio.run(_impl())


# ---------------------------------------------------------------------------
# skills update
# ---------------------------------------------------------------------------


@skills_group.command("update")
@click.argument("name")
def update_skill_cmd(name: str) -> None:
    """Re-fetch a skill from its recorded source_url.

    Requires a DB connection (source_url is stored in skill_catalog).
    """

    async def _impl() -> None:
        import asyncpg

        from services.skill_importer import SkillImportError, import_skill

        try:
            dsn = _dsn()
        except RuntimeError as exc:
            click.echo(f"Error: {exc}", err=True)
            raise SystemExit(1) from exc

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT source_url, pack FROM skill_catalog WHERE name = $1",
                    name,
                )
            if row is None:
                click.echo(
                    f"Error: skill '{name}' is not in the catalog. "
                    "Run 'poindexter skills list' to see installed skills.",
                    err=True,
                )
                raise SystemExit(1)

            source_url = row["source_url"]
            pack = row["pack"]

            if not source_url:
                click.echo(
                    f"Error: skill '{name}' has no source_url recorded "
                    "(it was installed from a local file). "
                    "Re-import from the original path instead.",
                    err=True,
                )
                raise SystemExit(1)

            from poindexter.cli._lifecycle import container_for_cli

            async with container_for_cli(pool) as container:
                site_config = container.site_config

            result = await import_skill(
                source_url,
                pack=pack,
                pool=pool,
                site_config=site_config,
                force=True,
            )
        except SkillImportError as exc:
            click.echo(f"Error: {exc}", err=True)
            raise SystemExit(1) from exc
        except RuntimeError as exc:
            click.echo(f"Error: {exc}", err=True)
            raise SystemExit(1) from exc
        finally:
            await pool.close()

        click.echo(f"Updated: {result['name']}")
        click.echo(f"  Pack:     {result['pack']}")
        click.echo(f"  License:  {result['license']}")
        click.echo(f"  Prompts:  {result['prompt_count']}")
        click.echo(f"  Path:     {result['path']}")

    asyncio.run(_impl())


__all__ = ["skills_group"]
