"""A/B experiment harness — declare, assign, record, conclude.

The codebase already has the pieces required for A/B testing
(``qa_workflow_*`` configs in ``app_settings``, swappable
``pipeline_writer_model``, content scoring metrics) but no harness to
actually declare an experiment and route requests through it. This
service is that harness.

Lifecycle
---------

1. Operator declares an experiment via :meth:`ExperimentService.create`.
   The experiment starts in ``draft`` status; a separate UPDATE flips
   it to ``running`` when ready (handled by the CLI / dashboard, not
   this service — keeping ``create`` side-effect-light).
2. The relevant pipeline ``Stage`` calls :meth:`assign` with
   ``subject_id=task_id`` (or whatever the experiment's
   ``assignment_field`` resolves to in context). The returned variant
   key drives the Stage's branching logic.
3. After the Stage finishes, it calls :meth:`record_outcome` to
   attribute the resulting score / cost / duration back to the
   assignment row.
4. The operator inspects :meth:`summary` and, when satisfied, calls
   :meth:`conclude` to record the winning variant. Promoting the
   winning config into production ``app_settings`` is a deliberate
   manual step (no auto-promotion — wins should be reviewed).

Assignment math
---------------

Sticky assignment uses
``blake2b(f"{experiment_key}:{subject_id}".encode()).digest()`` —
the first 4 bytes interpreted as a big-endian unsigned int, modulo
100, dropped into the cumulative weight buckets defined by the
experiment's variants. We use BLAKE2b (not SHA-1, which Bandit B324
correctly flags as broken for security uses); this is a uniform-hash
need, not a security need, but BLAKE2b is faster than SHA-1, just as
uniform, and available in stdlib without a dependency. See
GitHub issue Glad-Labs/poindexter#307.

Statistical significance testing is deliberately out of scope. This
module is plumbing; once data starts flowing, a follow-up can add a
``compare`` method that runs the appropriate t-test / chi-square /
etc. on the persisted ``metrics`` rows.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


# Validation slack: integer-rounded weights might land at 99 or 101 for
# a three-way 33/33/34 split, etc. We accept anything in [98, 102] so
# the operator doesn't fight schema math; outside that range is almost
# certainly a typo and we want to fail loud (per CLAUDE.md "no silent
# defaults").
_WEIGHT_SUM_MIN = 98
_WEIGHT_SUM_MAX = 102

_VALID_STATUSES = frozenset({"draft", "running", "paused", "complete"})


class ExperimentService:
    """A/B experiment harness — declarative experiments, sticky
    assignment, outcome recording.

    Backed by the ``experiments`` and ``experiment_assignments`` tables
    (migration 0097). The service holds no in-memory state; every
    method round-trips to the DB so multiple workers can share an
    experiment without coordinating.

    DI seam: takes ``site_config`` + ``pool`` in ``__init__``. Never
    reaches for module-level singletons. Tests construct their own
    instance with a stub pool; production constructs at startup with
    the canonical pool from ``app.state.pool``.
    """

    def __init__(self, *, site_config: Any, pool: Any) -> None:
        self._site_config = site_config
        self._pool = pool

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_variants(variants: Any) -> list[dict[str, Any]]:
        """Sanity-check the variants list at create() time.

        Raises ``ValueError`` (a programmer error) on malformed input —
        no silent fallback. Validates:

        - is a list / tuple of dicts
        - has at least 2 entries
        - each entry has non-empty ``key``, integer-ish ``weight``, and
          a ``config`` mapping (may be empty)
        - keys are unique within the experiment
        - weights sum to within ``[_WEIGHT_SUM_MIN, _WEIGHT_SUM_MAX]``
        """
        if not isinstance(variants, (list, tuple)):
            raise ValueError(
                f"variants must be a list, got {type(variants).__name__}"
            )
        if len(variants) < 2:
            raise ValueError(
                f"experiments need at least 2 variants, got {len(variants)}"
            )

        normalized: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for idx, variant in enumerate(variants):
            if not isinstance(variant, dict):
                raise ValueError(
                    f"variant[{idx}] must be a dict, got "
                    f"{type(variant).__name__}"
                )
            key = variant.get("key")
            if not key or not isinstance(key, str):
                raise ValueError(
                    f"variant[{idx}] missing required 'key' (non-empty str)"
                )
            if key in seen_keys:
                raise ValueError(
                    f"variant[{idx}] duplicate key {key!r}; variant keys "
                    "must be unique within an experiment"
                )
            seen_keys.add(key)

            if "weight" not in variant:
                raise ValueError(
                    f"variant[{idx}] ({key!r}) missing required 'weight'"
                )
            try:
                weight = int(variant["weight"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"variant[{idx}] ({key!r}) weight must be an int, "
                    f"got {variant['weight']!r}"
                ) from exc
            if weight < 0:
                raise ValueError(
                    f"variant[{idx}] ({key!r}) weight must be ≥ 0, "
                    f"got {weight}"
                )

            if "config" not in variant:
                raise ValueError(
                    f"variant[{idx}] ({key!r}) missing required 'config' "
                    "(may be an empty dict)"
                )
            config = variant["config"]
            if not isinstance(config, dict):
                raise ValueError(
                    f"variant[{idx}] ({key!r}) config must be a dict, "
                    f"got {type(config).__name__}"
                )

            normalized.append({"key": key, "weight": weight, "config": config})

        total = sum(v["weight"] for v in normalized)
        if not (_WEIGHT_SUM_MIN <= total <= _WEIGHT_SUM_MAX):
            raise ValueError(
                f"variant weights must sum to ~100 (within "
                f"[{_WEIGHT_SUM_MIN}, {_WEIGHT_SUM_MAX}]); got {total}"
            )
        return normalized

    @staticmethod
    def _hash_subject(experiment_key: str, subject_id: str) -> int:
        """Map (experiment, subject) → integer in [0, 100).

        BLAKE2b first-four-bytes mod 100. Stable across processes /
        OS / Python versions because we go through ``hashlib`` rather
        than relying on ``hash()`` (which is salted per process).

        BLAKE2b — not SHA-1 — because Bandit B324 (HIGH, CWE-327)
        correctly flags SHA-1 as broken for security uses. Bucketing
        is not a security use (any uniform hash works), but B324 is
        a blanket "no SHA-1" rule and BLAKE2b is a strict upgrade:
        cryptographically strong, faster than SHA-1, stdlib, drop-in.
        Only the first 4 bytes are consumed, so digest_size doesn't
        matter for the bucketing math — we use the default. See
        GitHub issue Glad-Labs/poindexter#307.
        """
        digest = hashlib.blake2b(
            f"{experiment_key}:{subject_id}".encode("utf-8")
        ).digest()
        return int.from_bytes(digest[:4], "big") % 100

    @classmethod
    def _pick_variant(
        cls,
        *,
        experiment_key: str,
        subject_id: str,
        variants: list[dict[str, Any]],
    ) -> str:
        """Resolve a hash bucket to a variant key via cumulative weights.

        Walks the variants in declared order, accumulating weight; the
        first bucket whose cumulative range covers the hash wins. The
        last variant catches any rounding overflow (i.e. when weights
        sum to 99 or 101 the last variant absorbs the gap).
        """
        bucket = cls._hash_subject(experiment_key, subject_id)
        cumulative = 0
        for variant in variants:
            cumulative += int(variant["weight"])
            if bucket < cumulative:
                return str(variant["key"])
        # Fallthrough — weights summed to <= bucket. Only possible when
        # weights total < 100 (within the [98, 100) slack range). Catch
        # to the last variant so we never return None on a running
        # experiment.
        return str(variants[-1]["key"])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        key: str,
        description: str,
        variants: list[dict[str, Any]],
        assignment_field: str = "task_id",
        status: str = "draft",
    ) -> str:
        """Create a new experiment row. Returns the new experiment id.

        Validates the variants list (≥2 entries, weights sum to ≈100,
        each entry has key/weight/config). Status defaults to ``draft``
        — the operator flips to ``running`` separately when ready.
        """
        if not key or not isinstance(key, str):
            raise ValueError("experiment key must be a non-empty str")
        if not description or not isinstance(description, str):
            raise ValueError("experiment description must be a non-empty str")
        if status not in _VALID_STATUSES:
            raise ValueError(
                f"invalid status {status!r}; must be one of "
                f"{sorted(_VALID_STATUSES)}"
            )
        normalized = self._validate_variants(variants)

        # ``started_at`` only set if the operator skipped draft and went
        # straight to running. ``completed_at`` only set by conclude().
        started_at_sql = "NOW()" if status == "running" else "NULL"

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO experiments
                    (key, description, status, variants, assignment_field,
                     started_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, {started_at_sql})
                RETURNING id::text AS id
                """,
                key,
                description,
                status,
                json.dumps(normalized),
                assignment_field,
            )
        if row is None:
            # Should be unreachable — INSERT ... RETURNING always yields
            # a row on success, and asyncpg raises on insert failure.
            # Fail loud per CLAUDE.md "no silent defaults".
            raise RuntimeError(
                f"experiment create returned no row for key={key!r}"
            )
        logger.info(
            "[EXPERIMENTS] created %r (id=%s, status=%s, %d variants)",
            key,
            row["id"],
            status,
            len(normalized),
        )
        return str(row["id"])

    async def list_running(self) -> list[dict[str, Any]]:
        """Every experiment with status='running'.

        Used by the assignment helper / dashboards. Returns dicts
        rather than asyncpg Records so callers don't have to import
        asyncpg; ``variants`` is already-decoded JSON.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id::text AS id, key, description, status,
                       variants, assignment_field,
                       created_at, started_at
                FROM experiments
                WHERE status = 'running'
                ORDER BY started_at DESC NULLS LAST, created_at DESC
                """,
            )
        return [self._row_to_dict(r) for r in rows]

    async def assign(
        self,
        *,
        experiment_key: str,
        subject_id: str,
    ) -> str | None:
        """Pick a variant for ``subject_id``. Sticky + idempotent.

        Returns the variant key, or ``None`` if the experiment isn't
        running (unknown / draft / paused / complete). When ``None`` is
        returned the calling Stage should fall through to its default
        (un-experimented) behavior.

        The same ``(experiment_key, subject_id)`` pair always yields
        the same variant — re-calls hit the UNIQUE constraint and are
        no-ops at the DB layer.
        """
        async with self._pool.acquire() as conn:
            exp = await conn.fetchrow(
                """
                SELECT id::text AS id, key, status, variants
                FROM experiments
                WHERE key = $1
                """,
                experiment_key,
            )
            if exp is None:
                logger.warning(
                    "[EXPERIMENTS] assign(): unknown experiment %r — "
                    "returning None so caller falls back to default",
                    experiment_key,
                )
                return None
            if exp["status"] != "running":
                logger.debug(
                    "[EXPERIMENTS] assign(): experiment %r status=%s — "
                    "no assignment",
                    experiment_key,
                    exp["status"],
                )
                return None

            variants = self._decode_variants(exp["variants"])
            if not variants:
                # Should be unreachable — create() rejects empty
                # variants — but defending against a hand-edited row.
                logger.warning(
                    "[EXPERIMENTS] assign(): experiment %r has no "
                    "variants — returning None",
                    experiment_key,
                )
                return None

            variant_key = self._pick_variant(
                experiment_key=experiment_key,
                subject_id=str(subject_id),
                variants=variants,
            )

            # Sticky: ON CONFLICT DO NOTHING means a second call with
            # the same subject is idempotent. We still RETURNING so we
            # can detect the case where a prior assignment already
            # exists with a different variant_key (the hash should be
            # deterministic, but a hand-edited variants array could
            # rebucket; we honor the existing assignment in that case).
            inserted = await conn.fetchrow(
                """
                INSERT INTO experiment_assignments
                    (experiment_id, subject_id, variant_key)
                VALUES ($1::uuid, $2, $3)
                ON CONFLICT (experiment_id, subject_id) DO NOTHING
                RETURNING variant_key
                """,
                exp["id"],
                str(subject_id),
                variant_key,
            )
            if inserted is None:
                # Conflict — the row already existed. Read the prior
                # assignment so the caller sees the historical variant,
                # not the freshly-computed one.
                existing = await conn.fetchrow(
                    """
                    SELECT variant_key
                    FROM experiment_assignments
                    WHERE experiment_id = $1::uuid AND subject_id = $2
                    """,
                    exp["id"],
                    str(subject_id),
                )
                return str(existing["variant_key"]) if existing else variant_key
            return str(inserted["variant_key"])

    async def record_outcome(
        self,
        *,
        experiment_key: str,
        subject_id: str,
        metrics: dict[str, Any],
    ) -> None:
        """Merge ``metrics`` into the assignment row's ``metrics`` column.

        Uses JSONB ``||`` so multiple calls (from different pipeline
        phases) accumulate — the second call's keys overwrite the
        first's, and net-new keys are added. No-op + WARNING if the
        assignment row doesn't exist; programmer errors (non-dict
        metrics, missing experiment) raise.
        """
        if not isinstance(metrics, dict):
            raise ValueError(
                f"metrics must be a dict, got {type(metrics).__name__}"
            )

        async with self._pool.acquire() as conn:
            updated = await conn.execute(
                """
                UPDATE experiment_assignments AS a
                   SET metrics = a.metrics || $3::jsonb
                  FROM experiments AS e
                 WHERE a.experiment_id = e.id
                   AND e.key = $1
                   AND a.subject_id = $2
                """,
                experiment_key,
                str(subject_id),
                json.dumps(metrics),
            )
            # asyncpg returns "UPDATE <n>"; parse the count to log a
            # useful warning when there's no matching assignment.
            try:
                count = int(updated.split()[-1])
            except (AttributeError, ValueError, IndexError):
                count = -1
            if count == 0:
                logger.warning(
                    "[EXPERIMENTS] record_outcome(): no assignment for "
                    "experiment=%r subject=%r — metrics dropped",
                    experiment_key,
                    subject_id,
                )

    async def summary(self, experiment_key: str) -> dict[str, Any]:
        """Per-variant counts + averages of every numeric metric.

        Shape::

            {
                "variant_key": {
                    "n": int,
                    "metrics": {"score_avg": float, "duration_ms_avg": float, ...},
                },
                ...
            }

        Numeric averages aggregate every ``metrics`` JSONB key whose
        value is a number (``int`` or ``float``). String / bool / nested
        values are skipped — the harness intentionally limits itself
        to plumbing; richer rollups belong in a follow-up.
        """
        async with self._pool.acquire() as conn:
            exp = await conn.fetchrow(
                "SELECT id::text AS id FROM experiments WHERE key = $1",
                experiment_key,
            )
            if exp is None:
                logger.warning(
                    "[EXPERIMENTS] summary(): unknown experiment %r",
                    experiment_key,
                )
                return {}
            rows = await conn.fetch(
                """
                SELECT variant_key, metrics
                  FROM experiment_assignments
                 WHERE experiment_id = $1::uuid
                """,
                exp["id"],
            )

        result: dict[str, dict[str, Any]] = {}
        # Per-variant tally: {variant: {metric: [count, sum]}}
        accum: dict[str, dict[str, list[float]]] = {}
        for row in rows:
            variant = str(row["variant_key"])
            metrics = self._decode_jsonb(row["metrics"]) or {}

            bucket = result.setdefault(variant, {"n": 0, "metrics": {}})
            bucket["n"] += 1

            metric_acc = accum.setdefault(variant, {})
            for k, v in metrics.items():
                # bool is a subclass of int — skip explicitly so True/False
                # outcomes don't get averaged as 0.5.
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    state = metric_acc.setdefault(k, [0.0, 0.0])
                    state[0] += 1
                    state[1] += float(v)

        for variant, metric_acc in accum.items():
            avgs = result[variant]["metrics"]
            for metric, (count, total) in metric_acc.items():
                if count > 0:
                    avgs[f"{metric}_avg"] = total / count
        return result

    async def conclude(
        self,
        *,
        experiment_key: str,
        winner_variant: str,
    ) -> None:
        """Mark an experiment ``complete`` with the winning variant.

        No auto-promotion — the operator promotes the winning config
        into production app_settings manually so wins can be reviewed.
        Raises ``ValueError`` if the experiment doesn't exist or the
        winner isn't a declared variant.
        """
        async with self._pool.acquire() as conn:
            exp = await conn.fetchrow(
                """
                SELECT id::text AS id, status, variants
                FROM experiments
                WHERE key = $1
                """,
                experiment_key,
            )
            if exp is None:
                raise ValueError(
                    f"unknown experiment {experiment_key!r}"
                )
            variants = self._decode_variants(exp["variants"])
            valid_keys = {v["key"] for v in variants}
            if winner_variant not in valid_keys:
                raise ValueError(
                    f"winner_variant {winner_variant!r} is not one of the "
                    f"declared variants {sorted(valid_keys)}"
                )
            await conn.execute(
                """
                UPDATE experiments
                   SET status = 'complete',
                       completed_at = NOW(),
                       winner_variant = $2
                 WHERE key = $1
                """,
                experiment_key,
                winner_variant,
            )
        logger.info(
            "[EXPERIMENTS] concluded %r winner=%s",
            experiment_key,
            winner_variant,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_jsonb(value: Any) -> Any:
        """Some asyncpg builds return jsonb as ``str``, others as the
        decoded structure. Normalize."""
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                logger.warning(
                    "[EXPERIMENTS] failed to decode jsonb str — treating as empty"
                )
                return None
        return value

    @classmethod
    def _decode_variants(cls, value: Any) -> list[dict[str, Any]]:
        decoded = cls._decode_jsonb(value)
        if not isinstance(decoded, list):
            return []
        return [dict(v) for v in decoded if isinstance(v, dict)]

    @classmethod
    def _row_to_dict(cls, row: Any) -> dict[str, Any]:
        out = dict(row)
        if "variants" in out:
            out["variants"] = cls._decode_variants(out["variants"])
        return out


__all__ = ["ExperimentService"]
