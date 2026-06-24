"""``services.experiment_runner`` — variant selection for the Phase 1 lab.

The single source of truth for "which variant should this task use?". Reads
the schema PR #699 landed (``experiments`` + ``experiment_variants``
tables + the ``capability_outcomes.variant_id`` FK) and returns a
:class:`ExperimentVariant` the writer-atom hook applies to LangGraph state.

Phase 1 selection model (per
``docs/architecture/2026-05-28-phase-1-variant-experiments-design.md``):

- **Uniform random** over the active variants of the niche's
  most-recent active experiment — the default. The ``weight`` column on
  ``experiment_variants`` is intentionally ignored on this path so the
  operator can read scorecard rollups without weighting bias.
- **Weighted random** (opt-in, #361 part 1): when the app_setting
  ``experiment_weighted_selection_enabled`` is true, allocation is
  proportional to ``ev.weight`` — the column the outcome→weight feedback
  loop (``services/router_outcome_feedback.py``) nudges from operator
  approve/reject. Default OFF keeps prod uniform until an operator opts in;
  the weight column is maintained either way, so only consumption is gated.
  Falls back to uniform when weights are all-zero or all-equal.
- **Task-level stickiness.** ``pick_variant`` is called ONCE per task
  (at writer-atom entry); the returned ``variant_id`` threads through
  every downstream atom via ``state["variant_id"]`` so the same task
  doesn't switch arms mid-pipeline.
- **One active experiment per niche** is enforced at the SQL level by
  the partial unique index PR #699 added. This module assumes that
  invariant and picks the single active row.

Production-grade reliability (from the design doc's
"Posture: testing in production" section, which is a binding constraint
on the harness):

- A misconfigured experiment, a missing pool, an inactive variant
  state, or any DB hiccup MUST NOT crash the writer atom. Every error
  path returns ``None``; the atom then uses the niche's production
  defaults — identical to the pre-PR behaviour. "Failure mode is
  nothing changes" is the contract.
- Per :func:`logger.warning`, exceptions are logged with full context
  (niche_slug, task_id, exception) so an operator scrolling logs can
  pinpoint a broken experiment immediately.

Public surface:

- :class:`ExperimentVariant` — frozen dataclass returned by
  :func:`pick_variant`. The writer-atom hook copies the override
  fields onto LangGraph state (only when non-None, preserving the
  scientific-method "one axis at a time" control).
- :func:`pick_variant` — async; returns ``None`` when no experiment is
  active or anything fails.

Future-deterministic-hashing note: the ``task_id`` parameter is currently
unused by the selection algorithm (``random.choice`` is non-deterministic).
The parameter is on the signature so a later Phase 1.5 change can swap
in ``hash(task_id) % n_variants`` for reproducible-per-task assignment
without touching every caller. Phase 1 stays with ``random.choice`` to
keep the read-only A/B harness simple; the scientific-method assertion
the operator wants comes from the SQL-level "one axis varying" + the
recorder stamping the held-constant axes on every row.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExperimentVariant:
    """A specific (prompt × model × RAG) configuration for a task to use.

    Returned by :func:`pick_variant`. Frozen so callers can pass it through
    multiple atoms without worrying about accidental mutation.

    Override semantics: every override field is ``None`` (or ``{}`` for
    ``rag_config``) when the variant inherits the niche's production
    default for that axis. The hook applies non-None overrides only —
    e.g. a model-axis variant sets ``writer_model`` and leaves
    ``prompt_template_key`` / ``rag_config`` empty so the scientific-
    method "one axis at a time" rule is enforced by the data, not the
    code.
    """

    variant_id: str
    """UUID of the selected ``experiment_variants`` row, as a string."""

    variant_label: str
    """Operator-facing label (e.g. ``'A'``, ``'gemma4-31b'``)."""

    experiment_id: str
    """UUID of the parent ``experiments`` row, as a string."""

    experiment_key: str
    """Operator-facing experiment key
    (e.g. ``'glad-labs/writer-model-gemma4-vs-qwen36-2026-05'``)."""

    prompt_template_key: str | None
    """When set, override ``state['prompt_template_key']``. ``None`` means
    inherit the niche default (held-constant axis)."""

    prompt_template_version: int | None
    """When set, override ``state['prompt_template_version']``. ``None``
    means inherit the niche default (held-constant axis)."""

    writer_model: str | None
    """When set, override the writer model resolution. ``None`` means
    inherit the niche default via :func:`services.llm_text.resolve_local_model`
    (the pipeline_writer_model pin)."""

    rag_config: dict[str, Any]
    """Shallow-merged INTO any niche-default rag_config when non-empty.
    ``{}`` means inherit the niche default (held-constant axis)."""


# SQL — pull the niche's active experiment + its active variants in one
# round-trip. Filtering on status='active' here matches the partial unique
# index PR #699 added (``idx_experiments_one_active_per_niche``) so the
# planner uses the index. The `LIMIT 1` is defensive — the unique index
# already guarantees at most one row, but a query-plan diff shouldn't
# silently return multiple if a future migration relaxes the index.
_PICK_ACTIVE_VARIANTS_SQL = """
SELECT
    e.id::text             AS experiment_id,
    e.key                  AS experiment_key,
    ev.id::text            AS variant_id,
    ev.label               AS variant_label,
    ev.prompt_template_key,
    ev.prompt_template_version,
    ev.writer_model,
    ev.rag_config,
    ev.weight
FROM experiments e
JOIN experiment_variants ev ON ev.experiment_id = e.id
WHERE e.niche_slug = $1
  AND e.status    = 'active'
  AND ev.active   = TRUE
ORDER BY e.activated_at DESC NULLS LAST, e.created_at DESC, ev.label
"""

# Phase 2 gate (#361 part 1). When this app_setting is true,
# :func:`pick_variant` allocates proportional to ``experiment_variants.weight``
# (which the outcome→weight feedback loop in ``router_outcome_feedback`` nudges)
# instead of uniform random. Default OFF preserves the Phase 1 uniform
# allocation so prod variant picking stays unbiased until an operator opts in.
_WEIGHTED_SELECTION_SETTING = "experiment_weighted_selection_enabled"


def _truthy(value: Any) -> bool:
    """Coerce an app_settings string/bool to a bool (``"true"`` -> True)."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


async def _weighted_selection_enabled(conn: Any) -> bool:
    """Read the ``experiment_weighted_selection_enabled`` flag off ``conn``.

    Best-effort — defaults to ``False`` (uniform allocation) on any miss or
    error so the Phase-1 behaviour is preserved unless explicitly opted in.
    """
    try:
        raw = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            _WEIGHTED_SELECTION_SETTING,
        )
    except Exception as exc:  # noqa: BLE001 — never let a config read break selection
        logger.debug(
            "[experiment_runner] weighted-selection flag read failed: %s — "
            "defaulting to uniform",
            exc,
        )
        return False
    return _truthy(raw)


def _weighted_choice(rows: list[Any]) -> Any:
    """Pick one row weighted by its ``weight`` column.

    Falls back to uniform :func:`random.choice` when weights are absent,
    non-positive, or all equal — so a misconfigured experiment degrades to
    the Phase-1 behaviour rather than crashing or starving a variant.
    """
    weights: list[float] = []
    for r in rows:
        try:
            w = float(r["weight"])
        except (KeyError, TypeError, ValueError):
            w = 0.0
        weights.append(max(0.0, w))
    total = sum(weights)
    if total <= 0.0 or len(set(weights)) <= 1:
        # No usable signal (all zero / all equal) — uniform.
        return random.choice(rows)
    return random.choices(rows, weights=weights, k=1)[0]


async def pick_variant(
    pool: Any,
    niche_slug: str,
    task_id: str,
) -> ExperimentVariant | None:
    """Pick a variant for ``task_id`` from the active experiment on ``niche_slug``.

    Phase 1 selection: uniform random over active variants of the
    niche's most-recent active experiment. Task-level stickiness is the
    caller's responsibility — call this ONCE per task (at writer-atom
    entry) and thread the returned ``variant_id`` through downstream
    atoms via ``state["variant_id"]``.

    Returns ``None`` (production path unchanged — atom uses niche defaults)
    when any of the following is true:

    - ``pool`` is ``None`` or the lookup raises (test / bootstrap path,
      or DB hiccup mid-flight).
    - ``niche_slug`` is falsy (legacy / manual tasks that don't carry a niche).
    - No row in ``experiments`` has ``status='active'`` for this niche.
    - The active experiment exists but every variant has ``active=false``
      (auto-paused, etc.).

    NEVER raises. The writer atom MUST treat ``pick_variant`` as
    best-effort; a runner bug cannot crash the production writer path
    (per the design doc's "Posture: testing in production" section).

    Args:
        pool: ``asyncpg.Pool`` (or test double). When ``None`` returns
            ``None`` without logging — the test/bootstrap path.
        niche_slug: The task's niche. Routes to that niche's active
            experiment (one active per niche enforced at the SQL layer).
        task_id: Currently unused by the selection algorithm; on the
            signature so Phase 1.5 can swap in deterministic
            ``hash(task_id) % n_variants`` without touching callers.

    Returns:
        An :class:`ExperimentVariant` to apply, or ``None`` for the
        no-variant production path.
    """
    if pool is None:
        return None
    if not niche_slug:
        return None
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_PICK_ACTIVE_VARIANTS_SQL, niche_slug)
            # Read the gate on the same connection while we hold it — one
            # extra cheap point-lookup, avoids a second acquire.
            weighted = await _weighted_selection_enabled(conn)
    except Exception as exc:  # noqa: BLE001 — fail-safe; never escape
        logger.warning(
            "[experiment_runner] pick_variant DB lookup failed "
            "(niche_slug=%r, task_id=%r): %s — falling back to no variant",
            niche_slug, task_id, exc,
        )
        return None

    if not rows:
        # No active experiment for this niche — production path unchanged.
        # Debug-level: this is the common path (most niches won't have an
        # experiment running), so logging at warning would be too noisy.
        logger.debug(
            "[experiment_runner] no active experiment for niche_slug=%r — "
            "task %s uses niche defaults",
            niche_slug, task_id,
        )
        return None

    # The ORDER BY (activated_at DESC, created_at DESC, label) groups all
    # variants of the single active experiment together — the partial
    # unique index guarantees only one experiment row, so every row in
    # ``rows`` shares the same (experiment_id, experiment_key).
    #
    # Selection model: uniform random by default (Phase 1, unbiased rollups).
    # When ``experiment_weighted_selection_enabled`` is true the pick is
    # proportional to ``ev.weight`` — the column the outcome→weight feedback
    # loop (``router_outcome_feedback``) nudges from operator approve/reject.
    # The weight COLUMN is always maintained; only its *consumption* here is
    # gated, so flipping the flag is the operator's explicit opt-in to
    # bandit-style allocation.
    chosen = _weighted_choice(rows) if weighted else random.choice(rows)

    try:
        rag_config_raw = chosen["rag_config"]
        # asyncpg returns JSONB as already-parsed dict; older paths
        # sometimes return str — handle both defensively.
        if isinstance(rag_config_raw, str):
            import json
            try:
                rag_config = json.loads(rag_config_raw)
                if not isinstance(rag_config, dict):
                    rag_config = {}
            except (ValueError, TypeError):
                rag_config = {}
        elif isinstance(rag_config_raw, dict):
            rag_config = dict(rag_config_raw)
        else:
            rag_config = {}

        variant = ExperimentVariant(
            variant_id=str(chosen["variant_id"]),
            variant_label=str(chosen["variant_label"]),
            experiment_id=str(chosen["experiment_id"]),
            experiment_key=str(chosen["experiment_key"]),
            prompt_template_key=chosen["prompt_template_key"],
            prompt_template_version=(
                int(chosen["prompt_template_version"])
                if chosen["prompt_template_version"] is not None else None
            ),
            writer_model=chosen["writer_model"],
            rag_config=rag_config,
        )
    except Exception as exc:  # noqa: BLE001 — fail-safe; never escape
        logger.warning(
            "[experiment_runner] pick_variant row decode failed "
            "(niche_slug=%r, task_id=%r): %s — falling back to no variant",
            niche_slug, task_id, exc,
        )
        return None

    logger.info(
        "[experiment_runner] niche=%s task=%s assigned variant %s "
        "(experiment=%s, model_override=%s, prompt_override=%s)",
        niche_slug, task_id[:8] if task_id else "?",
        variant.variant_label, variant.experiment_key,
        variant.writer_model or "<inherit>",
        f"{variant.prompt_template_key}/v{variant.prompt_template_version}"
        if variant.prompt_template_key else "<inherit>",
    )
    return variant


def apply_variant_to_state(
    state: dict[str, Any], variant: ExperimentVariant | None,
) -> dict[str, Any]:
    """Stamp variant fields onto LangGraph state in place.

    Shared helper so the writer-atom hook and the narrate-bundle hook
    apply variant overrides identically — keeping the
    one-axis-at-a-time discipline (per the design doc's
    scientific-method control section) in one place.

    Semantics:

    - ``variant_id`` / ``variant_label`` / ``experiment_id`` /
      ``experiment_key`` are always set when a variant is provided.
      Downstream observability (capability_outcomes, audit_log) reads
      these and stamps the variant on per-node telemetry rows.
    - Override knobs (``prompt_template_key``, ``prompt_template_version``,
      ``writer_model``) are applied ONLY when the variant explicitly
      sets them (non-None). A ``None`` field means "inherit niche
      default" — preserving the scientific-method "one axis at a time"
      rule.
    - ``rag_config`` shallow-merges INTO any existing ``state["rag_config"]``
      with variant values winning on key conflict. ``{}`` means inherit
      the niche default unchanged.

    No-op when ``variant`` is ``None`` — the production path stays
    identical to before this hook landed.

    Returns the mutated ``state`` dict so calls can chain.
    """
    if variant is None:
        return state
    state["variant_id"] = variant.variant_id
    state["variant_label"] = variant.variant_label
    state["experiment_id"] = variant.experiment_id
    state["experiment_key"] = variant.experiment_key
    if variant.prompt_template_key is not None:
        state["prompt_template_key"] = variant.prompt_template_key
    if variant.prompt_template_version is not None:
        state["prompt_template_version"] = variant.prompt_template_version
    if variant.writer_model is not None:
        state["writer_model"] = variant.writer_model
    if variant.rag_config:
        # Shallow merge — variant config wins on key conflict so a
        # variant testing snippet_limit=10 overrides the niche default
        # of 5 while leaving every other rag_config key unchanged.
        merged: dict[str, Any] = {}
        existing = state.get("rag_config")
        if isinstance(existing, dict):
            merged.update(existing)
        merged.update(variant.rag_config)
        state["rag_config"] = merged
    return state


__all__ = [
    "ExperimentVariant",
    "apply_variant_to_state",
    "pick_variant",
]
