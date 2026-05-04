"""Atom registry — discovery + cache of all ``ATOM_META`` declarations.

Walks ``services.atoms.*`` (and a future-extensible list of additional
modules) at startup, collects ``ATOM_META: AtomMeta`` from each, and
exposes the catalog as:

- :func:`list_atoms` — list of AtomMeta in registration order
- :func:`get_atom_meta` — lookup by name
- :func:`get_atom_callable` — lookup by name, returns the callable
  ``run`` function for execution

Phase 2 of the dynamic-pipeline-composition spec — the architect-LLM
queries this catalog to know what blocks exist when composing novel
pipelines.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Issue: Glad-Labs/poindexter#360.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any, Callable

from plugins.atom import AtomMeta

logger = logging.getLogger(__name__)


_ATOMS: dict[str, AtomMeta] = {}
_RUNNERS: dict[str, Callable[..., Any]] = {}
_DISCOVERED = False


def _walk_package(pkg_name: str) -> None:
    """Import every module under ``pkg_name`` so module-level
    ``ATOM_META`` constants are evaluated."""
    try:
        pkg = importlib.import_module(pkg_name)
    except Exception as exc:
        logger.warning("[atom_registry] could not import %s: %s", pkg_name, exc)
        return

    pkg_path = getattr(pkg, "__path__", None)
    if not pkg_path:
        return

    for module_info in pkgutil.iter_modules(pkg_path):
        if module_info.name.startswith("_"):
            continue
        full_name = f"{pkg_name}.{module_info.name}"
        try:
            module = importlib.import_module(full_name)
        except Exception as exc:
            logger.warning(
                "[atom_registry] failed to import %s: %s", full_name, exc,
            )
            continue
        meta = getattr(module, "ATOM_META", None)
        if not isinstance(meta, AtomMeta):
            continue
        run = getattr(module, "run", None)
        if not callable(run):
            logger.warning(
                "[atom_registry] %s declared ATOM_META but has no callable "
                "`run` — skipping",
                full_name,
            )
            continue
        if meta.name in _ATOMS:
            logger.warning(
                "[atom_registry] duplicate atom name %r — keeping first "
                "(existing module: ?, new module: %s)",
                meta.name, full_name,
            )
            continue
        _ATOMS[meta.name] = meta
        _RUNNERS[meta.name] = run
        logger.debug(
            "[atom_registry] registered %s (v%s) from %s",
            meta.name, meta.version, full_name,
        )


def discover() -> None:
    """Run discovery exactly once. Idempotent across multiple calls.

    Currently walks ``services.atoms``. Future packages with atoms
    (e.g. business-ops atoms in Phase 5) get added here as additional
    package paths.
    """
    global _DISCOVERED
    if _DISCOVERED:
        return
    _walk_package("services.atoms")
    _DISCOVERED = True
    logger.info(
        "[atom_registry] discovered %d atom(s): %s",
        len(_ATOMS), sorted(_ATOMS.keys()),
    )


def list_atoms() -> list[AtomMeta]:
    """Return all registered atoms in alphabetical order by name."""
    discover()
    return [_ATOMS[k] for k in sorted(_ATOMS.keys())]


def get_atom_meta(name: str) -> AtomMeta | None:
    """Lookup atom metadata by globally-unique name slug."""
    discover()
    return _ATOMS.get(name)


def get_atom_callable(name: str) -> Callable[..., Any] | None:
    """Lookup the atom's executable ``run`` function by name."""
    discover()
    return _RUNNERS.get(name)


def to_catalog_text() -> str:
    """Render the atom catalog as a compact text block for the
    architect-LLM prompt. Each atom gets one block: name, description,
    inputs, outputs, capability_tier, cost_class.
    """
    discover()
    lines: list[str] = []
    for meta in list_atoms():
        lines.append(f"## {meta.name} (v{meta.version})")
        lines.append(meta.description.strip())
        if meta.inputs:
            lines.append("Inputs:")
            for f in meta.inputs:
                req = "required" if f.required else "optional"
                lines.append(f"  - {f.name}: {f.type} ({req})")
        if meta.outputs:
            lines.append("Outputs:")
            for f in meta.outputs:
                lines.append(f"  - {f.name}: {f.type}")
        if meta.capability_tier:
            lines.append(f"Capability tier: {meta.capability_tier}")
        lines.append(f"Cost class: {meta.cost_class}")
        if meta.requires:
            lines.append(f"Requires preconditions: {', '.join(meta.requires)}")
        lines.append("")
    return "\n".join(lines)


async def sync_to_db(pool: Any) -> int:
    """Write-through every discovered ``ATOM_META`` to ``pipeline_atoms``.

    Idempotent — called from app startup after :func:`discover`. Returns
    the number of rows upserted. Stamps ``last_seen_at`` to NOW() on
    every row so operators can spot dead atoms (rows older than the
    latest sweep timestamp).

    Best-effort: a transient DB failure logs and returns 0 rather than
    blocking startup. The Python registry is the source of truth; this
    table is just a query convenience.
    """
    discover()
    if not _ATOMS:
        return 0
    try:
        async with pool.acquire() as conn:
            n = 0
            for meta in _ATOMS.values():
                await conn.execute(
                    """
                    INSERT INTO pipeline_atoms
                      (name, type, version, description,
                       capability_tier, cost_class, meta, last_seen_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, NOW())
                    ON CONFLICT (name) DO UPDATE SET
                      type             = EXCLUDED.type,
                      version          = EXCLUDED.version,
                      description      = EXCLUDED.description,
                      capability_tier  = EXCLUDED.capability_tier,
                      cost_class       = EXCLUDED.cost_class,
                      meta             = EXCLUDED.meta,
                      last_seen_at     = NOW(),
                      updated_at       = NOW()
                    """,
                    meta.name, meta.type, meta.version, meta.description,
                    meta.capability_tier, meta.cost_class,
                    _json_dumps(meta.to_jsonb()),
                )
                n += 1
            logger.info("[atom_registry] synced %d atom(s) to pipeline_atoms", n)
            return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("[atom_registry] sync_to_db failed: %s", exc)
        return 0


def _json_dumps(payload: dict[str, Any]) -> str:
    import json as _json
    return _json.dumps(payload, default=str)


__all__ = [
    "discover",
    "get_atom_callable",
    "get_atom_meta",
    "list_atoms",
    "sync_to_db",
    "to_catalog_text",
]
