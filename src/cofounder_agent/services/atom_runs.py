"""``atom_runs`` — per-atom run + outcome capture for composed pipelines.

Glad-Labs/poindexter#355 atom-cutover Plan 2. When a pipeline runs as a
composed graph_def (``build_graph_from_spec``), every node appends a
``TemplateRunRecord`` to the run's ``record_sink``. :func:`persist_atom_runs`
writes one ``atom_runs`` row per record — the (composition -> outcome)
substrate for #361 and a future composition-learning architect.

Complementary to ``capability_outcomes`` (which scores
``(atom, tier, model)`` for the router): ``atom_runs`` adds a per-invocation
``run_id`` (groups all atoms of one run), input/output state-key *digests*
(the composition shape), ``cost``/``retries``, and the full outcome join
(``post_id`` / approval ``decision`` / ``edit_distance``) backfilled by
:func:`record_atom_run_outcome` after the human-approval gate resolves.

Both writers are best-effort: capture is observational, never load-bearing,
so a DB error here is logged + swallowed — it must never fail content
generation. The per-run capture is gated by
``app_settings.atom_runs_capture_enabled`` (seeded ``true``) read through the
run-bound ``SiteConfig``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _truthy(value: Any) -> bool:
    """Coerce an app_settings string/bool to a bool (``"true"`` -> True)."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _status_of(record: Any) -> str:
    """Map a TemplateRunRecord's flags to one status token.

    Precedence: skipped -> halted -> error -> ok. ``halted`` covers a node
    that requested a graph halt (e.g. QA reject); ``error`` is a not-ok
    record that didn't explicitly halt; ``ok`` is the success path.
    """
    if getattr(record, "skipped", False):
        return "skipped"
    if getattr(record, "halted", False):
        return "halted"
    if not getattr(record, "ok", False):
        return "error"
    return "ok"


def _capture_enabled(site_config: Any) -> bool:
    if site_config is None:
        return True
    return _truthy(site_config.get("atom_runs_capture_enabled", "true"))


def _catalog_by_name() -> dict[str, Any]:
    """Best-effort atom catalog for capability_tier stamping (memory hit,
    mirrors capability_outcomes.record_run)."""
    try:
        from services import atom_registry
        return {m.name: m for m in atom_registry.list_atoms()}
    except Exception:  # noqa: BLE001
        return {}


def digest_keys(keys: Any) -> str:
    """Stable short digest of a collection of state keys — the composition
    'shape' at an atom boundary. Sorted (order-independent); sha256
    truncated to 16 hex chars (enough to dedupe shapes, cheap to store)."""
    norm = ",".join(sorted(str(k) for k in (keys or [])))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


async def persist_atom_runs(
    pool: Any,
    *,
    run_id: str,
    task_id: str | None,
    template_slug: str,
    records: list[Any],
    site_config: Any = None,
) -> int:
    """Write one ``atom_runs`` row per record in ``records``.

    Returns rows written. Gated by ``atom_runs_capture_enabled`` (via
    ``site_config``; default-on when no site_config is passed). Best-effort —
    exceptions are logged + swallowed so capture never breaks the pipeline.
    """
    if pool is None or not records:
        return 0
    if not _capture_enabled(site_config):
        return 0

    catalog = _catalog_by_name()
    written = 0
    try:
        async with pool.acquire() as conn:
            for seq, r in enumerate(records):
                atom = getattr(r, "name", "") or ""
                meta = catalog.get(atom)
                if meta is None:
                    for cand in (f"atoms.{atom}", f"stage.{atom}"):
                        if cand in catalog:
                            meta = catalog[cand]
                            break
                tier = getattr(meta, "capability_tier", None) if meta else None

                metrics = getattr(r, "metrics", {}) or {}
                node_id = getattr(r, "node_id", None) or metrics.get("node_id")
                model = metrics.get("model_used") or metrics.get("model")
                cost = metrics.get("cost")
                retries = int(metrics.get("retries", 0) or 0)
                input_keys = metrics.get("input_keys")
                output_keys = metrics.get("output_keys")
                input_digest = metrics.get("input_digest")
                output_digest = metrics.get("output_digest")

                await conn.execute(
                    """
                    INSERT INTO atom_runs
                      (run_id, task_id, template_slug, seq, atom, node_id,
                       tier, model, latency_ms, cost, retries, status,
                       input_digest, output_digest, input_keys, output_keys,
                       metrics)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                            $12, $13, $14, $15, $16, $17::jsonb)
                    """,
                    run_id, task_id, template_slug, seq, atom, node_id,
                    tier, model, int(getattr(r, "elapsed_ms", 0) or 0),
                    cost, retries, _status_of(r),
                    input_digest, output_digest,
                    list(input_keys) if input_keys is not None else None,
                    list(output_keys) if output_keys is not None else None,
                    json.dumps(metrics, default=str),
                )
                written += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("[atom_runs] persist_atom_runs failed: %s", exc)
    return written


async def record_atom_run_outcome(
    pool: Any,
    *,
    task_id: str,
    run_id: str | None = None,
    post_id: str | None = None,
    decision: str | None = None,
    quality_score: float | None = None,
    edit_distance: int | None = None,
) -> int:
    """Backfill the outcome columns on a run's ``atom_runs`` rows after the
    approval gate resolves. Returns rows updated. Best-effort.

    Keyed on ``task_id`` (the whole run shares one outcome); pass ``run_id``
    to scope to a specific invocation when a task was re-run. ``COALESCE``
    keeps any previously-written non-null value so a partial update (e.g.
    quality_score now, decision later) composes.
    """
    if pool is None or not task_id:
        return 0
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE atom_runs SET
                    post_id       = COALESCE($3::uuid, post_id),
                    decision      = COALESCE($4, decision),
                    quality_score = COALESCE($5, quality_score),
                    edit_distance = COALESCE($6, edit_distance)
                WHERE task_id = $1
                  AND ($2::text IS NULL OR run_id = $2)
                """,
                task_id, run_id, post_id, decision, quality_score, edit_distance,
            )
        # asyncpg returns a status string like "UPDATE 3" — parse the count.
        try:
            return int(str(result).split()[-1])
        except (ValueError, IndexError):
            return 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("[atom_runs] record_atom_run_outcome failed: %s", exc)
        return 0


__all__ = ["digest_keys", "persist_atom_runs", "record_atom_run_outcome"]
