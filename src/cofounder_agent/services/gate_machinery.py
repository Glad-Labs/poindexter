"""Shared HITL approval-gate machinery (Glad-Labs/poindexter#622).

:mod:`services.approval_service` (mid-pipeline gates on ``pipeline_tasks``)
and :mod:`services.posts_approval_service` (the final-publish gate on
``posts``) are deliberately split one-table-per-file — each owns its own
SQL so a UUID can never flip the wrong table's status. But the *non-SQL*
gate logic was copy-pasted across both: artifact coercion (byte-for-byte
identical), the not-paused / wrong-gate validation, the per-gate reject-status
lookup, and the timestamp ISO-coercion. A fix to any of those had to be
applied twice and would silently diverge.

This module is the single home for that shared logic. Both services keep
their entity-specific SQL and notification formatting; they import the pieces
below and parameterize them on entity kind (label + exception types). The
table-specific ``_fetch_*_row`` / ``pause_*`` SQL intentionally stays per
service per that module's stated design.
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# Exceptions — shared root so a caller can ``except GateServiceError`` and
# catch a failure from either the task-entity or post-entity service. Each
# service keeps its own named subclasses (TaskNotFoundError / PostNotFoundError
# etc.) for precise handling; they derive from their service base, which
# derives from this root.
# ---------------------------------------------------------------------------


class GateServiceError(Exception):
    """Base class for every approval-gate service failure (task or post)."""


# ---------------------------------------------------------------------------
# Artifact coercion
# ---------------------------------------------------------------------------


def coerce_artifact(raw: Any) -> dict[str, Any]:
    """Normalize a ``gate_artifact`` column value to a dict.

    ``gate_artifact`` round-trips from asyncpg as a JSON *string* unless an
    automatic JSONB codec is registered, so parse defensively: dicts pass
    through untouched, JSON strings are parsed (non-dict JSON and unparseable
    text are wrapped under ``{"raw": ...}``), and anything else is stringified
    under ``raw`` so arbitrary types never leak upstream.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"raw": parsed}
        except (ValueError, TypeError):
            return {"raw": raw}
    # Anything else — wrap so we don't leak arbitrary types upstream.
    return {"raw": str(raw)}


# ---------------------------------------------------------------------------
# Gate-match validation
# ---------------------------------------------------------------------------


def ensure_gate_match(
    row: dict[str, Any],
    gate_name: str | None,
    *,
    entity_label: str,
    entity_id: str,
    not_paused_exc: type[Exception],
    mismatch_exc: type[Exception],
    verb: str,
) -> str:
    """Validate a fetched row is paused at the expected gate; return its gate.

    Shared by both services' ``approve`` / ``reject`` (and the not-paused half
    by ``show_*``). The caller has already handled the not-found case (row is
    non-None here). Raises ``not_paused_exc`` when no gate is active, and
    ``mismatch_exc`` when ``gate_name`` is supplied but doesn't match the
    active gate — never silently flips the wrong gate (no-silent-fallback).

    Args:
        row: The fetched entity row (must contain ``awaiting_gate`` / ``status``).
        gate_name: Operator-asserted gate, or None for "any active gate".
        entity_label: Human label for messages, e.g. ``"Task"`` / ``"Post"``.
        entity_id: The id for messages.
        not_paused_exc: Exception type raised when no gate is active.
        mismatch_exc: Exception type raised on gate-name mismatch.
        verb: Action word for the mismatch message (``"approve"`` / ``"reject"``).

    Returns:
        The active gate name.
    """
    active_gate = row.get("awaiting_gate")
    if not active_gate:
        raise not_paused_exc(
            f"{entity_label} {entity_id} is not paused at any gate "
            f"(current status={row.get('status')!r})"
        )
    if gate_name is not None and gate_name != active_gate:
        raise mismatch_exc(
            f"{entity_label} {entity_id} is paused at gate {active_gate!r}, "
            f"not {gate_name!r}. Refusing to {verb} the wrong gate."
        )
    return active_gate


# ---------------------------------------------------------------------------
# Reject-status resolution
# ---------------------------------------------------------------------------


def resolve_reject_status(site_config: Any, gate_name: str, default: str) -> str:
    """Resolve the status a rejected entity moves to.

    Per-gate override lives at ``app_settings.approval_gate_<gate>_reject_status``
    (e.g. set the final-publish gate's to ``"draft"`` to bounce rejects back
    into the draft pool). Falls back to ``default`` when unset or no config.
    """
    if site_config is not None:
        custom = site_config.get(f"approval_gate_{gate_name}_reject_status", "")
        if custom:
            return str(custom).strip()
    return default


# ---------------------------------------------------------------------------
# Timestamp coercion
# ---------------------------------------------------------------------------


def iso_or_none(value: Any) -> Any:
    """Return ``value.isoformat()`` when it's a datetime-like, else ``value``.

    Keeps list/show payloads JSON-friendly for the CLI and MCP surfaces.
    """
    if value is not None and hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = [
    "GateServiceError",
    "coerce_artifact",
    "ensure_gate_match",
    "resolve_reject_status",
    "iso_or_none",
]
