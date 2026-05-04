"""``capability_outcomes`` — outcome feedback loop writer.

Phase 2 of the dynamic-pipeline-composition spec. Every TemplateRunner
run records one row per node into the ``capability_outcomes`` table
so the router (and future capability_router) can learn which
(atom, capability_tier, model) combinations produce good results in
production.

This module is the writer half. The reader half lives in
``services.model_router`` (and will move to ``capability_router`` in
the planned rename).

Public surface:

- :func:`record_run` — accepts a TemplateRunSummary + the initial
  state, writes one row per node. Best-effort — DB failures don't
  bubble up because the outcome log is observational, not load-bearing.
- :func:`record_one` — single-row writer for code paths that build
  outcomes inline (e.g. an atom that wants to log its own per-call
  outcome separate from the template summary).

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Issue: Glad-Labs/poindexter#358.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def record_run(
    pool: Any,
    summary: Any,
    initial_state: dict[str, Any] | None = None,
) -> int:
    """Insert one row per record in ``summary.records``. Returns rows
    written. Best-effort — exceptions are logged and swallowed so the
    feedback writer never breaks the pipeline.
    """
    if pool is None or summary is None or not getattr(summary, "records", None):
        return 0
    state = initial_state or {}
    task_id = str(state.get("task_id") or "") or None
    template_slug = getattr(summary, "template_slug", "") or ""

    # Best-effort lookup of the atom catalog so we can stamp
    # capability_tier alongside the resolved model_used. The atom
    # registry is already populated at startup; this is a memory hit.
    atoms_by_name: dict[str, Any] = {}
    try:
        from services import atom_registry
        atoms_by_name = {m.name: m for m in atom_registry.list_atoms()}
    except Exception:
        atoms_by_name = {}

    written = 0
    try:
        async with pool.acquire() as conn:
            for r in summary.records:
                # Heuristic atom name lookup: stage nodes use the stage
                # name, atom nodes prefix with the namespaced atom slug.
                # The registry holds the full names; match by suffix.
                node_name = getattr(r, "name", "") or ""
                atom_meta = atoms_by_name.get(node_name)
                if atom_meta is None:
                    # Try common prefixes the architect emits.
                    for cand in (f"atoms.{node_name}", f"stage.{node_name}"):
                        if cand in atoms_by_name:
                            atom_meta = atoms_by_name[cand]
                            break
                atom_name = getattr(atom_meta, "name", None) if atom_meta else None
                capability_tier = (
                    getattr(atom_meta, "capability_tier", None) if atom_meta else None
                )

                # Pull model + quality from per-node metrics when present.
                metrics = getattr(r, "metrics", {}) or {}
                model_used = (
                    metrics.get("model_used")
                    or metrics.get("model")
                    or state.get("model_used")
                    or None
                )
                quality_score = metrics.get("quality_score") or state.get("quality_score")
                if isinstance(quality_score, str):
                    try:
                        quality_score = float(quality_score)
                    except ValueError:
                        quality_score = None

                await conn.execute(
                    """
                    INSERT INTO capability_outcomes
                      (task_id, template_slug, node_name, atom_name,
                       capability_tier, model_used,
                       ok, halted, failure_reason, elapsed_ms,
                       quality_score, metrics)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
                    """,
                    task_id, template_slug, node_name, atom_name,
                    capability_tier, model_used,
                    bool(getattr(r, "ok", False)),
                    bool(getattr(r, "halted", False)),
                    (getattr(r, "detail", "") if not getattr(r, "ok", False) else None),
                    int(getattr(r, "elapsed_ms", 0) or 0),
                    quality_score,
                    json.dumps(metrics, default=str),
                )
                written += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("[capability_outcomes] record_run failed: %s", exc)
    return written


async def record_one(
    pool: Any,
    *,
    task_id: str | None,
    template_slug: str,
    node_name: str,
    atom_name: str | None = None,
    capability_tier: str | None = None,
    model_used: str | None = None,
    ok: bool = True,
    halted: bool = False,
    failure_reason: str | None = None,
    elapsed_ms: int = 0,
    quality_score: float | None = None,
    metrics: dict[str, Any] | None = None,
) -> bool:
    """Single-row writer for ad-hoc outcome logging (e.g. from inside
    an atom that wants to record an extra-fine-grained event). Returns
    True on success. Best-effort.
    """
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO capability_outcomes
                  (task_id, template_slug, node_name, atom_name,
                   capability_tier, model_used,
                   ok, halted, failure_reason, elapsed_ms,
                   quality_score, metrics)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
                """,
                task_id, template_slug, node_name, atom_name,
                capability_tier, model_used,
                ok, halted, failure_reason, elapsed_ms, quality_score,
                json.dumps(metrics or {}, default=str),
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[capability_outcomes] record_one failed: %s", exc)
        return False


__all__ = ["record_one", "record_run"]
