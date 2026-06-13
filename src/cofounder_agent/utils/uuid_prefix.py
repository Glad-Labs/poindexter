"""Server-side UUID-prefix resolution for FastAPI route handlers.

Operator surfaces — Grafana panels, ``poindexter tasks list``, the
awaiting-approval queues — render ids as 8-char prefixes
(``LEFT(<id>::text, 8)``). Operators paste that prefix back into
``poindexter tasks ...`` / ``poindexter posts ...``, which mutate through the
worker HTTP API (``WorkerClient``) rather than opening a DB pool. The route
handlers behind those calls exact-match a UUID column, so a bare prefix either:

* trips asyncpg's client-side UUID validation — ``posts.id`` is a real ``uuid``
  column, so ``WHERE id = $1`` with ``'0bc9badd'`` raises
  ``invalid input ... uuid`` → a 500; or
* silently resolves to whichever row sorts first — ``pipeline_tasks`` did a
  naive ``... LIKE $1 || '%' LIMIT 1`` in the approve/publish handlers, so an
  ambiguous prefix could approve/publish the *wrong* post.

This module is the single server-side resolver every mutate route delegates to:
the HTTP-layer twin of ``poindexter.cli._prefix.resolve_uuid_prefix``. Same
**exact-match-then-unique-LIKE-prefix** semantics, surfaced as HTTP errors:

* full-length dashed UUID  → returned unchanged, **no DB round trip**
* exactly one prefix match → the full id (``str``)
* zero matches             → :class:`fastapi.HTTPException` (404)
* many distinct matches    → :class:`fastapi.HTTPException` (409)

The comparison is ``<column>::text LIKE $1 || '%'`` — casting the column to text
keeps the bound parameter off the ``uuid`` type so a bare 8-char prefix never
trips asyncpg's client-side validation, regardless of whether the underlying
column is ``uuid`` (``posts.id``) or ``varchar`` (``pipeline_tasks.task_id``).

Design notes
------------

* ``table`` / ``column`` are interpolated into the SQL. They are
  **caller-supplied identifiers** (literal constants in route code), never
  operator input — the operator only supplies the bound ``value``. Do not pass
  user-controlled values as identifiers here.
* These routes are shared by the MCP server and the brain daemon. A full UUID is
  returned untouched with no DB round trip, so full-id callers keep identical
  behaviour — a genuinely missing full id still 404s at the caller's own fetch,
  not here.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

__all__ = [
    "looks_like_full_uuid",
    "resolve_task_id_prefix",
    "resolve_uuid_prefix",
]

# How many distinct candidates to pull before declaring a prefix ambiguous.
# One past the "unique" boundary is enough to decide, but a few extra make the
# disambiguation message actionable (operator sees real ids to pick from).
_CANDIDATE_LIMIT = 6

# The only characters a (possibly truncated) UUID can contain: hex digits and
# the dash separators. Anything else — a slug like ``nonexistent-task-id`` —
# can never prefix-match a real uuid column, so it short-circuits to a 404.
_UUID_PREFIX_CHARS = frozenset("0123456789abcdef-")


def looks_like_full_uuid(value: str) -> bool:
    """True when ``value`` is already a full 36-char dashed UUID.

    Callers use this to skip the DB round trip when the operator pasted a
    complete id. Deliberately shape-based (length + dash count) rather than a
    strict parse: a malformed-but-full-length string falls through to the
    caller's own exact-match, which fails loud the same way it always did.
    """
    return bool(value) and len(value) >= 36 and value.count("-") == 4


def _looks_like_uuid_prefix(value: str) -> bool:
    """True when ``value`` could be a *truncated* UUID — hex digits and dashes,
    shorter than a full id.

    Gates the DB round trip in :func:`resolve_uuid_prefix`: a value with any
    non-hex character (an operator typo, a slug like ``nonexistent-task-id``)
    can never ``LIKE``-prefix a real uuid column, so resolving it is wasted work
    that also trips bare mock pools in tests. Length is capped below a full UUID
    because the full-id case is already handled by :func:`looks_like_full_uuid`.
    """
    return bool(value) and len(value) < 36 and all(c in _UUID_PREFIX_CHARS for c in value.lower())


async def resolve_uuid_prefix(
    pool: Any,
    *,
    table: str,
    column: str,
    value: str,
    noun: str = "record",
) -> str:
    """Expand a short id prefix to the single full id it names.

    * full UUID in → returned unchanged, **no DB round trip**
    * exactly one match → the full id (``str``)
    * zero matches → :class:`fastapi.HTTPException` (404)
    * many distinct matches → :class:`fastapi.HTTPException` (409)

    ``pool`` is an asyncpg pool (or anything exposing ``acquire()`` →
    async-context-managed connection with ``fetch``). ``table``/``column`` are
    caller literals (see module docstring); ``value`` is bound as ``$1``.
    """
    if looks_like_full_uuid(value):
        return value

    if not _looks_like_uuid_prefix(value):
        # Non-hex chars (a slug, an operator typo) can't prefix a uuid column —
        # 404 here rather than running a guaranteed-empty LIKE against the DB.
        raise HTTPException(
            status_code=404,
            detail=f"{noun.capitalize()} not found for id '{value}'",
        )

    sql = (
        f"SELECT DISTINCT {column}::text AS id "  # noqa: S608 - identifiers are caller literals
        f"FROM {table} "
        f"WHERE {column}::text LIKE $1 || '%' "
        f"LIMIT {_CANDIDATE_LIMIT}"
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, value)

    ids = [row["id"] for row in rows]
    if not ids:
        raise HTTPException(
            status_code=404,
            detail=f"{noun.capitalize()} not found for id '{value}'",
        )
    if len(ids) > 1:
        shown = ", ".join(ids[:10])
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ambiguous id prefix '{value}' matches {len(ids)} {noun}s: "
                f"{shown}. Use the full id or a longer prefix."
            ),
        )
    return ids[0]


async def resolve_task_id_prefix(
    pool: Any,
    value: str,
    *,
    noun: str = "task",
) -> str:
    """Canonicalize a ``pipeline_tasks`` id taken from a URL path.

    Full UUIDs and legacy numeric ids pass straight through — the downstream
    ``DatabaseService.get_task`` resolves both (``task_id = $1 OR id::text =
    $1``), and numeric ids must stay numeric for the legacy ``id``-column write
    paths. Only a short prefix is expanded (to the one full
    ``pipeline_tasks.task_id`` it names, or 404/409).
    """
    if looks_like_full_uuid(value) or value.isdigit():
        return value
    return await resolve_uuid_prefix(
        pool,
        table="pipeline_tasks",
        column="task_id",
        value=value,
        noun=noun,
    )
