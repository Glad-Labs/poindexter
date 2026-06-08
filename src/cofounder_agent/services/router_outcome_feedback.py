"""``router_outcome_feedback`` — outcome → experiment-variant weight loop.

Glad-Labs/poindexter#361 Part 1. Closes the loop between the operator's
approve/reject verdict and the experiment-variant weights the router
learns from.

When an operator approves or rejects a task, :func:`record_task_outcome`:

1. Backfills ``atom_runs.decision`` (+ optional ``quality_score`` /
   ``edit_distance``) for every row of that task — covering BOTH the
   approve and reject paths. Before this loop the reject path had no
   backfill, so a rejected run's atoms carried a NULL ``decision`` and
   never became negative training signal. (The approve path was already
   backfilled by ``modules/content/auto_publish_gate.py``; this is the
   second writer of the same column and composes via the existing
   ``COALESCE`` semantics in :func:`record_atom_run_outcome`.)
2. Finds the experiment variant(s) the task's graph used (via
   ``capability_outcomes.variant_id``) and nudges
   ``experiment_variants.weight`` with an EWMA toward the binary outcome
   signal (``1.0`` approved, ``0.0`` rejected). One outcome can't swing a
   weight wildly because of the ``alpha`` damping.

The ``weight`` *column* is updated regardless of whether weighted
selection is enabled — so the data shifts as soon as outcomes arrive. The
*consumption* of those weights by ``experiment_runner.pick_variant`` is
gated separately behind ``experiment_weighted_selection_enabled`` (default
off), so prod variant picking stays uniform until an operator opts in.

Best-effort everywhere: this runs inside the approval path, so a DB
hiccup or misconfiguration logs + returns a summary dict and NEVER raises
back into approve/reject (per ``feedback_human_approval`` — the operator's
verdict must land even if the learning loop is down).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# EWMA damping. ``new = (1 - alpha) * old + alpha * signal``. Lower = more
# inertia (one outcome moves the weight less). Operator-tunable via the
# ``router_feedback_alpha`` app_setting (seeded 0.2).
_DEFAULT_ALPHA = 0.2

# Clamp bounds so a long reject streak can't drive a variant to exactly 0
# (which would make weighted selection ignore it forever) and an approve
# streak can't exceed the 1.0 ceiling the binary signal implies.
_WEIGHT_MIN = 0.01
_WEIGHT_MAX = 1.0


def _signal_for(decision: str) -> float | None:
    """Map an operator decision token to the EWMA target signal.

    ``approved`` → 1.0, ``rejected`` → 0.0. Returns ``None`` for any other
    token (e.g. ``dismissed`` / ``revised``) so the caller skips the weight
    nudge but still does the atom_runs backfill — an ambiguous verdict
    shouldn't be coerced into a positive or negative training signal.
    """
    norm = (decision or "").strip().lower()
    if norm in ("approved", "approve"):
        return 1.0
    if norm in ("rejected", "reject"):
        return 0.0
    return None


async def _resolve_alpha(pool: Any, site_config: Any) -> float:
    """Read ``router_feedback_alpha`` (DB-first), clamped to (0, 1].

    Prefers ``site_config`` (in-memory cache) when supplied; falls back to
    a direct ``app_settings`` read on the pool so the loop works even from
    surfaces that don't thread a SiteConfig. Defaults to
    :data:`_DEFAULT_ALPHA` on any miss or parse failure.
    """
    raw: Any = None
    if site_config is not None:
        try:
            raw = site_config.get("router_feedback_alpha", None)
        except Exception:  # noqa: BLE001 — never let config reads break the loop
            raw = None
    if raw in (None, "") and pool is not None:
        try:
            async with pool.acquire() as conn:
                raw = await conn.fetchval(
                    "SELECT value FROM app_settings WHERE key = 'router_feedback_alpha'",
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[router_feedback] alpha DB read failed: %s", exc)
            raw = None
    if raw is None or raw == "":
        alpha = _DEFAULT_ALPHA
    else:
        try:
            alpha = float(raw)
        except (TypeError, ValueError):
            alpha = _DEFAULT_ALPHA
    # Guard against a nonsense setting bricking the math.
    if not (0.0 < alpha <= 1.0):
        alpha = _DEFAULT_ALPHA
    return alpha


def ewma(old_weight: float, signal: float, alpha: float) -> float:
    """Compute the damped, clamped next weight.

    ``new = (1 - alpha) * old + alpha * signal``, clamped to
    ``[_WEIGHT_MIN, _WEIGHT_MAX]``. Pure helper so the math is unit-testable
    without a pool.
    """
    new = (1.0 - alpha) * old_weight + alpha * signal
    return max(_WEIGHT_MIN, min(_WEIGHT_MAX, new))


async def record_task_outcome(
    *,
    pool: Any,
    task_id: str,
    decision: str,
    quality_score: float | None = None,
    edit_distance: int | None = None,
    site_config: Any = None,
) -> dict[str, Any]:
    """Attribute an operator approve/reject to the variant(s) the task used.

    Backfills ``atom_runs`` outcome columns for the whole task, then nudges
    ``experiment_variants.weight`` via EWMA for every variant the task's
    graph ran under.

    Args:
        pool: asyncpg pool (or test double). ``None`` → no-op summary.
        task_id: the ``pipeline_tasks`` / ``capability_outcomes`` task id.
        decision: ``'approved'`` | ``'rejected'`` (other tokens still
            backfill atom_runs but skip the weight nudge).
        quality_score: optional, backfilled onto atom_runs when provided.
        edit_distance: optional, backfilled onto atom_runs when provided.
        site_config: optional SiteConfig for the alpha lookup.

    Returns:
        ``{"ok", "task_id", "decision", "variants_updated", "atom_rows_backfilled"}``.
        Best-effort — NEVER raises. On error returns ``ok=False`` with an
        ``error`` key so the caller can log without the loop breaking
        approve/reject.
    """
    summary: dict[str, Any] = {
        "ok": True,
        "task_id": task_id,
        "decision": decision,
        "variants_updated": [],
        "atom_rows_backfilled": 0,
    }
    if pool is None or not task_id:
        summary["ok"] = False
        summary["error"] = "missing pool or task_id"
        return summary

    # --- 1. atom_runs backfill (covers approve AND reject) -----------------
    try:
        from services.atom_runs import record_atom_run_outcome

        backfilled = await record_atom_run_outcome(
            pool,
            task_id=str(task_id),
            decision=decision,
            quality_score=quality_score,
            edit_distance=edit_distance,
        )
        summary["atom_rows_backfilled"] = int(backfilled or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[router_feedback] atom_runs backfill failed for task %s: %s",
            task_id, exc,
        )

    # --- 2. weight nudge per variant the task used ------------------------
    signal = _signal_for(decision)
    if signal is None:
        # Ambiguous decision — backfill happened, but don't manufacture a
        # positive/negative training signal from it.
        logger.debug(
            "[router_feedback] decision %r is not approve/reject — "
            "skipping weight nudge for task %s",
            decision, task_id,
        )
        return summary

    try:
        alpha = await _resolve_alpha(pool, site_config)
        async with pool.acquire() as conn:
            variant_rows = await conn.fetch(
                """
                SELECT DISTINCT variant_id
                  FROM capability_outcomes
                 WHERE task_id = $1
                   AND variant_id IS NOT NULL
                """,
                str(task_id),
            )
            for vrow in variant_rows:
                variant_id = vrow["variant_id"]
                if variant_id is None:
                    continue
                old_weight_raw = await conn.fetchval(
                    "SELECT weight FROM experiment_variants WHERE id = $1::uuid",
                    str(variant_id),
                )
                if old_weight_raw is None:
                    # Variant row gone (cleanup) — skip silently.
                    continue
                old_weight = float(old_weight_raw)
                new_weight = ewma(old_weight, signal, alpha)
                await conn.execute(
                    "UPDATE experiment_variants SET weight = $2 WHERE id = $1::uuid",
                    str(variant_id),
                    new_weight,
                )
                summary["variants_updated"].append(
                    {
                        "variant_id": str(variant_id),
                        "old_weight": round(old_weight, 6),
                        "new_weight": round(new_weight, 6),
                        "signal": signal,
                        "alpha": alpha,
                    }
                )
        if summary["variants_updated"]:
            logger.info(
                "[router_feedback] task %s decision=%s nudged %d variant weight(s) "
                "(alpha=%.3f)",
                task_id, decision, len(summary["variants_updated"]), alpha,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[router_feedback] variant weight nudge failed for task %s: %s",
            task_id, exc,
        )
        summary["ok"] = False
        summary["error"] = f"{type(exc).__name__}: {exc}"

    return summary


__all__ = ["ewma", "record_task_outcome"]
