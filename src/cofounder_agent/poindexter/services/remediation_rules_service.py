"""CRUD over the firefighter ``remediation_rules`` table.

The self-healing firefighter (``brain/remediation/``) matches an about-to-page
alert against enabled ``remediation_rules`` rows and runs an allowlisted action.
Those rows are **operational runtime state** — an operator adds, tunes, and
retires them one alert at a time as they learn which alerts are safely
auto-recoverable — so they are seeded by neither the public baseline nor the
operator overlay (see ``test_apply_does_not_seed_remediation_rules``); they are
managed live through this service.

This is the single contract behind the ``poindexter firefighter rule ...`` CLI
(epic #1340: the CLI is a thin adapter, the service owns the SQL). Unlike the 5
name-keyed declarative surfaces in :mod:`services.declarative_config_service`,
``remediation_rules`` is id-keyed with an *optional* partial-unique ``alertname``
(and regex-only rules), so it gets its own small service rather than a generic
``SurfaceSpec``.
"""

from __future__ import annotations

import json
from typing import Any

# Firefighter action executors registered in ``brain/remediation/registry.py``.
# Prod code must not import the brain package (only tests may — the worker and
# brain are decoupled), so this is a local copy kept honest by
# ``test_known_actions_in_sync_with_brain_registry``: if the brain registry
# grows an executor, that test fails until this tuple is updated.
_KNOWN_ACTIONS: tuple[str, ...] = ("restart_container", "run_auto_remediate")

# Columns written on insert, in bind order.
_INSERT_COLUMNS = (
    "alertname", "match_regex", "action_name", "params", "enabled",
    "max_attempts_per_window", "window_minutes", "verify_after_seconds",
    "description",
)


class RemediationRuleError(Exception):
    """A rule payload/selector is invalid (unknown action, missing match, etc.)."""


def _row_to_dict(record: Any) -> dict[str, Any]:
    """Materialize an asyncpg record as a dict, deserializing ``params`` if the
    driver handed ``jsonb`` back as text (same tolerance as
    :mod:`services.declarative_config_service`)."""
    row = dict(record)
    params = row.get("params")
    if isinstance(params, str):
        try:
            row["params"] = json.loads(params)
        except (ValueError, TypeError):
            row["params"] = {}
    return row


def _selector(rule_id: int | None, alertname: str | None) -> tuple[str, Any]:
    """Resolve exactly one of ``rule_id`` / ``alertname`` to a (column, value)
    pair. The column is a trusted literal (``id`` or ``alertname``), never
    caller text, so it is safe to interpolate into the SQL."""
    if (rule_id is None) == (alertname is None):
        raise RemediationRuleError(
            "specify exactly one of rule_id or alertname to identify the rule"
        )
    return ("id", rule_id) if rule_id is not None else ("alertname", alertname)


async def list_rules(
    pool: Any, *, enabled: bool | None = None
) -> list[dict[str, Any]]:
    """Return every remediation rule ordered by id; optionally filter by
    ``enabled``."""
    args: list[Any] = []
    where = ""
    if enabled is not None:
        args.append(enabled)
        where = " WHERE enabled = $1"
    sql = f"SELECT * FROM remediation_rules{where} ORDER BY id"  # nosec B608 - where is one of two hardcoded literals ("" or " WHERE enabled = $1")
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [_row_to_dict(r) for r in rows]


async def get_rule(
    pool: Any, *, rule_id: int | None = None, alertname: str | None = None
) -> dict[str, Any] | None:
    """Return a single rule by id or alertname, or ``None`` if none matched."""
    col, value = _selector(rule_id, alertname)
    sql = f"SELECT * FROM remediation_rules WHERE {col} = $1"  # nosec B608 - col is "id" or "alertname" from _selector, never caller text
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, value)
    return _row_to_dict(row) if row is not None else None


async def add_rule(
    pool: Any,
    *,
    action_name: str,
    alertname: str | None = None,
    match_regex: str | None = None,
    params: dict[str, Any] | None = None,
    description: str = "",
    max_attempts_per_window: int | None = None,
    window_minutes: int | None = None,
    verify_after_seconds: int | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Insert a new remediation rule and return it.

    Validates (before any DB write) that ``action_name`` is a registered
    executor, that at least one of ``alertname`` / ``match_regex`` is present
    (the table's CHECK constraint), and that a ``restart_container`` rule names
    a ``container`` — a rule the brain can never run is a silent dead row, so it
    fails loud here instead.

    Raises:
        RemediationRuleError: on any of the above.
    """
    if action_name not in _KNOWN_ACTIONS:
        raise RemediationRuleError(
            f"unknown action_name {action_name!r}; known actions: "
            f"{', '.join(_KNOWN_ACTIONS)}"
        )
    if not alertname and not match_regex:
        raise RemediationRuleError(
            "a rule needs an alertname or a match_regex to match against"
        )
    params = dict(params or {})
    if action_name == "restart_container" and not params.get("container"):
        raise RemediationRuleError(
            "restart_container requires a 'container' param "
            "(e.g. --param container=poindexter-pyroscope)"
        )

    values = {
        "alertname": alertname,
        "match_regex": match_regex,
        "action_name": action_name,
        "params": json.dumps(params),
        "enabled": enabled,
        "max_attempts_per_window": max_attempts_per_window,
        "window_minutes": window_minutes,
        "verify_after_seconds": verify_after_seconds,
        "description": description,
    }
    cols_sql = ", ".join(_INSERT_COLUMNS)
    placeholders = ", ".join(
        f"${i}::jsonb" if col == "params" else f"${i}"
        for i, col in enumerate(_INSERT_COLUMNS, start=1)
    )
    sql = (
        f"INSERT INTO remediation_rules ({cols_sql}) "  # nosec B608 - cols_sql/placeholders derive from the hardcoded _INSERT_COLUMNS tuple above; values are bind params
        f"VALUES ({placeholders}) RETURNING *"
    )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *(values[c] for c in _INSERT_COLUMNS))
    return _row_to_dict(row)


async def remove_rule(
    pool: Any, *, rule_id: int | None = None, alertname: str | None = None
) -> dict[str, Any] | None:
    """Delete a rule by id or alertname. Returns the deleted row, or ``None``
    if none matched (so an adapter can report a miss)."""
    col, value = _selector(rule_id, alertname)
    sql = f"DELETE FROM remediation_rules WHERE {col} = $1 RETURNING *"  # nosec B608 - col is "id" or "alertname" from _selector, never caller text
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, value)
    return _row_to_dict(row) if row is not None else None


async def set_rule_enabled(
    pool: Any,
    *,
    enabled: bool,
    rule_id: int | None = None,
    alertname: str | None = None,
) -> dict[str, Any] | None:
    """Enable/disable a rule by id or alertname without deleting it — the
    tune-in-place lever. Returns the updated row, or ``None`` if none matched.
    The brain's dispatch loop re-reads rules every cycle, so the change is live
    within one 30s cycle (no restart)."""
    col, value = _selector(rule_id, alertname)
    sql = (
        f"UPDATE remediation_rules SET enabled = $1, updated_at = now() "  # nosec B608 - col is "id" or "alertname" from _selector, never caller text
        f"WHERE {col} = $2 RETURNING *"
    )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, enabled, value)
    return _row_to_dict(row) if row is not None else None


__all__ = [
    "RemediationRuleError",
    "list_rules",
    "get_rule",
    "add_rule",
    "remove_rule",
    "set_rule_enabled",
]
