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

    Walks ``services.atoms`` for ATOM_META declarations, then
    surfaces every registered Stage plugin as a virtual atom so the
    architect-LLM sees the full composable surface (real atoms +
    existing stages) in one catalog. Future packages with atoms
    (e.g. business-ops atoms in Phase 5) get added here as additional
    package paths.
    """
    global _DISCOVERED
    if _DISCOVERED:
        return
    _walk_package("services.atoms")
    _surface_stages_as_atoms()
    _DISCOVERED = True
    logger.info(
        "[atom_registry] discovered %d atom(s): %s",
        len(_ATOMS), sorted(_ATOMS.keys()),
    )


def _surface_stages_as_atoms() -> None:
    """Lift each registered Stage plugin into the atom catalog as a
    virtual atom prefixed ``stage.<name>``.

    Virtual atoms have auto-derived metadata (name, description from
    class docstring, halts_on_failure → cost_class hint) and a runner
    that calls ``stage.execute(context, config)`` so the architect
    can compose them just like real atoms. The make_stage_node path
    in :mod:`services.template_runner` is the canonical executor —
    we route through it so virtual atoms get the same plugin-config
    + timeout treatment as native StageRunner runs.
    """
    try:
        from plugins.registry import get_core_samples, get_stages
    except Exception as exc:  # noqa: BLE001
        logger.warning("[atom_registry] could not import plugins.registry: %s", exc)
        return

    stages_by_name: dict[str, Any] = {}
    try:
        for stage in get_stages():  # entry-point-discovered stages
            name = getattr(stage, "name", None)
            if name:
                stages_by_name[name] = stage
    except Exception as exc:  # noqa: BLE001
        logger.debug("[atom_registry] get_stages() failed: %s", exc)

    try:
        for stage in get_core_samples().get("stages", []):
            name = getattr(stage, "name", None)
            if name and name not in stages_by_name:
                stages_by_name[name] = stage
    except Exception as exc:  # noqa: BLE001
        logger.debug("[atom_registry] get_core_samples() stages failed: %s", exc)

    surfaced = 0
    for stage_name, stage in stages_by_name.items():
        atom_slug = f"stage.{stage_name}"
        if atom_slug in _ATOMS:
            continue
        meta = _stage_to_atom_meta(stage_name, stage)
        if meta is None:
            continue
        _ATOMS[atom_slug] = meta
        _RUNNERS[atom_slug] = _make_stage_runner(stage)
        surfaced += 1
    if surfaced:
        logger.info(
            "[atom_registry] surfaced %d Stage plugin(s) as virtual atoms",
            surfaced,
        )


def _stage_to_atom_meta(stage_name: str, stage: Any) -> AtomMeta | None:
    """Auto-derive AtomMeta for a Stage plugin.

    Description prefers the class docstring's first paragraph; falls
    back to the stage's ``description`` attribute, then to a generic
    blurb. capability_tier defaults to None — stages don't declare
    one because they reach into model_router themselves.
    """
    from plugins.atom import RetryPolicy

    cls = type(stage)
    doc = (cls.__doc__ or "").strip()
    if doc:
        # First paragraph as description.
        description = doc.split("\n\n", 1)[0].replace("\n", " ").strip()
    else:
        description = getattr(stage, "description", "") or (
            f"Legacy Stage plugin {stage_name!r} surfaced as a virtual atom."
        )

    halts = bool(getattr(stage, "halts_on_failure", True))
    timeout_s = int(getattr(stage, "timeout_seconds", 120) or 120)

    return AtomMeta(
        name=f"stage.{stage_name}",
        type="stage",
        version=getattr(stage, "version", "1.0.0") or "1.0.0",
        description=description[:500],  # keep prompt budget tight
        inputs=(),         # stages share the LangGraph state dict
        outputs=(),
        requires=(),
        produces=(),
        capability_tier=None,
        cost_class="compute",
        idempotent=False,
        side_effects=(f"stage timeout {timeout_s}s",),
        retry=RetryPolicy(max_attempts=1),
        fallback=(),
        parallelizable=False,
    ) if halts is not None else None  # always returns; halts kept for clarity


def _make_stage_runner(stage: Any) -> Callable[..., Any]:
    """Build the atom-runner shim for a virtual stage atom.

    Calls ``services.template_runner.make_stage_node`` to get the
    canonical LangGraph node wrapper, then unwraps it as a plain
    awaitable that accepts the state dict — same contract real atoms
    expose. We resolve pool from ``state['database_service']`` because
    that's how stages already get their pool today.
    """

    async def runner(state: dict[str, Any]) -> dict[str, Any]:
        from services.template_runner import make_stage_node

        db = state.get("database_service")
        pool = getattr(db, "pool", None) if db else None
        node = make_stage_node(stage, pool, record_sink=None)
        return await node(state)  # type: ignore[arg-type]

    runner.__name__ = f"virtual_atom_runner_{getattr(stage, 'name', 'unknown')}"
    return runner


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
    """Render the atom catalog as an LLM-optimized prompt fragment.

    Per ``feedback_design_for_llm_consumers``: this is NOT human
    documentation. It's a structured prompt fragment the architect-LLM
    reads when composing pipelines. Format priorities (in order):

    1. Token-efficient — bullet-shaped, no headings beyond the atom
       name line, no marketing copy.
    2. Contract-shaped — every atom has a single-line PURPOSE then
       INPUTS / OUTPUTS / TIER blocks the LLM can ground on.
    3. Composition-friendly — REQUIRES + PRODUCES surface as
       compose-time hints so the architect can reason about which
       atom to chain after which.
    4. Stable — same atom always renders identically across runs so
       the LLM can build template-matching intuition over time.
    """
    discover()
    lines: list[str] = []
    for meta in list_atoms():
        # Single-line atom header: name + version + type + tier + cost
        header_bits = [f"{meta.name} v{meta.version}", meta.type]
        if meta.capability_tier:
            header_bits.append(f"tier={meta.capability_tier}")
        header_bits.append(f"cost={meta.cost_class}")
        if meta.idempotent:
            header_bits.append("idempotent")
        if meta.parallelizable:
            header_bits.append("parallelizable")
        lines.append("- " + " | ".join(header_bits))
        # PURPOSE: single line. Avoid prose paragraphs in catalog.
        purpose = " ".join(meta.description.split())[:300]
        lines.append(f"  PURPOSE: {purpose}")
        # INPUTS: terse {name}:{type}({R|O}) so the architect can scan.
        if meta.inputs:
            sigs = ", ".join(
                f"{f.name}:{f.type}({'R' if f.required else 'O'})"
                for f in meta.inputs
            )
            lines.append(f"  INPUTS: {sigs}")
        else:
            lines.append("  INPUTS: (reads from shared LangGraph state)")
        # OUTPUTS: same shape as inputs.
        if meta.outputs:
            sigs = ", ".join(f"{f.name}:{f.type}" for f in meta.outputs)
            lines.append(f"  OUTPUTS: {sigs}")
        else:
            lines.append("  OUTPUTS: (writes to shared LangGraph state)")
        # Composition hints — what this atom needs upstream + what it
        # produces for downstream. The architect uses these to chain.
        if meta.requires:
            lines.append(f"  REQUIRES: {', '.join(meta.requires)}")
        if meta.produces:
            lines.append(f"  PRODUCES: {', '.join(meta.produces)}")
        # FALLBACK chain (for tier-resolved atoms only).
        if meta.fallback:
            lines.append(f"  FALLBACK: {' -> '.join(meta.fallback)}")
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
