"""Per-module migration runner — applies one ``Module``'s migrations.

Phase 2 of Module v1 (Glad-Labs/poindexter#490). Each ``Module``
package can ship a ``migrations/`` subdirectory whose files follow
the SAME convention as ``services/migrations/`` (Python files with
an ``async def up(pool)``). The runner walks them in alphabetical
order, records each in ``module_schema_migrations`` keyed on
``(module_name, migration_name)``, and skips already-applied
migrations on rerun.

Public API:

    await run_module_migrations(pool, module_name, migrations_dir)

Returns a ``ModuleMigrationResult`` summarising applied / skipped /
failed counts. Failures are LOGGED + COUNTED, never raised — one
broken migration in module A must not block module B's migrations.
This matches ``services/migrations/__init__.py``'s posture.

The substrate's ``module_schema_migrations`` table must exist
before this runner is called. The substrate migration shipped in
Phase 2 Task 1 creates it; the boot wiring in
``utils/startup_manager.py`` calls this runner AFTER substrate
``run_migrations`` so the ordering is automatic.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ModuleMigrationResult:
    """What ``run_module_migrations`` produced for one module."""

    module_name: str
    applied: int
    skipped: int
    failed: int

    @property
    def ok(self) -> bool:
        """True if every migration either applied cleanly or was already
        applied. False if any failed."""
        return self.failed == 0


async def run_module_migrations(
    pool: Any,
    module_name: str,
    migrations_dir: Path,
) -> ModuleMigrationResult:
    """Apply every migration in ``migrations_dir`` for ``module_name``.

    Args:
        pool: asyncpg pool. The substrate ``module_schema_migrations``
            table must already exist.
        module_name: the canonical module slug (matches
            ``ModuleManifest.name`` — caller is responsible for
            passing it).
        migrations_dir: ``Path`` to the module's migrations directory.
            If it doesn't exist, the runner logs at INFO and returns
            a no-op result (a module without migrations is normal).

    Returns:
        ``ModuleMigrationResult`` with per-module counters.
    """
    if not migrations_dir.exists() or not migrations_dir.is_dir():
        logger.info(
            "module_migrations: %s has no migrations/ directory at %s — "
            "nothing to apply",
            module_name, migrations_dir,
        )
        return ModuleMigrationResult(
            module_name=module_name, applied=0, skipped=0, failed=0,
        )

    migration_files = sorted(
        [
            f for f in migrations_dir.glob("*.py")
            if f.name != "__init__.py" and not f.name.startswith("_")
        ]
    )

    if not migration_files:
        logger.info("module_migrations: %s has no migration files", module_name)
        return ModuleMigrationResult(
            module_name=module_name, applied=0, skipped=0, failed=0,
        )

    logger.info(
        "module_migrations: %s — found %d migration file(s)",
        module_name, len(migration_files),
    )

    applied = 0
    skipped = 0
    failed = 0

    for migration_file in migration_files:
        migration_name = migration_file.name
        try:
            async with pool.acquire() as conn:
                already_applied = await conn.fetchval(
                    "SELECT id FROM module_schema_migrations "
                    "WHERE module_name = $1 AND migration_name = $2",
                    module_name, migration_name,
                )

            if already_applied:
                logger.debug(
                    "module_migrations: skipping (already applied): %s/%s",
                    module_name, migration_name,
                )
                skipped += 1
                continue

            spec = importlib.util.spec_from_file_location(
                f"{module_name}_migration_{migration_name[:-3]}",
                migration_file,
            )
            if spec is None or spec.loader is None:
                logger.warning(
                    "module_migrations: cannot load %s/%s — invalid spec",
                    module_name, migration_name,
                )
                failed += 1
                continue
            migration_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration_module)

            up_fn = getattr(migration_module, "up", None)
            if up_fn is None or not callable(up_fn):
                logger.warning(
                    "module_migrations: %s/%s missing async up(pool) — "
                    "skipping",
                    module_name, migration_name,
                )
                failed += 1
                continue

            logger.info(
                "module_migrations: applying %s/%s",
                module_name, migration_name,
            )
            await up_fn(pool)  # type: ignore[misc]  # dynamic import — Pyright can't see signature

            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO module_schema_migrations "
                    "(module_name, migration_name) VALUES ($1, $2) "
                    "ON CONFLICT (module_name, migration_name) DO NOTHING",
                    module_name, migration_name,
                )

            logger.info(
                "module_migrations: applied %s/%s",
                module_name, migration_name,
            )
            applied += 1

        except Exception:
            logger.error(
                "module_migrations: failed %s/%s",
                module_name, migration_name, exc_info=True,
            )
            failed += 1

    logger.info(
        "module_migrations: %s done — applied=%d skipped=%d failed=%d",
        module_name, applied, skipped, failed,
    )
    return ModuleMigrationResult(
        module_name=module_name, applied=applied, skipped=skipped, failed=failed,
    )


__all__ = ["ModuleMigrationResult", "run_module_migrations"]
