"""Generic CRUD over the declarative data-plane config tables (#1522).

Epic #1340 (transport-adapter contract): the service layer is the single
contract; the CLI / HTTP API / MCP servers are thin adapters that delegate
here. Before this module, the 5 declarative data-plane tables had **no
service** — the ``poindexter taps|retention|webhooks|qa-gates|publishers``
CLI groups hand-rolled raw SQL straight to the tables, and there was no HTTP
mirror. This module is the one place that owns that SQL.

**One generic service, keyed on a registry** (not 5 near-identical CRUD
modules — per #1522). Each :class:`SurfaceSpec` describes a table: its name,
its natural key column, the operator-mutable column whitelist (so an adapter
can never write telemetry columns like ``last_run_*`` / ``total_*`` /
``state``), and which of those columns are ``jsonb``. All SQL identifiers
come **only** from the registry (never from a request payload), so the
dynamic SQL can't be injected; every value is a bound parameter.

Read path mirrors the jsonb-as-text tolerance in
:mod:`services.qa_gates_db` (asyncpg may hand back ``jsonb`` as ``str``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class DataPlaneError(Exception):
    """Base class for declarative-config service errors."""


class UnknownSurfaceError(DataPlaneError):
    """Raised when a caller names a surface that isn't in the registry."""


class SurfaceValidationError(DataPlaneError):
    """Raised when an upsert payload is invalid (e.g. missing the key column,
    or a per-surface ``validate`` hook rejects it)."""


@dataclass(frozen=True)
class SurfaceSpec:
    """Describes one declarative data-plane table for the generic CRUD.

    Attributes:
        table: Physical table name (trusted SQL identifier).
        key_column: The UNIQUE natural key used for get/upsert/delete
            (``name`` for every current surface).
        mutable_columns: The columns an operator may set. Anything outside
            this tuple (``id``, ``created_at``/``updated_at``, and the
            ``last_run_*`` / ``total_*`` / ``state`` telemetry columns) is
            never writable through this service.
        json_columns: Subset of ``mutable_columns`` that are ``jsonb`` — their
            values are ``json.dumps``'d and bound with a ``::jsonb`` cast on
            write, and deserialized from text on read.
        validate: Optional per-surface hook that normalizes/validates a
            payload (returns the cleaned payload or raises
            :class:`SurfaceValidationError`).
    """

    table: str
    key_column: str
    mutable_columns: tuple[str, ...]
    json_columns: frozenset[str] = field(default_factory=frozenset)
    validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None


_SURFACES: dict[str, SurfaceSpec] = {
    "taps": SurfaceSpec(
        table="external_taps",
        key_column="name",
        mutable_columns=(
            "name", "handler_name", "tap_type", "target_table",
            "record_handler", "schedule", "config", "enabled",
            "metadata", "niche_id",
        ),
        json_columns=frozenset({"config", "metadata"}),
    ),
    "retention": SurfaceSpec(
        table="retention_policies",
        key_column="name",
        mutable_columns=(
            "name", "handler_name", "table_name", "filter_sql", "age_column",
            "ttl_days", "downsample_rule", "summarize_handler", "enabled",
            "config", "metadata",
        ),
        json_columns=frozenset({"downsample_rule", "config", "metadata"}),
    ),
    "webhooks": SurfaceSpec(
        table="webhook_endpoints",
        key_column="name",
        mutable_columns=(
            "name", "direction", "handler_name", "path", "url",
            "signing_algorithm", "secret_key_ref", "event_filter", "enabled",
            "config", "metadata",
        ),
        json_columns=frozenset({"event_filter", "config", "metadata"}),
    ),
    "publishers": SurfaceSpec(
        table="publishing_adapters",
        key_column="name",
        mutable_columns=(
            "name", "platform", "handler_name", "credentials_ref",
            "default_tags", "rate_limit_per_day", "enabled", "config",
            "metadata",
        ),
        json_columns=frozenset({"default_tags", "config", "metadata"}),
    ),
    "qa-gates": SurfaceSpec(
        table="qa_gates",
        key_column="name",
        mutable_columns=(
            "name", "stage_name", "execution_order", "reviewer",
            "required_to_pass", "enabled", "config", "metadata",
        ),
        json_columns=frozenset({"config", "metadata"}),
    ),
}


def resolve_surface(surface: str) -> SurfaceSpec:
    """Return the :class:`SurfaceSpec` for ``surface`` or raise.

    Raises:
        UnknownSurfaceError: if ``surface`` isn't a registered data-plane
            surface. Listing the known surfaces in the message keeps the
            error actionable for an operator.
    """
    try:
        return _SURFACES[surface]
    except KeyError as exc:
        known = ", ".join(sorted(_SURFACES))
        raise UnknownSurfaceError(
            f"unknown data-plane surface {surface!r}; known surfaces: {known}"
        ) from exc


def _row_to_dict(record: Any, spec: SurfaceSpec) -> dict[str, Any]:
    """Materialize an asyncpg record as a plain dict, deserializing any
    ``json_columns`` that came back as a text string (asyncpg/typecodec
    variance — same tolerance as :func:`services.qa_gates_db.load_qa_gate_chain`)."""
    row = dict(record)
    for col in spec.json_columns:
        value = row.get(col)
        if isinstance(value, str):
            try:
                row[col] = json.loads(value)
            except (ValueError, TypeError):
                row[col] = {}
    return row


async def list_rows(
    pool: Any, surface: str, *, filters: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Return every row of ``surface``, ordered by its key column.

    ``filters`` applies equality predicates, but **only** for keys that are
    real columns of the surface (per the registry whitelist) — unknown keys
    are silently dropped, never interpolated, so a caller cannot inject a
    predicate column. Values are always bound parameters.
    """
    spec = resolve_surface(surface)
    allowed = set(spec.mutable_columns)
    clauses: list[str] = []
    args: list[Any] = []
    if filters:
        for col, value in filters.items():
            if col in allowed:
                args.append(value)
                clauses.append(f"{col} = ${len(args)}")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    # nosec B608 — table/column identifiers come from the trusted _SURFACES
    # registry, never from caller input; values are bound params ($N).
    sql = f"SELECT * FROM {spec.table}{where} ORDER BY {spec.key_column}"  # nosec B608
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [_row_to_dict(r, spec) for r in rows]


async def get_row(pool: Any, surface: str, key: str) -> dict[str, Any] | None:
    """Return the single row whose key column equals ``key``, or ``None``."""
    spec = resolve_surface(surface)
    # nosec B608 — identifiers from the trusted registry; key is a bound param.
    sql = f"SELECT * FROM {spec.table} WHERE {spec.key_column} = $1"  # nosec B608
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, key)
    return _row_to_dict(row, spec) if row is not None else None


async def upsert_row(
    pool: Any, surface: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Insert or update one row of ``surface`` (keyed on its key column).

    Only columns in the surface's ``mutable_columns`` whitelist are written —
    telemetry columns (``last_run_*`` / ``total_*`` / ``state``) and any
    unknown payload keys are dropped, so an adapter cannot write them and an
    injection attempt via a payload *key* is inert (the key simply isn't a
    registered column). ``json_columns`` values are ``json.dumps``'d and bound
    with a ``::jsonb`` cast. ``updated_at`` is always bumped on conflict.

    Raises:
        SurfaceValidationError: if the payload omits the key column (or a
            per-surface ``validate`` hook rejects it).
    """
    spec = resolve_surface(surface)
    data = spec.validate(dict(payload)) if spec.validate else dict(payload)

    cols = [c for c in spec.mutable_columns if c in data]
    if spec.key_column not in cols:
        raise SurfaceValidationError(
            f"{surface!r} upsert requires the key column {spec.key_column!r}"
        )

    placeholders: list[str] = []
    args: list[Any] = []
    for idx, col in enumerate(cols, start=1):
        value = data[col]
        if col in spec.json_columns and not isinstance(value, str):
            value = json.dumps(value)
        args.append(value)
        placeholders.append(f"${idx}::jsonb" if col in spec.json_columns else f"${idx}")

    update_cols = [c for c in cols if c != spec.key_column]
    set_clause = ", ".join(
        [f"{c} = EXCLUDED.{c}" for c in update_cols] + ["updated_at = now()"]
    )
    columns_sql = ", ".join(cols)
    placeholders_sql = ", ".join(placeholders)
    sql = (
        f"INSERT INTO {spec.table} ({columns_sql}) "  # nosec B608 — identifiers from trusted registry; values are bound params
        f"VALUES ({placeholders_sql}) "
        f"ON CONFLICT ({spec.key_column}) DO UPDATE SET {set_clause} "
        "RETURNING *"
    )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    return _row_to_dict(row, spec)


async def delete_row(pool: Any, surface: str, key: str) -> bool:
    """Delete the row whose key column equals ``key``.

    Returns ``True`` if a row was deleted, ``False`` if none matched (so an
    adapter can map a miss to 404). Reads the asyncpg ``DELETE <n>`` command
    tag — same technique as the legacy ``cli/taps.py`` did inline.
    """
    spec = resolve_surface(surface)
    # nosec B608 — identifiers from the trusted registry; key is a bound param.
    sql = f"DELETE FROM {spec.table} WHERE {spec.key_column} = $1"  # nosec B608
    async with pool.acquire() as conn:
        result = await conn.execute(sql, key)
    return not str(result).endswith(" 0")


__all__ = [
    "SurfaceSpec",
    "DataPlaneError",
    "UnknownSurfaceError",
    "SurfaceValidationError",
    "resolve_surface",
    "list_rows",
    "get_row",
    "upsert_row",
    "delete_row",
]
