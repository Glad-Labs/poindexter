"""Filter parser + SQL builder for ``poindexter post approve --filter ...``.

Glad-Labs/poindexter#338 — bulk-approval bullet of the gate-system polish
issue. Bulk approval is a thin UX shortcut that resolves a
strict-allowlist filter to a list of post ids, then re-uses the
single-post ``approve_gate`` service call once per match. Same audit
log, same dispatcher webhooks, same gate-history rows — the bulk path
is identical to N single ``poindexter post approve`` invocations.

This module owns the *resolution* step: parse the operator's
``--filter`` string into parameterised SQL, refusing anything outside
the column allowlist. Strict whitelist + parameterised values keep us
on the right side of bandit (B608) and Matt's no-arbitrary-SQL rule.

Grammar (handwritten parser — no lark / sqlparse dependency):

    filter   ::= clause ("AND" clause)*
    clause   ::= state_clause
               | created_clause
               | niche_clause
               | author_clause
               | gate_clause
    state_clause   ::= "state="          IDENT
    created_clause ::= "created_after="  ISO8601
                     | "created_before=" ISO8601
    niche_clause   ::= "niche="          IDENT
    author_clause  ::= "author="         IDENT
    gate_clause    ::= "gate_kind="      IDENT

Whitespace around AND / equals is forgiven. Identifiers are
``[A-Za-z0-9_-]+`` (no quotes, no commas, no SQL meta-characters).
ISO8601 values must parse via ``datetime.fromisoformat`` — the trailing
``Z`` shorthand is rewritten to ``+00:00`` so the stdlib accepts it.

Semantic mapping (operator-friendly, not literal column names):

- ``state=<gate_name>`` ➜ post has a pending gate with ``gate_name = $N``.
  This is the natural reading of "awaiting_<gate_name>" — e.g.
  ``--filter state=draft`` matches every post currently waiting on the
  draft gate. The literal value is restricted to canonical gate names.
- ``gate_kind=<gate_name>`` ➜ alias of ``state=`` (kept for spec clarity
  and future-proofing if ``state`` ever picks up a second meaning).
- ``created_after=<iso>`` / ``created_before=<iso>`` ➜
  ``posts.created_at`` half-open interval bounds.
- ``niche=<slug>`` ➜ ``EXISTS (SELECT 1 FROM content_tasks
  WHERE post_id = posts.id AND niche_slug = $N)``. Posts have no
  direct ``niche`` column; the link is via the originating task row.
- ``author=<value>`` ➜ ``posts.created_by = $N`` (TEXT column).

The output is always a tuple ``(where_sql, params)`` where ``where_sql``
contains placeholders ``$1, $2, ...`` and ``params`` is the matching
argument list to splat into ``conn.fetch(...)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Imported lazily inside ``parse_filter`` to dodge the heavy
# ``services.*`` import path for callers that only want the dataclass
# (e.g. unit tests inspecting the parser in isolation).
_CANONICAL_GATE_NAMES_FALLBACK: tuple[str, ...] = (
    "topic", "draft", "podcast", "video", "short", "final",
    "media_generation_failed",
)


_IDENT_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
# Splits on the literal token ``AND`` (case-insensitive, surrounded by
# whitespace). Doing this with a regex rather than ``str.split`` keeps
# us from chopping a value that happens to contain the substring "and"
# (e.g. ``author=brandon``).
_AND_SPLIT_RE = re.compile(r"\s+AND\s+", re.IGNORECASE)


class FilterParseError(ValueError):
    """Raised when an operator-supplied --filter is malformed.

    The CLI catches this and exits with code 2 so scripts can
    distinguish "you typed it wrong" from "the worker failed".
    """


@dataclass(frozen=True)
class ParsedFilter:
    """Result of :func:`parse_filter`.

    - ``where_sql`` contains placeholders ``$1, $2, ...`` ready to
      splice into ``SELECT ... FROM posts WHERE {where_sql}``.
    - ``params`` is the matching argument list.
    - ``clauses`` is a human-readable list of ``"key=value"`` pairs in
      the order they were parsed; useful for the dry-run summary.
    """

    where_sql: str
    params: list[Any]
    clauses: list[tuple[str, str]]


def _parse_iso8601(raw: str) -> datetime:
    """Parse an ISO8601 timestamp, normalising trailing ``Z`` to UTC.

    ``datetime.fromisoformat`` only learned to accept ``Z`` in 3.11;
    rewrite for cross-version safety. Naive timestamps are interpreted
    as UTC — operators rarely tag wall-clock filters with a local TZ.
    """
    candidate = raw.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError as e:
        raise FilterParseError(
            f"invalid ISO8601 timestamp {raw!r}: {e}"
        ) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _validate_ident(key: str, value: str) -> str:
    """Reject values containing SQL-meta or whitespace characters."""
    if not _IDENT_RE.match(value):
        raise FilterParseError(
            f"value for {key!r} must match [A-Za-z0-9_-]+ "
            f"(got {value!r})"
        )
    return value


def _canonical_gate_names() -> tuple[str, ...]:
    """Return the live ``CANONICAL_GATE_NAMES`` tuple, or a static
    fallback if the import path can't be resolved (e.g. parser
    imported from a script outside the package).
    """
    try:
        from services.gates.post_approval_gates import (  # noqa: WPS433
            CANONICAL_GATE_NAMES,
        )
        return CANONICAL_GATE_NAMES
    except Exception:  # noqa: BLE001
        return _CANONICAL_GATE_NAMES_FALLBACK


def parse_filter(raw_filter: str) -> ParsedFilter:
    """Parse the operator's ``--filter`` string into safe SQL.

    Empty input raises :class:`FilterParseError` — bulk mode without a
    predicate would be "approve everything pending", which is a foot-gun
    even with the dry-run guard. Force the operator to be explicit.
    """
    if raw_filter is None or not raw_filter.strip():
        raise FilterParseError(
            "filter is empty — pass at least one clause "
            "(e.g. state=draft AND created_after=2026-05-01T00:00:00Z)"
        )

    raw_clauses = [c.strip() for c in _AND_SPLIT_RE.split(raw_filter) if c.strip()]
    if not raw_clauses:
        raise FilterParseError("filter parsed to zero clauses")

    conditions: list[str] = []
    params: list[Any] = []
    clauses: list[tuple[str, str]] = []
    canonical_gates = _canonical_gate_names()

    for raw_clause in raw_clauses:
        # Each clause is ``key=value`` — exactly one ``=`` allowed,
        # because the value is a strict identifier or an ISO timestamp.
        if "=" not in raw_clause:
            raise FilterParseError(
                f"clause {raw_clause!r} is not key=value"
            )
        key, _, value = raw_clause.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if not value:
            raise FilterParseError(
                f"clause {raw_clause!r} has empty value"
            )

        if key == "state" or key == "gate_kind":
            gate_name = _validate_ident(key, value)
            if gate_name not in canonical_gates:
                raise FilterParseError(
                    f"{key}={gate_name!r} is not a canonical gate name "
                    f"(allowed: {', '.join(canonical_gates)})"
                )
            params.append(gate_name)
            _gate_cond = (
                "EXISTS (SELECT 1 FROM post_approval_gates g "
                "WHERE g.post_id = posts.id "
                "AND g.state = 'pending' "
                f"AND g.gate_name = ${len(params)})"  # nosec B608  # whitelist column; placeholder only
            )
            conditions.append(_gate_cond)
            clauses.append((key, gate_name))
        elif key == "created_after":
            dt = _parse_iso8601(value)
            params.append(dt)
            # nosec B608  # whitelist-only column names; values parameterized
            conditions.append(f"posts.created_at > ${len(params)}")
            clauses.append((key, value))
        elif key == "created_before":
            dt = _parse_iso8601(value)
            params.append(dt)
            # nosec B608  # whitelist-only column names; values parameterized
            conditions.append(f"posts.created_at < ${len(params)}")
            clauses.append((key, value))
        elif key == "niche":
            slug = _validate_ident(key, value)
            params.append(slug)
            _niche_cond = (
                "EXISTS (SELECT 1 FROM content_tasks ct "
                "WHERE ct.post_id = posts.id "
                f"AND ct.niche_slug = ${len(params)})"  # nosec B608  # whitelist column; placeholder only
            )
            conditions.append(_niche_cond)
            clauses.append((key, slug))
        elif key == "author":
            ident = _validate_ident(key, value)
            params.append(ident)
            # nosec B608  # whitelist-only column names; values parameterized
            conditions.append(f"posts.created_by = ${len(params)}")
            clauses.append((key, ident))
        else:
            raise FilterParseError(
                f"unknown filter column {key!r}. "
                f"Allowed: state, created_after, created_before, "
                f"niche, author, gate_kind."
            )

    # Reminder for the next bandit run: every interpolation in this
    # builder is from the static allowlist (the column names + the
    # gate-name set are baked in here). Values are bound via $N.
    where_sql = " AND ".join(conditions)  # nosec B608  # whitelist-only column names; values parameterized
    return ParsedFilter(where_sql=where_sql, params=params, clauses=clauses)
