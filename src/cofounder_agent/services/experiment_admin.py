"""``services.experiment_admin`` — operator CRUD for the Phase 1 lab harness.

The transport-agnostic owner of the experiment-admin operations that
``poindexter experiments`` exposes: list / create / add-variant /
activate / scorecard / conclude over the ``experiments`` +
``experiment_variants`` tables and the ``experiment_variant_scorecard_v1``
view. Extracted from the CLI per the transport-adapter contract
(``docs/architecture/2026-06-10-transport-adapter-contract.md``, epic
#1340 / guard #1344): the CLI is a thin adapter that opens a pool, calls
one of these functions, formats the result, and translates the exceptions
below into ``click.ClickException``. A future ``/api/experiments`` route
would call the same functions and map the same exceptions to 4xx — one
owning surface, no SQL in the adapters.

Contrast with :mod:`services.experiment_runner`, the *runtime* variant
sampler, which is deliberately fail-safe (never raises — "failure mode is
nothing changes"). This module is the opposite: operator commands that
**must** reject bad input loudly, so each validation failure raises
:class:`ExperimentAdminError` (or the :class:`ActiveExperimentConflict`
subclass for the one-active-per-niche pre-flight). Messages are
transport-neutral — no command names or flag spellings leak in; the
adapter composes those.

The design constraints enforced here are unchanged from the CLI's
original implementation (see PR #699 schema + #702 runner):

- **One active experiment per niche** — :func:`activate_experiment`
  pre-flights the partial unique index so the operator gets a friendly
  conflict instead of an asyncpg ``UniqueViolationError``.
- **Add-variant gated on draft status** — only ``draft`` experiments take
  new variants; the runner's allocation is locked once activated.
- **Activate requires >= 2 variants** — uniform random over a singleton
  always picks the same arm.
- **Conclude records but does not promote** — Phase 1 manual promotion;
  this persists the winner + note, the adapter prints next-step guidance.

The asyncpg / NicheService imports are intentionally lazy (inside the
functions) — matching the CLI idiom that lets tests patch them via
``sys.modules`` / attribute patching without a real DB.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Errors — the service's transport-agnostic failure contract.
# ---------------------------------------------------------------------------


class ExperimentAdminError(Exception):
    """An operator-facing rejection of an experiment-admin operation.

    Carries a human-readable, transport-neutral message. The CLI re-raises
    it as ``click.ClickException(str(exc))``; an HTTP route would map it to
    a 4xx with ``str(exc)`` as the detail.
    """


class ActiveExperimentConflict(ExperimentAdminError):
    """The niche already has an active experiment (so the partial unique
    index ``idx_experiments_one_active_per_niche`` would reject the flip).

    Raised by :func:`activate_experiment`. Carries the structured conflict
    (``niche_slug`` + ``conflict_key``) so the adapter can compose its own
    remediation hint (e.g. the CLI's ``poindexter experiments conclude
    <conflict_key> ...`` suggestion) rather than baking transport-specific
    text into the service.
    """

    def __init__(self, niche_slug: str, conflict_key: str) -> None:
        self.niche_slug = niche_slug
        self.conflict_key = conflict_key
        super().__init__(f"niche {niche_slug!r} already has an active experiment: {conflict_key!r}")


# ---------------------------------------------------------------------------
# SQL — static statements as module constants (matching experiment_runner).
# The ``list_experiments`` predicate is built per-call (optional filters),
# so its SQL is assembled in the function rather than pinned here.
# ---------------------------------------------------------------------------

_INSERT_EXPERIMENT_SQL = """
INSERT INTO experiments (key, niche_slug, description, objective_function)
VALUES ($1, $2, $3, $4)
RETURNING id::text AS id
"""

_SELECT_EXPERIMENT_ID_STATUS_SQL = "SELECT id::text AS id, status FROM experiments WHERE key = $1"

_INSERT_VARIANT_SQL = """
INSERT INTO experiment_variants (
    experiment_id, label, weight,
    prompt_template_key, prompt_template_version,
    writer_model, rag_config
) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
RETURNING id::text AS id
"""

_SELECT_EXPERIMENT_FOR_ACTIVATE_SQL = """
SELECT id::text AS id, status, niche_slug,
       (SELECT COUNT(*) FROM experiment_variants ev
        WHERE ev.experiment_id = e.id) AS variant_count
FROM experiments e WHERE key = $1
"""

_SELECT_ACTIVE_CONFLICT_SQL = """
SELECT key FROM experiments
WHERE niche_slug = $1 AND status = 'active'
LIMIT 1
"""

_UPDATE_ACTIVATE_SQL = """
UPDATE experiments
SET status = 'active', activated_at = NOW()
WHERE id = $1::uuid
"""

_SELECT_EXPERIMENT_FULL_SQL = """
SELECT key, niche_slug, status, objective_function,
       description, created_at, activated_at,
       concluded_at, winner_variant_label, conclusion_note
FROM experiments WHERE key = $1
"""

_SELECT_SCORECARD_SQL = """
SELECT
    variant_label, variant_active, paused_at, paused_reason,
    posts_attempted, posts_approved, approval_rate_pct,
    avg_edit_distance_pct, avg_views_24h, avg_views_7d,
    avg_cost_per_post, total_cost
FROM experiment_variant_scorecard_v1
WHERE experiment_key = $1
ORDER BY approval_rate_pct DESC NULLS LAST, variant_label
"""

_SELECT_EXPERIMENT_FOR_CONCLUDE_SQL = """
SELECT id::text AS id, status, niche_slug
FROM experiments WHERE key = $1
"""

_SELECT_VARIANT_SQL = """
SELECT label, writer_model,
       prompt_template_key, prompt_template_version
FROM experiment_variants
WHERE experiment_id = $1::uuid AND label = $2
"""

_SELECT_VARIANT_LABELS_SQL = """
SELECT label FROM experiment_variants
WHERE experiment_id = $1::uuid ORDER BY label
"""

_UPDATE_CONCLUDE_SQL = """
UPDATE experiments
SET status = 'concluded',
    concluded_at = NOW(),
    winner_variant_label = $2,
    conclusion_note = $3
WHERE id = $1::uuid
"""


# ---------------------------------------------------------------------------
# Operations.
# ---------------------------------------------------------------------------


async def list_experiments(
    pool: Any,
    *,
    status: str | None = None,
    niche: str | None = None,
) -> list[dict[str, Any]]:
    """Return experiments (most-recent first) with variant + outcome counts.

    Optional ``status`` / ``niche`` filters narrow the set. Each row carries
    key / niche_slug / status / objective_function / timestamps /
    winner_variant_label plus ``variant_count`` and ``outcome_count``.
    """
    where = ["TRUE"]
    args: list[Any] = []
    if status:
        args.append(status)
        where.append(f"e.status = ${len(args)}")
    if niche:
        args.append(niche)
        where.append(f"e.niche_slug = ${len(args)}")
    sql = f"""
    SELECT
        e.key,
        e.niche_slug,
        e.status,
        e.objective_function,
        e.created_at,
        e.activated_at,
        e.concluded_at,
        e.winner_variant_label,
        (
            SELECT COUNT(*) FROM experiment_variants ev
            WHERE ev.experiment_id = e.id
        ) AS variant_count,
        (
            SELECT COUNT(*) FROM capability_outcomes co
            JOIN experiment_variants ev2 ON ev2.id = co.variant_id
            WHERE ev2.experiment_id = e.id
        ) AS outcome_count
    FROM experiments e
    WHERE {" AND ".join(where)}
    ORDER BY e.created_at DESC
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def create_experiment(
    pool: Any,
    *,
    key: str,
    niche_slug: str,
    description: str,
    objective: str,
) -> str:
    """Create a draft experiment; return its new id (as text).

    Raises :class:`ExperimentAdminError` for an unknown niche (checked via
    :class:`services.niche_service.NicheService` before the INSERT) or a
    duplicate ``key`` (the UNIQUE constraint, surfaced cleanly).
    """
    import asyncpg

    from services.niche_service import NicheService

    n = await NicheService(pool).get_by_slug(niche_slug)
    if not n:
        raise ExperimentAdminError(f"unknown niche: {niche_slug}")
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                _INSERT_EXPERIMENT_SQL,
                key,
                niche_slug,
                description,
                objective,
            )
    except asyncpg.UniqueViolationError as exc:
        raise ExperimentAdminError(
            f"experiment {key!r} already exists (UNIQUE key violation)"
        ) from exc
    return row["id"]


async def add_variant(
    pool: Any,
    *,
    key: str,
    label: str,
    weight: float,
    prompt_template_key: str | None,
    prompt_template_version: int | None,
    writer_model: str | None,
    rag_config: dict[str, Any],
) -> str:
    """Add a variant to a draft experiment; return its new id (as text).

    ``rag_config`` is the already-parsed dict (the adapter validates the
    raw ``--rag-config`` JSON before calling). Raises
    :class:`ExperimentAdminError` for an unknown experiment, a non-draft
    experiment, or a duplicate ``label`` within the experiment.
    """
    import json

    import asyncpg

    async with pool.acquire() as conn:
        exp = await conn.fetchrow(_SELECT_EXPERIMENT_ID_STATUS_SQL, key)
        if exp is None:
            raise ExperimentAdminError(f"unknown experiment: {key}")
        if exp["status"] != "draft":
            raise ExperimentAdminError(
                f"experiment {key!r} is {exp['status']!r}; "
                "only draft experiments can take new variants"
            )
        try:
            row = await conn.fetchrow(
                _INSERT_VARIANT_SQL,
                exp["id"],
                label,
                weight,
                prompt_template_key,
                prompt_template_version,
                writer_model,
                json.dumps(rag_config),
            )
        except asyncpg.UniqueViolationError as exc:
            raise ExperimentAdminError(
                f"variant label {label!r} already exists on experiment {key!r}"
            ) from exc
    return row["id"]


async def activate_experiment(pool: Any, *, key: str) -> None:
    """Flip a draft experiment to active (stamps ``activated_at = NOW()``).

    Raises :class:`ExperimentAdminError` for an unknown experiment, a
    non-draft experiment, or fewer than 2 variants; raises
    :class:`ActiveExperimentConflict` (carrying the conflicting key) when
    the niche already has an active experiment.
    """
    async with pool.acquire() as conn:
        exp = await conn.fetchrow(_SELECT_EXPERIMENT_FOR_ACTIVATE_SQL, key)
        if exp is None:
            raise ExperimentAdminError(f"unknown experiment: {key}")
        if exp["status"] != "draft":
            raise ExperimentAdminError(
                f"experiment {key!r} is {exp['status']!r}; only draft experiments can be activated"
            )
        if exp["variant_count"] < 2:
            raise ExperimentAdminError(
                f"experiment {key!r} has only {exp['variant_count']} "
                "variant(s); need >=2 to activate "
                "(one-variant experiment is meaningless)"
            )
        # Pre-flight the "one active per niche" rule with a structured
        # conflict before the UNIQUE index would surface as a
        # UniqueViolationError.
        conflict = await conn.fetchval(_SELECT_ACTIVE_CONFLICT_SQL, exp["niche_slug"])
        if conflict:
            raise ActiveExperimentConflict(exp["niche_slug"], conflict)
        await conn.execute(_UPDATE_ACTIVATE_SQL, exp["id"])


async def get_scorecard(
    pool: Any,
    *,
    key: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return ``(experiment, variant_rows)`` for the scorecard render.

    ``experiment`` is the full experiments row; ``variant_rows`` are the
    ``experiment_variant_scorecard_v1`` rows sorted by approval-rate desc
    (NULLs last). Raises :class:`ExperimentAdminError` for an unknown key.
    """
    async with pool.acquire() as conn:
        exp = await conn.fetchrow(_SELECT_EXPERIMENT_FULL_SQL, key)
        if exp is None:
            raise ExperimentAdminError(f"unknown experiment: {key}")
        rows = await conn.fetch(_SELECT_SCORECARD_SQL, key)
    return dict(exp), [dict(r) for r in rows]


async def conclude_experiment(
    pool: Any,
    *,
    key: str,
    winner: str,
    note: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Mark an experiment concluded and record the winning variant.

    Returns ``(experiment, winning_variant)`` so the adapter can print
    next-step promotion guidance from the variant's override columns.
    Raises :class:`ExperimentAdminError` for an unknown experiment, an
    already-concluded experiment, or a ``winner`` that matches no variant
    (the message lists the defined labels).
    """
    async with pool.acquire() as conn:
        exp = await conn.fetchrow(_SELECT_EXPERIMENT_FOR_CONCLUDE_SQL, key)
        if exp is None:
            raise ExperimentAdminError(f"unknown experiment: {key}")
        if exp["status"] == "concluded":
            raise ExperimentAdminError(f"experiment {key!r} is already concluded")
        variant = await conn.fetchrow(_SELECT_VARIANT_SQL, exp["id"], winner)
        if variant is None:
            labels = await conn.fetch(_SELECT_VARIANT_LABELS_SQL, exp["id"])
            raise ExperimentAdminError(
                f"winner {winner!r} does not match any variant on {key!r}. "
                f"Defined variants: {[r['label'] for r in labels]}"
            )
        await conn.execute(_UPDATE_CONCLUDE_SQL, exp["id"], winner, note)
    return dict(exp), dict(variant)


__all__ = [
    "ActiveExperimentConflict",
    "ExperimentAdminError",
    "activate_experiment",
    "add_variant",
    "conclude_experiment",
    "create_experiment",
    "get_scorecard",
    "list_experiments",
]
